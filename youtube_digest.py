# 유튜브 구독 채널 3일치 영상 링크 모음 — NotebookLM 오디오 소스용
#
# youtube_channels.json에 적어 둔 채널들의 최근 영상 링크만 모아
#   1) docs/youtube/<날짜>-links.txt  — 주소만 한 줄씩. NotebookLM 소스 창에 통째로 붙여넣는 용도
#   2) docs/youtube/<날짜>.md         — 채널·제목·날짜가 붙은 읽는 판
#   3) 텔레그램(@gs_analyst_bot)      — 같은 목록을 메시지로
# 로 내보낸다.
#
# 수집은 매일, 발송은 3일에 한 번이다. 유튜브 피드는 최근 15개만 들고 있어서
# 3일에 한 번만 긁으면 그 사이에 많이 올라온 채널은 앞부분이 잘려 나간다.
# 그래서 매일 돌며 새 영상을 상태 파일의 pending 버퍼에 쌓아 두고, 간격이 차면
# 버퍼를 통째로 비워 내보낸다(--force로 지금 비우기).
#
# 건설기계 브리프(construction_news.py)와 같은 원칙: 표준 라이브러리만 쓴다.

import argparse
import html
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

KST = timezone(timedelta(hours=9), name="KST")
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "youtube_channels.json"
STATE_PATH = ROOT / "youtube_state.json"
OUT_DIR = ROOT / "docs" / "youtube"

FEED_URL = "https://www.youtube.com/feeds/videos.xml"
# 채널 피드(channel_id=UC…)는 쇼츠를 빼고 주는 때가 있다. 업로드 재생목록
# (playlist_id=UU… — 채널 ID의 UC를 UU로 바꾼 것)은 쇼츠까지 담아 준다.
# 어느 쪽이 뭘 빠뜨리든 둘 다 긁어 합치면 놓치지 않는다.
FEED_PARAMS = ("channel_id", "playlist_id")
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"

# 채널 ID는 22자 base64url. 페이지 HTML/피드 어디서 긁어도 이 모양이다.
CHANNEL_ID_RE = re.compile(r"(UC[A-Za-z0-9_-]{22})")
# 발송 이력은 무한정 쌓을 필요가 없다 — 이 기간이 지난 영상 ID는 버린다.
SEEN_RETENTION_DAYS = 120
# 러너가 며칠 죽어 있었어도 창을 무한정 늘리지는 않는다.
MAX_LOOKBACK_DAYS = 14

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# 공통
# ---------------------------------------------------------------------------

def log(message: str) -> None:
    print(message, flush=True)


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def http_get(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    """유튜브는 러너 IP를 간헐적으로 막는다 — 429/5xx는 쉬었다 다시 친다."""
    last_error: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        if attempt < retries - 1:
            time.sleep(3 * (attempt + 1))
    raise last_error if last_error else RuntimeError(f"GET 실패: {url}")


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path.name} 을 읽을 수 없습니다: {exc}") from exc
    return loaded if isinstance(loaded, dict) else dict(default)


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 채널 해결 — handle/url → 채널 ID
# ---------------------------------------------------------------------------

def candidate_urls(entry: dict) -> list[str]:
    """설정 한 줄에서 채널 ID를 찾아볼 주소 후보를 만든다."""
    urls: list[str] = []
    raw_url = (entry.get("url") or "").strip()
    if raw_url:
        urls.append(raw_url)

    handle = (entry.get("handle") or "").strip()
    if not handle and not raw_url:
        handle = (entry.get("name") or "").strip()
    if handle:
        bare = handle.lstrip("@")
        quoted = urllib.parse.quote(bare, safe="")
        # @핸들이 정석이고, 옛 채널은 /c/ 나 /user/ 에만 남아 있기도 하다.
        urls.append(f"https://www.youtube.com/@{quoted}")
        urls.append(f"https://www.youtube.com/c/{quoted}")
        urls.append(f"https://www.youtube.com/user/{quoted}")

    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


def extract_channel_id(page: str) -> str | None:
    for pattern in (
        r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"',
        r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"',
        r'channel_id=(UC[A-Za-z0-9_-]{22})',
        r'youtube\.com/channel/(UC[A-Za-z0-9_-]{22})',
    ):
        found = re.search(pattern, page)
        if found:
            return found.group(1)
    return None


def resolve_channel_id(entry: dict, state: dict) -> str | None:
    """설정 → 채널 ID. 한 번 찾으면 상태 파일에 캐시해 두고 다시 안 찾는다."""
    # channel_id를 줬거나, url이 이미 .../channel/UC… 꼴이면 요청 없이 끝난다.
    # (유튜브 카드에서 복사한 주소가 대개 이 꼴이다)
    for raw in (entry.get("channel_id"), entry.get("url")):
        text = (raw or "").strip()
        if not text:
            continue
        found = CHANNEL_ID_RE.search(text)
        if found:
            return found.group(1)
    if (entry.get("channel_id") or "").strip():
        log(f"  ! channel_id 형식이 이상합니다: {entry['channel_id']}")

    cache = state.setdefault("resolved", {})
    key = (
        (entry.get("handle") or entry.get("url") or entry.get("name") or "")
        .strip()
        .lower()
    )
    cached = cache.get(key)
    if isinstance(cached, dict) and cached.get("channel_id"):
        return cached["channel_id"]

    for url in candidate_urls(entry):
        try:
            page = http_get(url, timeout=20, retries=2).decode("utf-8", "replace")
        except Exception as exc:  # 404·차단 모두 여기로 — 다음 후보로 넘어간다
            log(f"  · {url} → {exc}")
            continue
        channel_id = extract_channel_id(page)
        if channel_id:
            cache[key] = {
                "channel_id": channel_id,
                "source_url": url,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            }
            log(f"  · 채널 ID 확인: {channel_id} ({url})")
            return channel_id
    return None


# ---------------------------------------------------------------------------
# 피드 수집
# ---------------------------------------------------------------------------

def parse_feed(raw: bytes) -> tuple[str, list[dict]]:
    """유튜브 채널 Atom 피드 → (채널명, 영상 목록). 최근 15개까지만 들어 있다."""
    root = ET.fromstring(raw)
    channel_title = (root.findtext(f"{ATOM}title") or "").strip()

    videos: list[dict] = []
    for item in root.findall(f"{ATOM}entry"):
        video_id = (item.findtext(f"{YT}videoId") or "").strip()
        if not video_id:
            continue
        published_raw = (item.findtext(f"{ATOM}published") or "").strip()
        try:
            published = datetime.fromisoformat(published_raw)
        except ValueError:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)

        group = item.find(f"{MEDIA}group")
        description = ""
        if group is not None:
            description = (group.findtext(f"{MEDIA}description") or "").strip()

        videos.append(
            {
                "video_id": video_id,
                "title": (item.findtext(f"{ATOM}title") or "").strip(),
                "published": published,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "description": description,
            }
        )
    videos.sort(key=lambda v: v["published"], reverse=True)
    return channel_title, videos


def uploads_playlist_id(channel_id: str) -> str:
    """UCxxxx → UUxxxx. 그 채널의 '업로드' 재생목록 ID다."""
    return "UU" + channel_id[2:]


def merge_videos(*batches: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for batch in batches:
        for video in batch:
            merged.setdefault(video["video_id"], video)
    return sorted(merged.values(), key=lambda v: v["published"], reverse=True)


def fetch_channel_videos(channel_id: str) -> tuple[str, list[dict]]:
    """채널 피드 + 업로드 재생목록 피드를 합쳐 쇼츠까지 훑는다."""
    channel_title = ""
    batches: list[list[dict]] = []
    errors: list[str] = []
    for param in FEED_PARAMS:
        value = (
            channel_id if param == "channel_id" else uploads_playlist_id(channel_id)
        )
        url = f"{FEED_URL}?{urllib.parse.urlencode({param: value})}"
        try:
            title, videos = parse_feed(http_get(url))
        except Exception as exc:
            errors.append(f"{param}={value}: {exc}")
            continue
        channel_title = channel_title or title
        batches.append(videos)
    if not batches:
        raise RuntimeError(" / ".join(errors))
    for message in errors:  # 한쪽만 실패하면 나머지로 계속 간다
        log(f"  · 피드 한쪽 실패 (계속 진행): {message}")
    return channel_title, merge_videos(*batches)


def title_passes(title: str, entry: dict) -> bool:
    match = entry.get("match")
    if match and not re.search(match, title, re.IGNORECASE):
        return False
    exclude = entry.get("exclude")
    if exclude and re.search(exclude, title, re.IGNORECASE):
        return False
    return True


def select_videos(
    videos: list[dict], entry: dict, since: datetime, seen: dict, limit: int
) -> list[dict]:
    picked = []
    for video in videos:
        if video["published"] < since:
            continue
        if video["video_id"] in seen:
            continue
        if not title_passes(video["title"], entry):
            continue
        picked.append(video)
        if len(picked) >= limit:
            break
    return picked


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def render_links(groups: list[dict]) -> str:
    """NotebookLM 소스 창에 통째로 붙여넣는 용도 — 주소만 한 줄씩."""
    return "\n".join(v["url"] for group in groups for v in group["videos"]) + "\n"


def render_markdown(groups: list[dict], since: datetime, until: datetime) -> str:
    total = sum(len(g["videos"]) for g in groups)
    lines = [
        f"# 유튜브 구독 채널 모음 {until.astimezone(KST):%Y-%m-%d}",
        "",
        f"- 대상 구간: {since.astimezone(KST):%Y-%m-%d %H:%M} ~ "
        f"{until.astimezone(KST):%Y-%m-%d %H:%M} (KST)",
        f"- 채널 {len(groups)}개 · 영상 {total}건",
        "- NotebookLM 소스로 넣을 때는 같은 날짜의 `-links.txt`를 열어 주소만 복사하세요.",
        "",
    ]
    for group in groups:
        lines.append(f"## {group['name']}")
        lines.append("")
        for video in group["videos"]:
            stamp = video["published"].astimezone(KST).strftime("%m-%d %H:%M")
            lines.append(f"- [{video['title']}]({video['url']}) · {stamp}")
        lines.append("")

    lines.append("## 주소만 (복사용)")
    lines.append("")
    lines.append("```")
    lines.append(render_links(groups).rstrip("\n"))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def write_outputs(groups: list[dict], since: datetime, until: datetime) -> dict:
    stamp = until.astimezone(KST).strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    markdown = render_markdown(groups, since, until)
    links = render_links(groups)
    for name, body in (
        (f"{stamp}.md", markdown),
        (f"{stamp}-links.txt", links),
        ("latest.md", markdown),
        ("latest-links.txt", links),
    ):
        (OUT_DIR / name).write_text(body, encoding="utf-8")

    index_path = OUT_DIR / "index.json"
    index = load_json(index_path, {"digests": []})
    digests = [d for d in index.get("digests", []) if d.get("date") != stamp]
    digests.insert(
        0,
        {
            "date": stamp,
            "from": since.astimezone(KST).isoformat(),
            "to": until.astimezone(KST).isoformat(),
            "channels": len(groups),
            "videos": sum(len(g["videos"]) for g in groups),
            "markdown": f"{stamp}.md",
            "links": f"{stamp}-links.txt",
        },
    )
    save_json(index_path, {"digests": digests[:120]})
    return {"date": stamp, "markdown": f"{stamp}.md", "links": f"{stamp}-links.txt"}


# ---------------------------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------------------------

def telegram_credentials() -> tuple[str, str] | None:
    """@gs_analyst_bot 전용 값이 있으면 그걸, 없으면 리포 공용 값을 쓴다."""
    token = os.getenv("YT_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    chat_id = os.getenv("YT_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or ""
    if not token or not chat_id:
        return None
    return token, chat_id


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
            "parse_mode": "HTML",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def chunk_lines(lines: list[str], limit: int = 3500) -> list[str]:
    """텔레그램 한 통 4096자 제한 — 줄 단위로 끊어 담는다."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def build_telegram_lines(
    groups: list[dict], since: datetime, until: datetime, pages_url: str | None
) -> list[str]:
    total = sum(len(g["videos"]) for g in groups)
    lines = [
        "<b>📺 유튜브 구독 채널 3일 모음</b>",
        f"{since.astimezone(KST):%m-%d} ~ {until.astimezone(KST):%m-%d} (KST) · "
        f"채널 {len(groups)}개 · 영상 {total}건",
    ]
    if pages_url:
        lines.append(f'<a href="{html.escape(pages_url)}">주소만 모은 파일 (NotebookLM 붙여넣기용)</a>')
    lines.append("")
    for group in groups:
        lines.append(f"<b>{html.escape(group['name'])}</b>")
        for video in group["videos"]:
            stamp = video["published"].astimezone(KST).strftime("%m-%d")
            lines.append(f"{stamp} {html.escape(video['title'])}")
            lines.append(video["url"])
        lines.append("")
    return lines


def maybe_send_telegram(
    groups: list[dict], since: datetime, until: datetime, pages_url: str | None
) -> None:
    if os.getenv("ENABLE_YT_TELEGRAM", "true").lower() != "true":
        log("텔레그램 발송 꺼짐 (ENABLE_YT_TELEGRAM)")
        return
    credentials = telegram_credentials()
    if not credentials:
        log("텔레그램 토큰·챗 ID가 없어 발송을 건너뜁니다 "
            "(YT_TELEGRAM_BOT_TOKEN / YT_TELEGRAM_CHAT_ID)")
        return
    token, chat_id = credentials
    for chunk in chunk_lines(build_telegram_lines(groups, since, until, pages_url)):
        send_telegram_message(token, chat_id, chunk)
        time.sleep(1.1)
    log("텔레그램 발송 완료")


# ---------------------------------------------------------------------------
# 상태
# ---------------------------------------------------------------------------

def prune_seen(seen: dict, now: datetime) -> dict:
    cutoff = now - timedelta(days=SEEN_RETENTION_DAYS)
    kept = {}
    for video_id, stamp in seen.items():
        try:
            when = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= cutoff:
            kept[video_id] = stamp
    return kept


def window_start(state: dict, now: datetime, interval_days: int) -> datetime:
    """마지막 발송 이후를 창으로 잡되, 최소 간격·최대 소급은 지킨다."""
    last_raw = state.get("last_digest_at")
    if last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            floor = now - timedelta(days=MAX_LOOKBACK_DAYS)
            return max(min(last, now - timedelta(days=interval_days)), floor)
        except ValueError:
            pass
    return now - timedelta(days=interval_days)


def due(state: dict, now: datetime, interval_days: int) -> bool:
    last_raw = state.get("last_digest_at")
    if not last_raw:
        return True
    try:
        last = datetime.fromisoformat(last_raw)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    # 몇 시간 일찍 도는 것까지 막으면 러너 시각이 밀릴 때 하루를 통째로 건너뛴다.
    return now - last >= timedelta(days=interval_days) - timedelta(hours=6)


# ---------------------------------------------------------------------------
# pending 버퍼 — 매일 모아 두고 3일마다 비운다
#
# 유튜브 피드는 채널당 최근 15개만 준다. 3일에 한 번만 긁으면 그 사이에 15개를
# 넘겨 올린 채널은 앞부분이 잘린다. 매일 긁어 여기 쌓아 두면 그 일이 없다.
# ---------------------------------------------------------------------------

def buffer_videos(state: dict, groups: list[dict], now: datetime) -> int:
    pending = state.setdefault("pending", {})
    added = 0
    for group in groups:
        for video in group["videos"]:
            if video["video_id"] in pending:
                continue
            pending[video["video_id"]] = {
                "channel": group["name"],
                "order": group.get("order", 9999),
                "title": video["title"],
                "url": video["url"],
                "published": video["published"].isoformat(),
                "buffered_at": now.isoformat(),
            }
            added += 1
    return added


def groups_from_buffer(state: dict, config: dict) -> list[dict]:
    """버퍼를 채널별로 묶는다. 채널 순서는 설정 파일 순서를 따른다."""
    pending = state.get("pending", {})
    by_channel: dict[str, list[dict]] = {}
    channel_order: dict[str, int] = {}
    for video_id, row in pending.items():
        try:
            published = datetime.fromisoformat(row["published"])
        except (KeyError, TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        channel = row.get("channel") or "(채널 미상)"
        by_channel.setdefault(channel, []).append(
            {
                "video_id": video_id,
                "title": row.get("title", ""),
                "url": row.get("url", ""),
                "published": published,
            }
        )
        order = row.get("order", 9999)
        channel_order[channel] = min(channel_order.get(channel, order), order)

    order = [
        e.get("name")
        for e in (config or {}).get("channels", [])
        if isinstance(e, dict) and e.get("name")
    ]
    rank = {name: i for i, name in enumerate(order)}
    # 버퍼에 실어 둔 설정 순서가 1순위. 설정에 name을 적어 뒀으면 그 순서가 2순위
    # (옛 버퍼에는 order가 없다).
    def sort_key(n: str) -> tuple:
        return (channel_order.get(n, 9999), rank.get(n, len(rank)), n)

    groups = []
    for name in sorted(by_channel, key=sort_key):
        videos = sorted(by_channel[name], key=lambda v: v["published"], reverse=True)
        groups.append({"name": name, "videos": videos})
    return groups


# ---------------------------------------------------------------------------
# 점검 모드
# ---------------------------------------------------------------------------

def check_channels(config: dict, state: dict) -> int:
    """채널이 잡히는지, 피드에 뭐가 들어 있는지만 보고 끝낸다. 아무것도 쓰지 않는다."""
    failed = 0
    for entry in config.get("channels", []):
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        label = entry.get("name") or entry.get("url") or entry.get("handle") or "?"
        channel_id = resolve_channel_id(entry, state)
        if not channel_id:
            log(f"❌ {label} — 채널을 못 찾았습니다")
            failed += 1
            continue
        try:
            title, videos = fetch_channel_videos(channel_id)
        except Exception as exc:
            log(f"❌ {label} — 피드 실패: {exc}")
            failed += 1
            continue
        newest = videos[0]["published"].astimezone(KST) if videos else None
        log(f"✅ {title or label} ({channel_id}) — 피드 {len(videos)}건"
            + (f" · 최신 {newest:%Y-%m-%d %H:%M}" if newest else ""))
    if failed:
        log(f"\n{failed}개 채널이 안 잡혔습니다 — youtube_channels.json의 url을 확인하세요.")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def collect(
    config: dict, state: dict, since: datetime, limit: int, blocked: set
) -> list[dict]:
    groups: list[dict] = []
    for order, entry in enumerate(config.get("channels", [])):
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        name = entry.get("name") or entry.get("handle") or entry.get("url") or "(이름 없음)"
        log(f"- {name}")
        channel_id = resolve_channel_id(entry, state)
        if not channel_id:
            log("  ! 채널 해결 실패 — youtube_channels.json의 handle/url을 확인하세요")
            continue
        try:
            feed_title, videos = fetch_channel_videos(channel_id)
        except Exception as exc:
            log(f"  ! 피드 실패: {exc}")
            continue
        picked = select_videos(videos, entry, since, blocked, limit)
        log(f"  · 새 영상 {len(picked)}건 (피드 {len(videos)}건)")
        if picked:
            # name을 안 적었으면 유튜브가 주는 실제 채널명을 쓴다.
            groups.append(
                {
                    "name": entry.get("name") or feed_title or name,
                    "order": order,
                    "videos": picked,
                }
            )
    return groups


def main() -> int:
    load_dotenv(ROOT / ".env")
    cli = argparse.ArgumentParser(
        description="유튜브 구독 채널 3일치 영상 링크를 모아 텔레그램·파일로 낸다"
    )
    cli.add_argument("--force", action="store_true", help="간격이 안 찼어도 실행")
    cli.add_argument("--days", type=int, help="창 길이를 직접 지정 (기본 3일)")
    cli.add_argument("--dry-run", action="store_true",
                     help="출력만 보고 파일·상태·텔레그램은 건드리지 않음")
    cli.add_argument("--no-telegram", action="store_true", help="텔레그램만 건너뜀")
    cli.add_argument("--check", action="store_true",
                     help="채널이 제대로 잡히는지만 확인하고 끝낸다(아무것도 안 씀)")
    args = cli.parse_args()

    interval_days = args.days or int(os.getenv("YT_DIGEST_INTERVAL_DAYS", "3"))
    limit = int(os.getenv("YT_MAX_PER_CHANNEL", "20"))
    now = datetime.now(timezone.utc)

    config = load_json(CONFIG_PATH, {"channels": []})
    if not config.get("channels"):
        log("youtube_channels.json에 채널이 없습니다.")
        return 1
    state = load_json(
        STATE_PATH,
        {"resolved": {}, "seen": {}, "pending": {}, "last_digest_at": None},
    )
    state.setdefault("seen", {})
    state.setdefault("pending", {})

    if args.check:
        return check_channels(config, state)

    since = (
        now - timedelta(days=interval_days)
        if args.days
        else window_start(state, now, interval_days)
    )
    log(f"수집 구간: {since.astimezone(KST):%Y-%m-%d %H:%M} ~ "
        f"{now.astimezone(KST):%Y-%m-%d %H:%M} (KST)")

    # 수집은 매일 — 이미 보낸 것과 버퍼에 든 것은 다시 담지 않는다.
    blocked = set(state["seen"]) | set(state["pending"])
    fresh = collect(config, state, since, limit, blocked)
    added = buffer_videos(state, fresh, now)
    log(f"버퍼에 새로 담은 것 {added}건 · 버퍼 총 {len(state['pending'])}건")

    if args.dry_run:
        groups = groups_from_buffer(state, config)
        if groups:
            print()
            print(render_markdown(groups, since, now))
        else:
            log("버퍼가 비어 있습니다.")
        return 0

    # 발송은 3일에 한 번 — 간격이 안 찼으면 버퍼만 남기고 끝낸다.
    if not args.force and not due(state, now, interval_days):
        save_json(STATE_PATH, state)
        log(f"아직 {interval_days}일이 안 됐습니다 "
            f"(마지막 {state.get('last_digest_at')}) — 버퍼만 저장하고 넘어감")
        return 0

    groups = groups_from_buffer(state, config)
    total = sum(len(g["videos"]) for g in groups)
    if not total:
        save_json(STATE_PATH, state)
        log("보낼 영상이 없습니다 — 발송하지 않습니다.")
        return 0

    written = write_outputs(groups, since, now)
    log(f"파일 기록: docs/youtube/{written['links']}, docs/youtube/{written['markdown']}")

    # Pages를 켜 두지 않았으면 죽은 링크가 되므로 기본값 없이 opt-in으로 둔다.
    pages_base = os.getenv("PAGES_BASE_URL", "").rstrip("/")
    pages_url = f"{pages_base}/youtube/{written['links']}" if pages_base else None
    if not args.no_telegram:
        maybe_send_telegram(groups, since, now, pages_url)

    stamp = now.isoformat()
    for video_id in state["pending"]:
        state["seen"][video_id] = stamp
    state["pending"] = {}
    state["seen"] = prune_seen(state["seen"], now)
    state["last_digest_at"] = stamp
    save_json(STATE_PATH, state)
    log(f"완료 — 채널 {len(groups)}개 · 영상 {total}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
