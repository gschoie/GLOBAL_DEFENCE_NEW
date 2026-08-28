# 건설기계 데일리 브리프 (Global Construction Equipment Daily)

방산 뉴스 봇(GS.korea-defense-news-bot)과 같은 골격으로 만든 **건설기계 산업 버전**입니다.
매일 아침 구글뉴스 RSS를 시장·기업·정책 축으로 수집하고, Gemini가 관련성 채점 + 한국어
번역·요약 + 카테고리 분류를 한 뒤, **대시보드(GitHub Pages)** 로 보여줍니다.
텔레그램 발송은 코드에 준비만 되어 있고 기본 꺼짐입니다 (나중 단계).

- 수집기: `construction_news.py` (표준 라이브러리만 사용, 외부 패키지 불필요)
- 대시보드: `docs/index.html` → GitHub Pages로 서빙 (`docs/data/latest.json` 렌더)
- 스케줄: `.github/workflows/daily-brief.yml` — 매일 KST 06:00 (UTC 21:00)

## 커버리지 설계

### 뉴스 섹션 (수요 → 경쟁 → 정책 3축)

| 섹션 | 잡는 것 | 왜 |
|---|---|---|
| 한국 기업 | HD현대건설기계·HD현대인프라코어(Develon)·두산밥캣 — 실적, 수주, 신제품, 딜러망. 국내 매체는 한국어 예외 피드로 커버 | 커버리지의 중심 |
| 글로벌 피어 | CAT, Komatsu, Volvo CE, Hitachi CM, JCB, Liebherr, Kubota, CNH, Terex 등 — 실적·가이던스·재고(destocking)·구조조정 | 업황의 선행 신호 + 상대 밸류에이션 |
| 중국 업체 | SANY, XCMG, Zoomlion, LiuGong — 해외 침투·수출·가격 경쟁 | 신흥국·자원국에서 한국 업체의 최대 경쟁 변수 |
| 신제품·딜러망·전략 | 신장비 출시(전동화·자율화), bauma/CONEXPO 전시, 딜러·유통망 계약 | 유저 요청 축 — 제품·채널 전략 |
| 미국 | 주택착공·허가·빌더심리(NAHB), 건설지출, 인프라 예산·고속도로, 데이터센터 건설, **렌탈사(United Rentals·Ashtead·Herc)** | 렌탈사 실적·가동률은 장비 수요의 최전선 지표 |
| 유럽 | 건설 PMI·생산, 주택 허가, 독일 인프라 특별기금, CECE | 회복 트리거 = 독일 재정 |
| 중국 | **월간 굴착기 판매(CME 통계)**, 인프라 특별채, 부동산 투자·부양책, 일대일로 | 굴착기 판매는 업계 대표 데이터 포인트 |
| 자원국·광산 | 글로벌 광산사(BHP·Rio·Vale·Freeport·Glencore·Codelco·**Barrick·Newmont**) capex·신광산 승인·장비 발주 + 인니(니켈·석탄)·몽골(오유톨고이)·남미(구리)·중동(NEOM 기가프로젝트)·아프리카(시만두) | 광산 capex = 대형 장비 수요. 금광사 포함 → 금 가격 상승이 투자로 이어지는 경로 포착 |
| 신흥국 | 인도(세계 3위 시장 — 도로·인프라 예산), 아세안, 브라질, 인니 신수도 | 볼륨 성장 시장 |
| 정책·통상·금리 | 인프라 법안·패키지, 철강/기계 관세(232조), 건설·주택 문맥의 금리 뉴스 | 관세=원가·수출, 금리=주택 수요 |

노이즈 가드: Bobcat(동물·대학 스포츠팀), Caterpillar(애벌레), JCB(신용카드),
Kubota/Takeuchi(성씨)는 장비 문맥을 함께 요구. 주식 스크리너 자동생성 기사
(price target, 13F 등)는 도메인·제목 패턴으로 차단.

### 매크로 지표 패널 (API 키 불필요)

| 그룹 | 지표 | 소스 |
|---|---|---|
| 금리 | 미국채 10년, 미 모기지 30년(주간) | FRED fredgraph.csv |
| 원자재 | 금(XAU/USD), 구리(COMEX), WTI | stooq.com CSV |
| 환율 | 원/달러, 위안/달러 | stooq.com CSV |
| 미국 건설 | 주택착공, 건축허가, 건설지출 | FRED |

철광석·원료탄은 무료 무키 소스가 없어 수치 패널에서는 제외 — 가격 급변은
자원국·광산 뉴스 섹션이 커버합니다.

## 설정 (1회)

1. **Gemini 키**: 리포지토리 Settings → Secrets and variables → Actions →
   `GEMINI_API_KEY` 추가 (방산 봇과 같은 키 재사용 가능. 무료 티어 OK).
2. **GitHub Pages**: Settings → Pages → Source: `Deploy from a branch`,
   Branch: 기본 브랜치 / `/docs` 선택.
3. Actions 탭에서 `Daily Construction Equipment Brief` 워크플로를 한 번
   수동 실행(workflow_dispatch)하면 샘플 데이터가 실데이터로 교체됩니다.

## 로컬 실행

```bash
cp .env.example .env   # GEMINI_API_KEY 채우기
python construction_news.py
python -m http.server -d docs 8000   # http://localhost:8000
```

## 주요 환경변수

| 변수 | 기본 | 의미 |
|---|---|---|
| `GOOGLE_NEWS_LOOKBACK` | `2d` | 구글뉴스 검색 창 |
| `DASHBOARD_WINDOW_HOURS` | `36` | 대시보드에 표시할 기사 창 |
| `MIN_RELEVANCE` | `4` | 관련성 컷 (0–10) |
| `MAX_ITEMS_TO_SCORE` | `80` | 회당 AI 채점 한도 (무료 쿼터 보호) |
| `MAX_ITEMS_PER_SECTION` | `15` | 섹션당 표시 건수 |
| `ENABLE_TELEGRAM` | `false` | 텔레그램 다이제스트 발송 (나중 단계) |

## 텔레그램 (나중 단계)

`TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID` 시크릿을 추가하고 워크플로의
`ENABLE_TELEGRAM`을 `true`로 바꾸면, 실행마다 신규 고관련성 기사를
다이제스트로 발송합니다.

---

# 유튜브 3일 모음 (NotebookLM 오디오용)

구독 채널의 최근 영상 **링크만** 3일에 한 번 모아 텔레그램(@gs_analyst_bot)으로
보내고, 같은 목록을 파일로도 남깁니다. NotebookLM에 소스로 넣어 오디오 개요로
듣는 용도입니다.

- 수집기: `youtube_digest.py` (표준 라이브러리만 사용)
- 채널 목록: `youtube_channels.json` ← **여기만 고치면 대상이 바뀝니다**
- 상태: `youtube_state.json` (채널 ID 캐시 + 이미 보낸 영상 ID + 마지막 발송 시각)
- 스케줄: `.github/workflows/youtube-digest.yml`

## 산출물

| 파일 | 쓰임 |
|---|---|
| `docs/youtube/<날짜>-links.txt` | **주소만 한 줄씩** — NotebookLM 소스 창에 통째로 붙여넣기 |
| `docs/youtube/<날짜>.md` | 채널·제목·시각이 붙은 읽는 판 |
| `docs/youtube/latest-links.txt`, `latest.md` | 항상 최신본 |
| `docs/youtube/index.json` | 지난 모음 목록 |

Pages가 켜져 있으면 텔레그램 메시지에
`https://gschoie.github.io/GLOBAL_DEFENCE_NEW/youtube/<날짜>-links.txt` 링크가
같이 갑니다. 폰에서 그 주소를 열어 전체 복사 → NotebookLM에 붙여넣으면 끝입니다.

## 채널 등록

**유튜브에서 채널 주소를 복사해 `url`에 그대로 붙여넣으면 끝입니다.**

```json
{ "url": "https://www.youtube.com/@user-charlesmililab" }
{ "url": "https://www.youtube.com/channel/UC..." }
```

`.../channel/UC…` 꼴이면 그 안의 ID를 바로 읽어 요청 없이 잡습니다.
`@핸들`이면 첫 실행 때 채널 페이지에서 ID를 찾아 `youtube_state.json`에 캐시합니다.

`name`은 생략해도 됩니다 — **유튜브가 주는 실제 채널명**을 씁니다. 묶음 순서는
이름과 무관하게 이 목록 순서를 따릅니다. 잡히는지 확인은 아래 `--check`로 합니다.

선택 키: `match`(제목 정규식에 걸리는 것만), `exclude`(걸리면 버림),
`enabled: false`(잠시 끄기).

## 놓치지 않는 구조 — 수집은 매일, 발송은 3일에 한 번

유튜브 피드는 채널당 **최근 15개**만 들고 있습니다. 3일에 한 번만 긁으면
그 사이 15개를 넘겨 올린 채널은 앞부분이 잘려 나갑니다. 그래서 워크플로는
**매일** 돌며 새 영상을 `youtube_state.json`의 `pending` 버퍼에 쌓아 두고,
3일이 차면 버퍼를 통째로 비워 내보냅니다.

- 크론 `*/3`은 달이 바뀔 때 간격이 어긋나 쓰지 않았습니다. 발송 여부는
  상태 파일의 마지막 발송 시각으로 판단합니다.
- 러너가 하루 죽어도 다음 날 그 구간까지 담아 따라잡습니다(최대 소급 14일).
- 이미 보낸 영상은 ID로 걸러 다시 담지 않습니다.

### 쇼츠도 잡습니다

채널 피드(`channel_id=UC…`)는 쇼츠를 빼고 주는 경우가 보고돼 있어,
**업로드 재생목록 피드**(`playlist_id=UU…`)를 같이 긁어 합칩니다. 한쪽이
막히거나 404여도 나머지로 계속 갑니다. 링크는 `/shorts/` 대신
`watch?v=` 형태로 냅니다 — 쇼츠도 이 주소로 열리고, NotebookLM도 이 쪽을 받습니다.

한계 하나: 한 채널이 **하루에 15개 넘게** 올리면 그날 치 앞부분은 잘립니다.
그런 채널이 있으면 워크플로를 하루 두 번으로 늘리면 됩니다.

## 설정 (1회)

1. `@gs_analyst_bot`과 대화를 시작한 뒤 봇 토큰과 chat_id를 확인합니다.
2. Settings → Secrets → Actions 에 `YT_TELEGRAM_BOT_TOKEN`,
   `YT_TELEGRAM_CHAT_ID` 추가. (없으면 `TELEGRAM_*` 로 폴백합니다.)
3. **이 브랜치를 기본 브랜치에 합칩니다.** GitHub Actions의 `schedule`과
   `workflow_dispatch`는 **기본 브랜치의 워크플로만** 봅니다. 합치기 전에는
   크론도 안 돌고 Actions 탭에 수동 실행 버튼도 안 생깁니다.
4. Actions 탭 → `YouTube 3-Day Digest` → `check: true`로 한 번 돌려
   채널 6개가 다 잡히는지 확인합니다(발송·커밋 안 함).
5. 확인되면 `force: true`로 첫 모음을 받아 봅니다.

## 로컬 실행

```bash
python youtube_digest.py --check            # 채널이 잡히는지만 확인 (아무것도 안 씀)
python youtube_digest.py --dry-run          # 파일·상태·텔레그램 안 건드리고 출력만
python youtube_digest.py --force --days 7   # 7일치를 지금 바로
python -m unittest discover -s tests        # 39개 검산 테스트
```

## NotebookLM 쪽 주의

- NotebookLM의 유튜브 소스는 **공개 영상 + 자막이 있는 것**만 읽습니다.
  자막 없는 영상은 소스 추가 단계에서 거절됩니다.
- 소스 개수 상한(무료 50개)이 있으니, 3일치가 많으면 `YT_MAX_PER_CHANNEL`을
  줄이거나 채널별 `match`로 좁히세요.
