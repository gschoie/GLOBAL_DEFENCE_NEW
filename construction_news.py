# 글로벌 건설기계 산업 데일리 브리프 수집기
#
# GS.korea-defense-news-bot과 같은 골격(구글뉴스 RSS 다중 쿼리 → 프리필터 →
# Gemini 채점/번역/요약)을 쓰되, 산출물이 텔레그램이 아니라 대시보드 JSON이다.
# 매일 아침 GitHub Actions에서 실행되어 docs/data/latest.json 을 갱신하고
# docs/index.html (GitHub Pages)이 이를 렌더링한다. 텔레그램 발송은
# ENABLE_TELEGRAM=true 로 켤 수 있게만 준비해 둔다 (기본 꺼짐).

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
from email.utils import parsedate_to_datetime
from pathlib import Path

KST = timezone(timedelta(hours=9), name="KST")
ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"
DATA_DIR = ROOT / "docs" / "data"

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
GEMINI_API_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# ---------------------------------------------------------------------------
# 쿼리 설계
#
# 시장(수요) / 기업(경쟁) / 정책·통상 세 축으로 쪼갠다.
# 방산 봇과 같은 원칙: 일반 단어와 겹치는 토큰(Bobcat=살쾡이, Caterpillar=애벌레,
# JCB=일본 신용카드, Kubota·Takeuchi=일본 성씨)은 장비 문맥 가드를 함께 요구하고,
# 고유한 토큰(Develon, XCMG, Zoomlion, bauma)은 무가드로 잡는다.
# ---------------------------------------------------------------------------

CE_GUARD = (
    'excavator OR loader OR "construction equipment" OR machinery OR "heavy equipment"'
)

# 한국 기업: HD현대건설기계·HD현대인프라코어(Develon)·두산밥캣.
# Bobcat은 동물·대학 스포츠팀(Ohio Bobcats 등)과 겹쳐 장비 문맥을 요구한다.
KR_COMPANY_QUERY = (
    '"HD Hyundai Construction Equipment" OR "Hyundai Construction Equipment" '
    'OR "HD Hyundai Infracore" OR "Develon" '
    'OR "Doosan Bobcat" '
    'OR ("Bobcat" AND (excavator OR "skid steer" OR "compact track" OR loader '
    'OR "compact equipment" OR Doosan OR telehandler))'
)
# 국내 매체는 원칙적으로 제외하지만, 한국 상장사 2사는 국내 보도가 1차 소스라
# 한국어 전용 피드를 예외 채널로 둔다 (방산 봇의 '국내 칼럼' 채널과 같은 지위)
KR_COMPANY_KO_QUERY = (
    '"HD현대건설기계" OR "HD현대인프라코어" OR "두산밥캣" OR "디벨론" OR "밥캣"'
)

# 글로벌 피어: 실적·수주·감산/증산·구조조정이 업황의 선행 신호다
GLOBAL_PEER_QUERY = (
    '"Caterpillar" OR "Komatsu" OR "Volvo Construction Equipment" OR "Volvo CE" '
    'OR "Hitachi Construction Machinery" OR "Liebherr" OR "Wacker Neuson" '
    'OR "CNH Industrial" OR "Manitou" '
    f'OR ("JCB" AND (excavator OR backhoe OR digger OR Bamford OR {CE_GUARD})) '
    f'OR ("Kubota" AND (tractor OR {CE_GUARD})) '
    f'OR ("Takeuchi" AND ({CE_GUARD})) '
    f'OR ("Terex" AND ({CE_GUARD} OR crane OR aerial))'
)

# 중국 업체: 해외(신흥국·자원국) 저가 침투가 한국 업체의 최대 경쟁 변수
CN_COMPANY_QUERY = (
    '"Sany" OR "SANY Heavy Industry" OR "XCMG" OR "Zoomlion" OR "LiuGong" '
    'OR "Sunward" OR ("Shantui" AND bulldozer)'
)

# 전략: 신제품 출시(전동화·자율화 포함), 딜러망·유통, 전시회
STRATEGY_QUERY = (
    '(("excavator" OR "wheel loader" OR "skid steer" OR "compact track loader" '
    'OR "articulated hauler" OR "construction equipment" OR "mini excavator") '
    'AND (launch OR launches OR unveil OR unveils OR debut OR introduces)) '
    'OR "electric excavator" OR "autonomous excavator" '
    'OR "bauma" OR "CONEXPO" OR "Intermat" OR "Excon" '
    'OR ((dealer OR dealership OR distributor) AND ("construction equipment" '
    'OR excavator OR Develon OR Bobcat OR Komatsu OR "Volvo CE" OR SANY OR XCMG))'
)

# 미국: 주택(착공·허가·모기지·빌더 심리) + 인프라(연방 예산·고속도로) +
# 렌탈사(URI·Ashtead 실적 = 장비 수요의 최전선 지표) + 데이터센터 건설
US_MARKET_QUERY = (
    '"housing starts" OR "building permits" OR "homebuilder confidence" OR "NAHB" '
    'OR "construction spending" OR "nonresidential construction" '
    'OR "infrastructure bill" OR "infrastructure funding" OR "highway bill" '
    'OR "surface transportation" OR "data center construction" '
    'OR "architecture billings" OR "Dodge Momentum" '
    'OR "United Rentals" OR "Ashtead" OR "Sunbelt Rentals" OR "Herc Rentals" '
    'OR ("equipment rental" AND (construction OR fleet OR utilization))'
)

# 유럽: 건설 경기(PMI·수주·허가) + 독일 인프라 기금 + CECE(유럽건설장비협회)
EUROPE_MARKET_QUERY = (
    '(("Europe" OR "European" OR "Eurozone" OR "Germany" OR "German" OR "UK" '
    'OR "France" OR "Nordic") AND ("construction output" OR "construction PMI" '
    'OR "construction sector" OR "housing permits" OR "housebuilding" '
    'OR "infrastructure fund" OR "infrastructure investment" '
    'OR "construction equipment sales")) '
    'OR "CECE" OR ("Germany" AND "special fund" AND infrastructure)'
)

# 중국: 굴착기 판매(CME/CCMA 월간 통계) + 인프라 부양(특별채) + 부동산
CHINA_MARKET_QUERY = (
    '(("China" OR "Chinese") AND ("excavator sales" OR "excavator" '
    'OR "construction machinery" OR "infrastructure stimulus" OR "special bonds" '
    'OR "special-purpose bonds" OR "property market" OR "real estate investment" '
    'OR "fixed-asset investment" OR "urban renewal" OR "urban village")) '
    'OR ("Belt and Road" AND (construction OR infrastructure))'
)

# 자원국 1 — 글로벌 광산사 capex: 장비 수요의 최선행 지표.
# 금광사(Barrick·Newmont)를 포함해 금 가격 상승 → 금광 투자 확대 경로를 잡는다
MINING_QUERY = (
    '("BHP" OR "Rio Tinto" OR "Vale" OR "Freeport" OR "Glencore" '
    'OR "Anglo American" OR "Codelco" OR "Antofagasta" OR "Barrick" OR "Newmont" '
    'OR "Teck Resources" OR "Fortescue") '
    'AND (capex OR "capital expenditure" OR expansion OR "new mine" '
    'OR investment OR fleet OR trucks OR "haul truck" OR equipment OR approve)'
)

# 자원국 2 — 지역별: 인니(니켈·석탄), 몽골(구리·석탄), 남미(구리),
# 중동(기가프로젝트), 아프리카(구리·코발트·시만두 철광석)
RESOURCE_REGION_QUERY = (
    '("Indonesia" AND (nickel OR coal OR smelter OR "mining investment")) '
    'OR ("Mongolia" AND (coal OR copper OR mining OR "Oyu Tolgoi" OR "Tavan Tolgoi")) '
    'OR (("Chile" OR "Peru") AND (copper OR "mining investment" OR "mine expansion")) '
    'OR (("Saudi Arabia" OR "NEOM" OR "UAE" OR "Qatar") '
    'AND (construction OR "giga project" OR gigaproject OR infrastructure)) '
    'OR (("Congo" OR "Zambia" OR "Ghana" OR "Guinea" OR "Simandou") '
    'AND (mining OR copper OR cobalt OR "iron ore"))'
)

# 신흥국: 인도(세계 3위 건설기계 시장) + 아세안 + 브라질
EMERGING_QUERY = (
    '("India" AND ("construction equipment" OR "infrastructure budget" '
    'OR "excavator" OR "highway construction" OR "capital expenditure" '
    'OR "National Infrastructure Pipeline")) '
    'OR (("Vietnam" OR "Philippines" OR "Thailand" OR "Malaysia") '
    'AND ("infrastructure project" OR "infrastructure spending" OR construction)) '
    'OR ("Brazil" AND ("infrastructure" OR "construction equipment")) '
    'OR ("Nusantara" AND capital)'
)

# 정책·통상·금리: 인프라 법안, 관세(철강 232조 = 원가, 장비 관세 = 수출),
# 금리는 건설·주택 문맥이 함께 있을 때만 (매크로 지표 패널이 수치를 커버)
POLICY_QUERY = (
    '"infrastructure plan" OR "infrastructure package" OR "public works program" '
    'OR ("stimulus" AND (construction OR infrastructure)) '
    'OR (tariff AND (machinery OR "construction equipment" OR excavator OR steel)) '
    'OR "Section 232" '
    'OR (("Federal Reserve" OR "ECB" OR "rate cut" OR "rate hike" OR "interest rate") '
    'AND (construction OR housing OR homebuilder OR mortgage))'
)

# 현지어 피드 — 독일(bauma의 나라, 유럽 건설경기), 인니(알랏브랏=중장비)
GERMAN_QUERY = (
    '"Baumaschinen" OR ("Bauindustrie" AND (Konjunktur OR Infrastruktur OR Krise)) '
    'OR ("Bauwirtschaft" AND (Auftrag OR Prognose))'
)
INDONESIAN_QUERY = (
    '"alat berat" OR (("ekskavator" OR "excavator") AND (tambang OR proyek)) '
    'OR ("infrastruktur" AND (tambang OR nikel OR IKN))'
)

# (섹션 키, 쿼리, hl, gl, ceid, 한국어피드 여부)
# 영어판을 앞에 두어 중복 기사는 영어 기사가 대표로 남는다 (방산 봇과 동일)
FEEDS = [
    ("kr_company", KR_COMPANY_QUERY, "en-US", "US", "US:en", False),
    ("kr_company", KR_COMPANY_QUERY, "en-GB", "GB", "GB:en", False),
    ("kr_company", KR_COMPANY_KO_QUERY, "ko", "KR", "KR:ko", True),
    ("global_peer", GLOBAL_PEER_QUERY, "en-US", "US", "US:en", False),
    ("global_peer", GLOBAL_PEER_QUERY, "en-GB", "GB", "GB:en", False),
    ("cn_company", CN_COMPANY_QUERY, "en-US", "US", "US:en", False),
    ("cn_company", CN_COMPANY_QUERY, "en-SG", "SG", "SG:en", False),
    ("strategy", STRATEGY_QUERY, "en-US", "US", "US:en", False),
    ("us", US_MARKET_QUERY, "en-US", "US", "US:en", False),
    ("europe", EUROPE_MARKET_QUERY, "en-GB", "GB", "GB:en", False),
    ("europe", GERMAN_QUERY, "de", "DE", "DE:de", False),
    ("china", CHINA_MARKET_QUERY, "en-US", "US", "US:en", False),
    ("china", CHINA_MARKET_QUERY, "en-SG", "SG", "SG:en", False),
    ("resource", MINING_QUERY, "en-US", "US", "US:en", False),
    ("resource", MINING_QUERY, "en-AU", "AU", "AU:en", False),
    ("resource", RESOURCE_REGION_QUERY, "en-US", "US", "US:en", False),
    ("resource", INDONESIAN_QUERY, "id", "ID", "ID:id", False),
    ("emerging", EMERGING_QUERY, "en-IN", "IN", "IN:en", False),
    ("emerging", EMERGING_QUERY, "en-US", "US", "US:en", False),
    ("policy", POLICY_QUERY, "en-US", "US", "US:en", False),
]

SECTION_TITLES = {
    "kr_company": "한국 기업 (HD현대·두산밥캣)",
    "global_peer": "글로벌 피어",
    "cn_company": "중국 업체",
    "strategy": "신제품·딜러망·전략",
    "us": "미국 — 주택·인프라",
    "europe": "유럽 — 건설·인프라",
    "china": "중국 — 굴착기·부동산·부양책",
    "resource": "자원국·광산 (남미·중동·아프리카·인니·몽골)",
    "emerging": "신흥국 (인도·아세안·브라질)",
    "policy": "정책·통상·금리",
}
SECTION_ORDER = [
    "kr_company", "global_peer", "cn_company", "strategy",
    "us", "europe", "china", "resource", "emerging", "policy",
]

# ---------------------------------------------------------------------------
# 프리필터 — AI 쿼터를 아끼기 위한 1차 컷
# ---------------------------------------------------------------------------

# 한국 언론사 영어보도 제외 (한국어 예외 피드는 통과) — 방산 봇 목록 재사용
EXCLUDED_SOURCE_KEYWORDS = [
    "korea herald", "korea times", "korea joongang", "joongang", "yonhap",
    "chosun", "dong-a", "donga", "hankyoreh", "hankyung",
    "korea economic daily", "ked global", "maeil", "pulse by", "businesskorea",
    "business korea", "aju business", "aju press", "arirang", "kbs world",
    "korea bizwire", "korea.net", "korea pro", "korea daily", "newsis",
    "seoul economic", "sedaily", "etnews", "asia economic", "asiae", "ajunews",
    "money today", "newspim", "edaily", "heraldcorp", "hankook ilbo",
    "kyunghyang", "segye", "chosunbiz",
]
# 주식 스크리너·재발행 사이트: CAT·DE 같은 대형주는 이런 사이트의
# 자동생성 기사가 피드를 도배한다 — 원 매체 보도만 남긴다
EXCLUDED_DOMAINS = [
    "marketbeat.com", "zacks.com", "simplywall.st", "insidermonkey.com",
    "gurufocus.com", "tipranks.com", "stocktitan.net", "defenseworld.net",
    "americanbankingnews.com", "modernreaders.com", "themarketsdaily.com",
    "wkrb13.com", "vietnam.vn", "khlaasa.net",
]

# 오검색 문맥: 동물(밥캣·살쾡이)·곤충(캐터필러)·대학 스포츠팀(Bobcats)·
# 신용카드(JCB) 등. 장비 신호가 함께 없으면 AI 채점 전에 버린다
NOISE_TITLE_PATTERN = re.compile(
    r"(?i)\b(bobcat (?:sighting|spotted|attack|kitten|cub|rescued|wildlife)"
    r"|mountain lion|wildlife officials|caterpillars? (?:crawl|found|species|infestation)"
    r"|butterfly|butterflies|moth|larvae?"
    r"|bobcats (?:beat|defeat|win|lose|fall|edge|host|vs)|lady bobcats"
    r"|basketball|football|soccer|baseball|volleyball|touchdown|quarterback"
    r"|k-?pop|box office|netflix|concert tour"
    r"|credit card|jcb card)\b"
)
STRONG_CE_PATTERN = re.compile(
    r"(?i)(excavator|wheel loader|skid steer|backhoe|bulldozer|telehandler"
    r"|construction equipment|heavy equipment|heavy machinery|machinery"
    r"|doosan|develon|hyundai|komatsu|caterpillar inc|volvo ce|liebherr|kubota"
    r"|sany|xcmg|zoomlion|liugong|dealer|dealership|mining|quarry|infrastructure"
    r"|housing starts|construction)"
)

STOCK_NOISE_TITLE_PATTERN = re.compile(
    r"(?i)(price target|analyst rating|short interest|options activity"
    r"|shares? (?:bought|sold) by|stake (?:raised|lowered|boosted|trimmed)"
    r"|hedge fund|13F|moving average|RSI|what wall street|stock a buy)"
)

# ---------------------------------------------------------------------------
# Gemini 채점·번역·요약
# ---------------------------------------------------------------------------

CATEGORY_ENUM = [
    "수주/계약", "신제품", "딜러/유통", "실적", "생산/공장", "M&A/투자",
    "정책/금리", "시장지표", "광산/자원", "부동산/주택", "기타",
]

BRIEF_SYSTEM_PROMPT = (
    "You translate news headlines (any language) into Korean and write short Korean "
    "summaries for a construction equipment industry analyst. Return valid JSON only. "
    "The output must be an object with one key named briefs, an array of objects. "
    "Each object must contain id, translated_title, summary_ko, relevance, category. "
    "translated_title must be a natural Korean translation of the original title "
    "(if the title is already Korean, copy it as is). "
    "summary_ko must be 2 short Korean sentences grounded only in the provided "
    "information. If context is thin, say the available snippet is limited. "
    "relevance must be an integer 0-10 scoring how useful the article is for "
    "analyzing the GLOBAL CONSTRUCTION EQUIPMENT industry — demand, competition, "
    "and policy. Score 6 or higher for: "
    "(1) OEM news — Caterpillar, Komatsu, Volvo CE, Hitachi CM, JCB, Liebherr, "
    "Kubota, CNH, SANY, XCMG, Zoomlion, LiuGong, and especially the Korean makers "
    "HD Hyundai Construction Equipment, HD Hyundai Infracore/Develon, Doosan Bobcat: "
    "earnings, orders, guidance, new products, electrification, dealer/distribution "
    "network changes, plant investment, restructuring, M&A; "
    "(2) demand signals — US housing starts/permits/builder sentiment, US "
    "infrastructure funding, equipment rental companies (United Rentals, Ashtead, "
    "Herc) results and fleet plans, European construction activity, China excavator "
    "sales and infrastructure/property stimulus, India and ASEAN infrastructure "
    "spending, data center construction; "
    "(3) mining capex — mining companies approving projects, expanding mines, "
    "ordering fleets (BHP, Rio Tinto, Vale, Freeport, Glencore, Codelco, Barrick, "
    "Newmont, Oyu Tolgoi, Simandou, Indonesian nickel/coal, Mongolian coal/copper); "
    "(4) policy — infrastructure bills/packages in any major market, tariffs on "
    "steel or machinery, interest rate moves tied to construction/housing. "
    "Score 3 or lower for: articles about the animal bobcat or caterpillar insects, "
    "college sports teams, generic stock-screener pieces (price targets, 13F "
    "filings, short interest), passing mentions, and unrelated industries. "
    "category must be exactly one of: "
    + ", ".join(CATEGORY_ENUM) + ". "
    "Preserve every input id exactly once."
)


class QuotaExhaustedError(RuntimeError):
    """Gemini 일일 무료 쿼터 소진 — 재시도로 회복 불가."""


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def http_get(url: str, timeout: int = 30, headers: dict | None = None) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# ---------------------------------------------------------------------------
# 뉴스 수집
# ---------------------------------------------------------------------------

def build_rss_url(query: str, hl: str, gl: str, ceid: str) -> str:
    lookback = os.getenv("GOOGLE_NEWS_LOOKBACK", "2d")
    if lookback:
        query = f"{query} when:{lookback}"
    params = {"q": query, "hl": hl, "gl": gl, "ceid": ceid}
    return f"{GOOGLE_NEWS_RSS}?{urllib.parse.urlencode(params)}"


def fetch_single_feed(
    section: str, query: str, hl: str, gl: str, ceid: str, is_korean_feed: bool
) -> list[dict]:
    xml_bytes = http_get(build_rss_url(query, hl, gl, ceid))
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall("./channel/item"):
        guid = item.findtext("guid", default="")
        title = item.findtext("title", default="(no title)")
        link = item.findtext("link", default="")
        pub_date = item.findtext("pubDate", default="")
        source_el = item.find("source")
        source = (source_el.text or "") if source_el is not None else ""
        source_url = source_el.get("url", "") if source_el is not None else ""
        items.append(
            {
                "id": guid or link or title,
                "section": section,
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "source": source,
                "source_url": source_url,
                "feed_lang": hl.split("-")[0].lower(),
                "is_korean_feed": is_korean_feed,
            }
        )
    return items


def fetch_all_feeds() -> list[dict]:
    merged: list[dict] = []
    seen_keys: set[str] = set()
    for section, query, hl, gl, ceid, is_korean_feed in FEEDS:
        try:
            feed_items = fetch_single_feed(section, query, hl, gl, ceid, is_korean_feed)
        except Exception as exc:
            print(f"Feed fetch failed ({section}/{ceid}): {exc}", file=sys.stderr, flush=True)
            continue
        for item in feed_items:
            # 같은 기사가 에디션·쿼리마다 다른 guid로 잡힐 수 있어 제목+매체로도 dedupe
            dedupe_keys = [
                item["id"],
                f"{item['title'].strip().lower()}|{item['source'].strip().lower()}",
            ]
            if any(key in seen_keys for key in dedupe_keys):
                continue
            seen_keys.update(dedupe_keys)
            merged.append(item)
    return merged


def is_excluded_source(item: dict) -> bool:
    # 한국어 예외 피드(HD현대·두산밥캣 국내 보도)는 통과
    if item.get("is_korean_feed"):
        return False
    if os.getenv("EXCLUDE_KOREAN_MEDIA", "true").lower() != "true":
        return False
    # 예외 피드 밖에서 한글 제목/매체 = 국내 보도 → 제외 (한국어 피드가 커버)
    if re.search(r"[가-힣]", f"{item.get('source', '')} {item.get('title', '')}"):
        return True
    source = item.get("source", "").lower()
    return bool(source) and any(k in source for k in EXCLUDED_SOURCE_KEYWORDS)


def is_blocked_domain(item: dict) -> bool:
    host = urllib.parse.urlparse(item.get("source_url", "")).netloc.lower()
    if not host:
        return False
    extra = [
        d.strip().lower()
        for d in os.getenv("EXTRA_EXCLUDED_DOMAINS", "").split(",")
        if d.strip()
    ]
    return any(
        host == d or host.endswith("." + d) for d in EXCLUDED_DOMAINS + extra
    )


def is_noise_item(item: dict) -> bool:
    title = item.get("title", "")
    if STOCK_NOISE_TITLE_PATTERN.search(title):
        return True
    return bool(NOISE_TITLE_PATTERN.search(title)) and not STRONG_CE_PATTERN.search(title)


def parse_date_for_sort(value: str) -> float:
    if not value:
        return 0.0
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


# ---------------------------------------------------------------------------
# 기사 본문 컨텍스트 (요약 근거)
# ---------------------------------------------------------------------------

def strip_html(raw_html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw_html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def resolve_final_article_url(link: str) -> str:
    if not link:
        return ""
    try:
        request = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.geturl() or link
    except Exception:
        return link


def get_article_url(item: dict) -> str:
    resolved = item.get("resolved_link", "").strip()
    if resolved:
        return resolved
    item["resolved_link"] = resolve_final_article_url(item.get("link", "").strip())
    return item["resolved_link"]


def fetch_article_context(link: str) -> str:
    if not link:
        return ""
    try:
        raw_html = http_get(link)[:300_000].decode("utf-8", errors="ignore")
    except Exception:
        return ""
    for pattern in (
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
    ):
        match = re.search(pattern, raw_html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            description = html.unescape(match.group(1)).strip()
            if description:
                return description[:1500]
    return strip_html(raw_html)[:1500]


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def ai_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def gemini_request(system_text: str, user_text: str) -> str:
    model = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
    body = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "briefs": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "id": {"type": "STRING"},
                                "translated_title": {"type": "STRING"},
                                "summary_ko": {"type": "STRING"},
                                "relevance": {"type": "INTEGER"},
                                "category": {"type": "STRING", "enum": CATEGORY_ENUM},
                            },
                            "required": [
                                "id", "translated_title", "summary_ko",
                                "relevance", "category",
                            ],
                        },
                    }
                },
                "required": ["briefs"],
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
                    text_value = part.get("text")
                    if isinstance(text_value, str) and text_value.strip():
                        parts.append(text_value.strip())
            output_text = "\n".join(parts).strip()
            if not output_text:
                raise RuntimeError("Gemini response did not include usable text output")
            return output_text
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 429:
                if "PerDay" in error_body or "per day" in error_body.lower():
                    raise QuotaExhaustedError(
                        "Gemini daily free-tier quota exhausted"
                    ) from exc
                if attempt < max_attempts:
                    delay_match = re.search(r'"retryDelay":\s*"(\d+)', error_body)
                    delay = (
                        float(delay_match.group(1)) + 1.0
                        if delay_match
                        else base_delay * (2 ** (attempt - 1))
                    )
                    time.sleep(delay)
                    continue
            raise RuntimeError(
                f"Gemini API error {exc.code}: {error_body or exc.reason}"
            ) from exc

    raise RuntimeError("Gemini API retry loop exited unexpectedly")


def build_batch_prompt(items: list[dict]) -> list[dict]:
    prompt_items = []
    for item in items:
        article_url = get_article_url(item)
        prompt_items.append(
            {
                "id": item["id"],
                "title": item["title"],
                "source": item["source"],
                "published": item["pub_date"],
                "section": item["section"],
                "link": article_url,
                "article_context": fetch_article_context(article_url),
            }
        )
    return prompt_items


def score_items_chunk(items: list[dict]) -> dict[str, dict]:
    empty = {
        item["id"]: {
            "translated_title": "",
            "summary_ko": "",
            "relevance": None,
            "category": "기타",
        }
        for item in items
    }
    user_text = json.dumps(build_batch_prompt(items), ensure_ascii=False)
    output_text = gemini_request(BRIEF_SYSTEM_PROMPT, user_text)
    parsed = json.loads(output_text)
    results = dict(empty)
    for brief in parsed.get("briefs", []):
        brief_id = str(brief.get("id", "")).strip()
        if brief_id and brief_id in results:
            try:
                relevance = int(brief.get("relevance"))
            except (TypeError, ValueError):
                relevance = None
            category = str(brief.get("category", "기타")).strip()
            if category not in CATEGORY_ENUM:
                category = "기타"
            results[brief_id] = {
                "translated_title": str(brief.get("translated_title", "")).strip(),
                "summary_ko": str(brief.get("summary_ko", "")).strip(),
                "relevance": relevance,
                "category": category,
            }
    return results


def score_items(items: list[dict]) -> dict[str, dict]:
    # 청크 단위 실패 격리 + 무점수 항목 1회 재시도 (방산 봇과 동일한 전략)
    results = {
        item["id"]: {
            "translated_title": "",
            "summary_ko": "",
            "relevance": None,
            "category": "기타",
        }
        for item in items
    }
    if not items or not ai_available():
        return results
    batch_size = max(1, int(os.getenv("GEMINI_BATCH_SIZE", "10")))
    for start in range(0, len(items), batch_size):
        chunk = items[start : start + batch_size]
        try:
            results.update(score_items_chunk(chunk))
        except QuotaExhaustedError:
            raise
        except Exception as exc:
            print(
                f"AI chunk failed (items {start + 1}-{start + len(chunk)}): {exc}",
                file=sys.stderr, flush=True,
            )
    unscored = [item for item in items if results[item["id"]]["relevance"] is None]
    for start in range(0, len(unscored), batch_size):
        chunk = unscored[start : start + batch_size]
        try:
            results.update(score_items_chunk(chunk))
        except QuotaExhaustedError:
            raise
        except Exception as exc:
            print(f"AI retry failed ({len(chunk)} item(s)): {exc}", file=sys.stderr, flush=True)
    return results


# ---------------------------------------------------------------------------
# 매크로 지표 — API 키 없는 무료 소스만 사용
#   FRED fredgraph.csv : 금리·모기지·주택착공·건설지출
#   stooq.com CSV      : 금·구리·WTI·환율 (일간 시계열)
# ---------------------------------------------------------------------------

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
STOOQ_CSV = "https://stooq.com/q/d/l/?s={symbol}&i=d"

# (지표 id, 표시명, 단위, 소스, 시리즈/심볼, 소수 자릿수, 그룹)
MACRO_SPECS = [
    ("us10y", "미국채 10년", "%", "fred", "DGS10", 2, "금리"),
    ("mortgage30", "미 모기지 30년(주간)", "%", "fred", "MORTGAGE30US", 2, "금리"),
    ("gold", "금 (XAU/USD)", "$/oz", "stooq", "xauusd", 0, "원자재"),
    ("copper", "구리 (COMEX)", "$/lb", "stooq", "hg.f", 2, "원자재"),
    ("wti", "WTI 원유", "$/bbl", "stooq", "cl.f", 1, "원자재"),
    ("usdkrw", "원/달러", "₩", "stooq", "usdkrw", 0, "환율"),
    ("usdcny", "위안/달러", "¥", "stooq", "usdcny", 3, "환율"),
    ("houst", "미 주택착공(연율, 월간)", "천호", "fred", "HOUST", 0, "미국 건설"),
    ("permit", "미 건축허가(연율, 월간)", "천호", "fred", "PERMIT", 0, "미국 건설"),
    ("ttlcons", "미 건설지출(월간)", "$B", "fred", "TTLCONS", 0, "미국 건설"),
]
MACRO_GROUP_ORDER = ["금리", "원자재", "환율", "미국 건설"]


def fetch_fred_series(series: str) -> list[tuple[str, float]]:
    raw = http_get(FRED_CSV.format(series=series), timeout=30).decode("utf-8")
    rows = []
    for line in raw.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) != 2:
            continue
        date_str, value_str = parts[0].strip(), parts[1].strip()
        if value_str in ("", "."):
            continue
        try:
            rows.append((date_str, float(value_str)))
        except ValueError:
            continue
    return rows


def fetch_stooq_series(symbol: str) -> list[tuple[str, float]]:
    raw = http_get(STOOQ_CSV.format(symbol=symbol), timeout=30).decode("utf-8")
    rows = []
    for line in raw.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 5:
            continue
        try:
            rows.append((parts[0].strip(), float(parts[4])))
        except ValueError:
            continue
    return rows


def collect_macro() -> list[dict]:
    indicators = []
    for spec_id, label, unit, source, series, decimals, group in MACRO_SPECS:
        try:
            rows = (
                fetch_fred_series(series)
                if source == "fred"
                else fetch_stooq_series(series)
            )
            if len(rows) < 2:
                raise RuntimeError("insufficient data")
            # TTLCONS는 $M 단위 → $B로 환산
            if series == "TTLCONS":
                rows = [(d, v / 1000.0) for d, v in rows]
            asof, value = rows[-1]
            _, prev = rows[-2]
            spark = [round(v, 4) for _, v in rows[-30:]]
            change = value - prev
            indicators.append(
                {
                    "id": spec_id,
                    "label": label,
                    "unit": unit,
                    "group": group,
                    "value": round(value, decimals),
                    "prev": round(prev, decimals),
                    "change": round(change, max(decimals, 2)),
                    "change_pct": round(change / prev * 100.0, 2) if prev else None,
                    "asof": asof,
                    "spark": spark,
                }
            )
        except Exception as exc:
            print(f"Macro fetch failed ({spec_id}): {exc}", file=sys.stderr, flush=True)
    return indicators


# ---------------------------------------------------------------------------
# 상태 관리 — 대시보드 중심 설계
#
# 방산 봇의 seen_ids(발송 여부)와 달리, 채점 결과 자체를 캐시한다.
# 같은 날 재실행해도 대시보드가 비지 않고, 이미 채점한 기사를 재채점하지 않는다.
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"items": {}}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        state.setdefault("items", {})
        return state
    except json.JSONDecodeError:
        return {"items": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def prune_state_items(state: dict, now_ts: float) -> None:
    keep_hours = float(os.getenv("STATE_RETENTION_HOURS", "168"))  # 7일
    cutoff = now_ts - keep_hours * 3600
    state["items"] = {
        item_id: record
        for item_id, record in state["items"].items()
        if record.get("first_seen_ts", 0) >= cutoff
    }


# ---------------------------------------------------------------------------
# 대시보드 JSON 출력
# ---------------------------------------------------------------------------

def write_dashboard_json(state: dict, macro: list[dict], stats: dict) -> None:
    now = datetime.now(KST)
    window_hours = float(os.getenv("DASHBOARD_WINDOW_HOURS", "36"))
    window_cutoff = now.timestamp() - window_hours * 3600
    min_relevance = int(os.getenv("MIN_RELEVANCE", "4"))
    max_per_section = int(os.getenv("MAX_ITEMS_PER_SECTION", "15"))

    sections = []
    for key in SECTION_ORDER:
        records = [
            r
            for r in state["items"].values()
            if r["section"] == key
            and r.get("first_seen_ts", 0) >= window_cutoff
            and (
                r.get("relevance") is None  # AI 미가동 시에도 표시
                or r["relevance"] >= min_relevance
            )
        ]
        records.sort(
            key=lambda r: (r.get("relevance") or 0, r.get("pub_ts") or 0),
            reverse=True,
        )
        items = [
            {
                "title": r["title"],
                "translated_title": r.get("translated_title", ""),
                "summary_ko": r.get("summary_ko", ""),
                "category": r.get("category", "기타"),
                "source": r.get("source", ""),
                "published": r.get("published_iso", ""),
                "link": r.get("link", ""),
                "relevance": r.get("relevance"),
                "lang": r.get("feed_lang", "en"),
            }
            for r in records[:max_per_section]
        ]
        sections.append(
            {"key": key, "title": SECTION_TITLES[key], "items": items}
        )

    macro_groups = []
    for group in MACRO_GROUP_ORDER:
        group_indicators = [m for m in macro if m["group"] == group]
        if group_indicators:
            macro_groups.append({"title": group, "indicators": group_indicators})

    payload = {
        "generated_at": now.isoformat(),
        "date_kst": now.strftime("%Y-%m-%d"),
        "window_hours": window_hours,
        "macro_groups": macro_groups,
        "sections": sections,
        "stats": stats,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    archive_dir = DATA_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    latest_path = DATA_DIR / "latest.json"
    latest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    archive_path = archive_dir / f"{payload['date_kst']}.json"
    archive_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # 아카이브 목록 (대시보드 날짜 선택용)
    dates = sorted(
        (p.stem for p in archive_dir.glob("????-??-??.json")), reverse=True
    )
    max_archive_days = int(os.getenv("MAX_ARCHIVE_DAYS", "60"))
    for stale_date in dates[max_archive_days:]:
        (archive_dir / f"{stale_date}.json").unlink(missing_ok=True)
    dates = dates[:max_archive_days]
    (archive_dir / "index.json").write_text(
        json.dumps({"dates": dates}, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 텔레그램 (나중 단계 — 기본 비활성)
# ---------------------------------------------------------------------------

def send_telegram_message(text: str) -> None:
    token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
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


def maybe_send_telegram_digest(state: dict, new_ids: list[str]) -> None:
    if os.getenv("ENABLE_TELEGRAM", "false").lower() != "true":
        return
    min_relevance = int(os.getenv("MIN_RELEVANCE", "4"))
    fresh = [
        state["items"][i]
        for i in new_ids
        if (state["items"][i].get("relevance") or 0) >= min_relevance
    ]
    if not fresh:
        return
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    lines = [
        "<b>건설기계 산업 뉴스 브리프</b>",
        f"<b>기준시각:</b> {stamp} (KST) · 신규 {len(fresh)}건",
    ]
    send_telegram_message("\n".join(lines))
    fresh.sort(key=lambda r: (r.get("relevance") or 0), reverse=True)
    max_send = int(os.getenv("TELEGRAM_MAX_ITEMS", "20"))
    for record in fresh[:max_send]:
        title = record.get("translated_title") or record["title"]
        lines = [
            f"<b>{html.escape(title)}</b>",
            f"[{record.get('category', '기타')}] {html.escape(record.get('source', ''))}",
        ]
        if record.get("summary_ko"):
            lines.append(html.escape(record["summary_ko"]))
        if record.get("link"):
            lines.append(html.escape(record["link"]))
        send_telegram_message("\n".join(lines))
        time.sleep(1.1)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def run_once() -> None:
    now_ts = time.time()
    state = load_state()
    prune_state_items(state, now_ts)

    items = fetch_all_feeds()
    print(f"Fetched {len(items)} candidate item(s) from {len(FEEDS)} feed(s).", flush=True)

    max_age_hours = float(os.getenv("MAX_ITEM_AGE_HOURS", "48"))
    age_cutoff_ts = now_ts - max_age_hours * 3600

    counts = {"stale": 0, "korean_media": 0, "blocked_domain": 0, "noise": 0, "known": 0}
    fresh_items = []
    for item in items:
        if item["id"] in state["items"]:
            counts["known"] += 1
            continue
        pub_ts = parse_date_for_sort(item["pub_date"])
        if pub_ts and pub_ts < age_cutoff_ts:
            counts["stale"] += 1
            continue
        if is_excluded_source(item):
            counts["korean_media"] += 1
            continue
        if is_blocked_domain(item):
            counts["blocked_domain"] += 1
            continue
        if is_noise_item(item):
            counts["noise"] += 1
            print(f"Noise: {item['title'][:80]}", flush=True)
            continue
        item["pub_ts"] = pub_ts
        fresh_items.append(item)

    # 최신순으로 채점 한도 적용 — 무료 쿼터 보호
    fresh_items.sort(key=lambda i: i.get("pub_ts") or 0, reverse=True)
    max_to_score = int(os.getenv("MAX_ITEMS_TO_SCORE", "80"))
    overflow = fresh_items[max_to_score:]
    fresh_items = fresh_items[:max_to_score]
    if overflow:
        print(f"Skipped AI scoring for {len(overflow)} overflow item(s).", flush=True)

    quota_exhausted = False
    briefs: dict[str, dict] = {}
    try:
        briefs = score_items(fresh_items)
    except QuotaExhaustedError as exc:
        quota_exhausted = True
        print(f"AI scoring unavailable: {exc}", file=sys.stderr, flush=True)

    ai_active = ai_available() and not quota_exhausted
    min_relevance = int(os.getenv("MIN_RELEVANCE", "4"))
    new_ids: list[str] = []
    dropped_low = 0
    for item in fresh_items:
        brief = briefs.get(item["id"]) or {}
        relevance = brief.get("relevance")
        if ai_active and relevance is None:
            # 채점 실패분은 상태에 넣지 않는다 → 다음 실행에서 새 기사로 재시도
            continue
        if isinstance(relevance, int) and relevance < min_relevance:
            dropped_low += 1
        published_iso = ""
        if item.get("pub_ts"):
            published_iso = datetime.fromtimestamp(
                item["pub_ts"], tz=timezone.utc
            ).astimezone(KST).isoformat()
        state["items"][item["id"]] = {
            "section": item["section"],
            "title": item["title"],
            "link": get_article_url(item),
            "source": item["source"],
            "published_iso": published_iso,
            "pub_ts": item.get("pub_ts") or 0,
            "first_seen_ts": now_ts,
            "feed_lang": item["feed_lang"],
            "translated_title": brief.get("translated_title", ""),
            "summary_ko": brief.get("summary_ko", ""),
            "relevance": relevance,
            "category": brief.get("category", "기타"),
        }
        new_ids.append(item["id"])

    macro = collect_macro()
    print(f"Collected {len(macro)} macro indicator(s).", flush=True)

    stats = {
        "candidates": len(items),
        "new_scored": len(new_ids),
        "dropped_low_relevance": dropped_low,
        "noise": counts["noise"],
        "korean_media_excluded": counts["korean_media"],
        "blocked_domain": counts["blocked_domain"],
        "stale": counts["stale"],
        "ai_active": ai_active,
        "quota_exhausted": quota_exhausted,
    }
    write_dashboard_json(state, macro, stats)
    save_state(state)

    try:
        maybe_send_telegram_digest(state, new_ids)
    except Exception as exc:
        print(f"Telegram send failed: {exc}", file=sys.stderr, flush=True)

    print(
        f"Done: {len(new_ids)} new item(s) stored, {dropped_low} below relevance cut, "
        f"AI active: {ai_active}.",
        flush=True,
    )


def main() -> int:
    load_dotenv(ROOT / ".env")
    run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
