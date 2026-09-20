#!/usr/bin/env python3
"""LNG 수출·액화터미널 트래커 자동 수집기.

매일 daily-brief 워크플로에서 construction_news.py 다음에 실행된다.
Google News RSS에서 LNG FID/SPA/인허가 관련 보도를 모아 트래커 리스트
(docs/data/lng_projects.json)의 프로젝트와 이름 매칭한 뒤, Gemini로
프로젝트 확정·이벤트 분류·한국어 한 줄 요약을 만들어 해당 프로젝트의
log 배열에 누적한다. 구조화 필드(fid_date, spa, doe, ferc, status)는
자동으로 바꾸지 않는다 — 로그에 "⚑ FID 확정" 등으로 표시만 하고,
사람이(또는 Claude 세션이) 확인 후 갱신한다.

표준 라이브러리만 사용. GEMINI_API_KEY가 없으면 번역·분류 없이
제목 그대로 로그에 쌓는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "docs" / "data" / "lng_projects.json"

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
GEMINI_API_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

KST = timezone(timedelta(hours=9))

# 프로젝트명 매칭과 무관하게 항상 도는 주제 쿼리 (영문 위주)
THEME_QUERIES = [
    'LNG "final investment decision"',
    "LNG FID liquefaction export terminal",
    'LNG "sale and purchase agreement" OR "offtake agreement"',
    'LNG export FERC OR "Federal Energy Regulatory Commission" approval',
    'LNG export "Department of Energy" authorization',
    "LNG liquefaction FEED OR EPC contract award",
]

EVENT_ENUM = ["FID확정", "FID설", "SPA", "DOE", "FERC", "EPC/건설", "완공/가동", "기타"]

# 짧거나 흔한 단어라 오탐 위험이 큰 매칭 키 → 제외
BAD_KEYS = {"lng", "flng", "gato"}


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def http_get(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# ---------------------------------------------------------------------------
# 프로젝트명 → 매칭 키
# ---------------------------------------------------------------------------

def base_name(name: str) -> str:
    """행 이름에서 뉴스 헤드라인에 등장할 법한 기본 프로젝트명을 뽑는다.

    'Sabine Pass LNG Train 7' → 'Sabine Pass LNG',
    'Ksi Lisims (FLNG 1)' → 'Ksi Lisims', 'Argentina LNG Phase 1 (FLNG 2)' → 'Argentina LNG'
    """
    s = re.sub(r"\s*\([^)]*\)", "", name).strip()
    prev = None
    while prev != s:
        prev = s
        s = re.sub(
            r"\s+(?:Phase|Stage|Trains?|Expansion|Bolt-On)\s*[\w\-–&.]*$", "", s,
            flags=re.IGNORECASE,
        ).strip()
        s = re.sub(r"\s+\d[\d\-–]*$", "", s).strip()
    return s


def build_groups(projects: list[dict]) -> dict[str, dict]:
    """매칭 키(소문자 base name) → {'regex', 'project'(로그를 달 대표 행)}."""
    groups: dict[str, dict] = {}

    def register(key: str, p: dict, display: str) -> None:
        g = groups.setdefault(
            key,
            {
                "regex": re.compile(r"\b" + re.escape(key) + r"\b", re.IGNORECASE),
                "project": p,
                "display": display,
            },
        )
        # 워치리스트 행이 있으면 그 행을 대표로
        if p.get("watch") and not g["project"].get("watch"):
            g["project"] = p

    for p in projects:
        display = base_name(p["name"])
        key = display.lower()
        if not key or key in BAD_KEYS or len(key) < 5:
            continue
        register(key, p, display)
        # 헤드라인은 'LNG'를 생략하기도 한다('CP2 expansion', 'Sabine Pass Train 7').
        # 뒤의 ' lng'를 뗀 별칭도 등록하되, 남는 부분이 두 단어 이상이거나 숫자를
        # 포함해 충분히 고유할 때만 (단어 하나짜리 'texas'·'alaska' 등은 오탐 위험).
        if key.endswith(" lng"):
            alias = key[:-4].strip()
            has_digit = bool(re.search(r"\d", alias))
            if (
                alias and alias not in BAD_KEYS
                and len(alias) >= (3 if has_digit else 4)
                and (len(alias.split()) >= 2 or has_digit)
            ):
                register(alias, p, display)
    return groups


# ---------------------------------------------------------------------------
# 뉴스 수집
# ---------------------------------------------------------------------------

def build_rss_url(query: str) -> str:
    lookback = os.getenv("GOOGLE_NEWS_LOOKBACK", "2d")
    if lookback:
        query = f"{query} when:{lookback}"
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    return f"{GOOGLE_NEWS_RSS}?{urllib.parse.urlencode(params)}"


def fetch_feed(query: str) -> list[dict]:
    xml_bytes = http_get(build_rss_url(query))
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall("./channel/item"):
        source_el = item.find("source")
        items.append(
            {
                "id": item.findtext("guid", default="") or item.findtext("link", default=""),
                "title": item.findtext("title", default="(no title)"),
                "link": item.findtext("link", default=""),
                "pub_date": item.findtext("pubDate", default=""),
                "source": (source_el.text or "") if source_el is not None else "",
            }
        )
    return items


def collect_items(groups: dict[str, dict]) -> list[dict]:
    queries = list(THEME_QUERIES)
    # 워치리스트 프로젝트는 이름으로도 직접 검색
    seen_q = set()
    for g in groups.values():
        if g["project"].get("watch"):
            q = f'"{g["display"]}" LNG'
            if q not in seen_q:
                seen_q.add(q)
                queries.append(q)

    merged: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        try:
            feed_items = fetch_feed(query)
        except Exception as exc:  # 네트워크 실패는 그 쿼리만 건너뜀
            print(f"Feed fetch failed ({query!r}): {exc}", file=sys.stderr, flush=True)
            continue
        for item in feed_items:
            keys = [item["id"], item["title"].strip().lower()]
            if any(k in seen for k in keys):
                continue
            seen.update(keys)
            merged.append(item)
    return merged


def match_projects(item: dict, groups: dict[str, dict]) -> list[str]:
    title = item["title"]
    return [key for key, g in groups.items() if g["regex"].search(title)]


# ---------------------------------------------------------------------------
# Gemini 분류 (없으면 제목 그대로)
# ---------------------------------------------------------------------------

def ai_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def gemini_classify(candidates: list[dict]) -> dict[str, dict]:
    """id → {project_key, event, summary_ko, relevance}"""
    model = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
    system_text = (
        "너는 LNG 산업 리서치 어시스턴트다. 각 뉴스 헤드라인에 대해:\n"
        "1) project_key: 후보 프로젝트 키 중 기사가 실제로 다루는 것 하나를 고른다. "
        "동명이인/무관한 기사면 'none'.\n"
        "2) event: 기사 내용을 다음 중 하나로 분류 — "
        "FID확정(최종투자결정 발표), FID설(FID 목표/임박/지연 보도), SPA(장기공급·오프테이크 계약), "
        "DOE(미 에너지부 수출허가), FERC(미 FERC 인허가), EPC/건설(EPC·파이낸싱·건설), "
        "완공/가동, 기타.\n"
        "3) summary_ko: 한국어 한 문장 요약(계약이면 물량 MTPA·기간, 인허가면 단계 포함).\n"
        "4) relevance: LNG 수출/액화터미널 프로젝트 진행 상황으로서의 중요도 0~10. "
        "주가·ETF·일반 시황 기사는 3 이하."
    )
    body = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": [
            {"role": "user", "parts": [{"text": json.dumps(candidates, ensure_ascii=False)}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "results": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "id": {"type": "STRING"},
                                "project_key": {"type": "STRING"},
                                "event": {"type": "STRING", "enum": EVENT_ENUM + ["none"]},
                                "summary_ko": {"type": "STRING"},
                                "relevance": {"type": "INTEGER"},
                            },
                            "required": ["id", "project_key", "event", "summary_ko", "relevance"],
                        },
                    }
                },
                "required": ["results"],
            },
        },
    }
    max_attempts = int(os.getenv("GEMINI_MAX_RETRIES", "4"))
    base_delay = float(os.getenv("GEMINI_RETRY_BASE_SECONDS", "15"))
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            GEMINI_API_TEMPLATE.format(model=model),
            data=json.dumps(body).encode("utf-8"),
            headers={
                "x-goog-api-key": env("GEMINI_API_KEY"),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
            parts = []
            for candidate in payload.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    if isinstance(part.get("text"), str):
                        parts.append(part["text"])
            data = json.loads("\n".join(parts))
            return {r["id"]: r for r in data.get("results", [])}
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 429 and attempt < max_attempts:
                time.sleep(base_delay * (2 ** (attempt - 1)))
                continue
            raise RuntimeError(f"Gemini API error {exc.code}: {error_body or exc.reason}") from exc
    raise RuntimeError("Gemini retry loop exited unexpectedly")


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def item_hash(item: dict) -> str:
    return hashlib.sha1(item["title"].strip().lower().encode("utf-8")).hexdigest()[:16]


def main() -> int:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    projects = data["projects"]
    groups = build_groups(projects)

    seen = set(data["meta"].get("seen", []))
    for p in projects:  # 이미 로그에 있는 링크도 dedupe 대상
        for entry in p.get("log", []):
            if entry.get("link"):
                seen.add(hashlib.sha1(entry["link"].encode()).hexdigest()[:16])

    items = collect_items(groups)
    matched = []
    for item in items:
        h = item_hash(item)
        lh = hashlib.sha1(item["link"].encode()).hexdigest()[:16] if item["link"] else ""
        if h in seen or (lh and lh in seen):
            continue
        keys = match_projects(item, groups)
        if not keys:
            continue
        matched.append({"item": item, "keys": keys, "hash": h})

    max_score = int(os.getenv("LNG_MAX_ITEMS_TO_SCORE", "25"))
    matched = matched[:max_score]
    print(f"LNG tracker: {len(items)} fetched, {len(matched)} matched", flush=True)

    today = datetime.now(KST).strftime("%Y-%m-%d")
    min_rel = int(os.getenv("LNG_MIN_RELEVANCE", "5"))
    added = 0

    classified: dict[str, dict] = {}
    if matched and ai_available():
        candidates = [
            {
                "id": m["hash"],
                "title": m["item"]["title"],
                "source": m["item"]["source"],
                "candidate_project_keys": m["keys"],
            }
            for m in matched
        ]
        try:
            classified = gemini_classify(candidates)
        except Exception as exc:
            print(f"Gemini classify failed, falling back to raw titles: {exc}",
                  file=sys.stderr, flush=True)

    for m in matched:
        item, keys, h = m["item"], m["keys"], m["hash"]
        result = classified.get(h)
        if result:
            if result["project_key"] == "none" or result["event"] == "none":
                seen.add(h)
                continue
            if result["relevance"] < min_rel:
                seen.add(h)
                continue
            key = result["project_key"] if result["project_key"] in groups else keys[0]
            flag = "⚑ " if result["event"] == "FID확정" else ""
            text = f"{flag}[{result['event']}] {result['summary_ko']} ({item['source']})"
        else:
            key = keys[0]
            text = f"[뉴스] {item['title']} ({item['source']})"
        project = groups[key]["project"]
        project.setdefault("log", []).append(
            {"date": today, "text": text, "link": item["link"]}
        )
        seen.add(h)
        added += 1
        print(f"  + {project['name']}: {text[:80]}", flush=True)

    # seen 목록은 최근 600개까지만 보관
    data["meta"]["seen"] = list(seen)[-600:]
    if added:
        data["meta"]["updated"] = today
    DATA_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"LNG tracker: {added} log entries added", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
