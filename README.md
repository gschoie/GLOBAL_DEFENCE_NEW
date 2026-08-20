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
