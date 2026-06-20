# 국내주식 종목 발굴 랩

한국투자증권 Open API와 글로벌 지표, KRX/DART 보조 데이터를 함께 사용해서 국내주식의 수급 집중, 거래량 이상증가, 차트 초입, 섹터 강세, 미국 증시 영향, 이슈 감응도를 점수화하는 종목 발굴 도구입니다.

중요: 이 프로그램은 다음날 상승을 보장하지 않습니다. “세력”을 직접 관측하는 공식 데이터는 없으므로, 외국인/기관 수급과 거래량/가격 행동을 이용해 의심 신호를 정량화합니다.

## 데이터 구조

1. 한국투자증권 Open API
   - 현재가
   - 일봉
   - 거래량 순위 원천 JSON 저장
   - 전체/KOSPI/KOSDAQ 외국인/기관 매매종목 가집계 자동 수집
   - 거래량 랭킹 자동 수집
   - 수급+거래량 후보 기반 `kis_market.csv` 자동 생성

2. 글로벌 지표
   - 나스닥, S&P500, 필라델피아 반도체
   - 엔비디아, 테슬라, 바이오 ETF, 금융 ETF
   - 원유, 달러/원, 중국 ETF, 리튬/배터리 ETF

3. KRX 데이터
   - 전종목 시세 CSV
   - 투자자별 순매수상위종목 CSV
   - 공매도/외국인 보유 데이터 CSV 확장 가능

4. OpenDART
   - 공시검색
   - 위험 공시 키워드 감점 구조

KRX 데이터 마켓플레이스는 주식 메뉴에 전종목 시세, 투자자별 순매수상위종목, 투자자별 거래실적, 공매도, 외국인보유량 관련 통계를 제공합니다.
OpenDART 개발가이드는 공시검색, 기업개황, 고유번호 API를 제공합니다.

## 설정

`.env.example`을 복사해서 `.env`를 만드세요.

```bash
cd /Users/mac/Documents/Codex/2026-06-20/new-chat/outputs/k-stock-force-tracker
cp .env.example .env
```

`.env`에 한국투자증권 키를 넣습니다. 키는 GitHub에 올리면 안 됩니다.

```text
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_VIRTUAL=true
DART_API_KEY=...
```

## 실행

샘플 데이터 분석:

```bash
python3 stock_force_tracker.py analyze
```

웹 대시보드:

```bash
python3 stock_force_tracker.py serve
```

브라우저에서 열리는 주소:

```text
http://127.0.0.1:8777/
```

앱 화면에서 사용할 주요 버튼:

- `KIS 수급 자동수집`: 전체/KOSPI/KOSDAQ 외국인·기관 수급과 거래량 후보를 새로 받습니다.
- `실시간 ON/OFF`: 장중에 30초마다 KIS 거래량 랭킹과 화면 상위 종목 현재가를 갱신합니다.
- `미국 흐름 업데이트`: 나스닥, SOX, 엔비디아, 테슬라, 환율 등 글로벌 지표를 갱신합니다.

실시간 모드는 한국투자증권 REST API 호출 제한을 피하기 위해 안정적인 30초 폴링으로 동작합니다. 더 촘촘한 체결 단위 갱신은 KIS 웹소켓 실시간 체결 API로 확장할 수 있습니다.

한국투자증권 API 원천 JSON 저장 예:

```bash
python3 stock_force_tracker.py fetch-kis --codes 005930,000660,042700 --volume-rank
```

한국투자증권 외국인/기관 수급과 거래량 후보 자동 수집:

```bash
python3 stock_force_tracker.py fetch-kis-supply
```

이 명령은 아래 파일을 자동으로 만듭니다.

```text
data/kis_market.csv
data/krx/kis_외국인_순매수.csv
data/krx/kis_기관_순매수.csv
```

이미 저장된 KIS 원천 JSON에서 CSV만 다시 만드는 명령:

```bash
python3 stock_force_tracker.py rebuild-kis-cache
```

점수식이나 금액 보정 로직을 바꾼 뒤 API를 다시 호출하지 않고 `data/kis_market.csv`, `data/krx/kis_외국인_순매수.csv`, `data/krx/kis_기관_순매수.csv`를 재생성할 때 씁니다.

글로벌 지표 업데이트:

```bash
python3 -c "from global_signals import fetch_global_signals; fetch_global_signals()"
```

## 실제 국내주식 데이터 연결 순서

1. 한국투자증권 API 키를 `.env`에 넣습니다.
2. 앱에서 `KIS 수급 자동수집` 버튼을 누르거나 `fetch-kis-supply` 명령을 실행합니다.
3. 앱에서 `미국 흐름 업데이트` 버튼을 눌러 글로벌 지표를 갱신합니다.
4. 앱이 KIS 외국인/기관 수급, 거래량 후보, 섹터/이슈, 미국 영향, 차트 초입을 합쳐 발굴 점수를 계산합니다.
5. KRX CSV는 더 넓은 전종목 데이터가 필요할 때 보조로 넣습니다.

KRX 자동 다운로드:

```bash
python3 stock_force_tracker.py fetch-krx
```

주의: KRX Data Marketplace가 로그인 세션을 요구하는 경우 `fetch-krx`가 실패할 수 있습니다. 이제 기본 수급 데이터는 KIS에서 자동으로 받으며, KRX는 더 넓은 시장 CSV를 보강할 때 사용합니다.

## 발굴 점수 방식

- 거래대금 대비 외국인/기관 수급 집중
- 외국인+기관 동반 순매수
- 20일 평균 대비 거래량 폭증
- 20일 평균 대비 거래대금 폭증
- 전고점/종가 고점 돌파
- 고가 부근 종가 마감
- 상승 초입 구간
- 섹터/테마 이슈 감응도
- 미국 증시/ETF/환율 흐름의 테마 영향
- 거래대금 유동성
- ETF/ETN/인버스/레버리지 감점
- 공매도 부담 감점
- 투자주의/투자경고/관리종목 감점

## 상세 차트

미니 그래프 또는 종목 행을 클릭하면 상세 차트를 볼 수 있습니다.

- 종가선
- MA5
- MA20
- 기준선
- 거래량 막대
- 거래량 5일 평균선

## 컴퓨터가 꺼져도 휴대폰에서 보기

Mac이 꺼져 있어도 보려면 GitHub Pages 같은 클라우드 정적 사이트에 올려야 합니다. 자세한 순서는 [CLOUD_DEPLOY.md](CLOUD_DEPLOY.md)에 정리되어 있습니다.

요약:

```bash
cd /Users/mac/Documents/Codex/2026-06-20/new-chat/outputs/k-stock-force-tracker
./scripts/publish_github_pages.sh https://github.com/YOUR_ID/k-stock-force-tracker.git
```

배포가 끝나면 휴대폰에서 아래 주소로 접속합니다.

```text
https://YOUR_ID.github.io/k-stock-force-tracker/
```

이 주소는 Mac이 꺼져 있어도 열립니다.

## 같은 Wi-Fi에서 휴대폰 미리보기

배포 전 휴대폰 화면만 빠르게 확인할 때 씁니다. 이 방식은 Mac이 켜져 있어야 합니다.

```bash
python3 stock_force_tracker.py serve-mobile
```

터미널에 아래와 같은 주소가 표시됩니다.

```text
Mobile stock viewer: http://192.168.x.x:8788/
```

휴대폰이 이 Mac과 같은 Wi-Fi에 연결되어 있으면, 사파리나 크롬에서 해당 주소를 열면 됩니다. 이 명령은 실행할 때마다 `docs/data.json`을 최신 분석 결과로 다시 만들고 모바일 화면을 띄웁니다.

## GitHub Pages 자동 분석

휴대폰용 화면은 `docs/index.html`입니다. API 키는 화면에 넣지 않습니다. GitHub Actions가 Secrets 값을 사용해서 분석을 실행하고, Pages에는 분석 결과만 배포합니다.

GitHub 저장소에 올린 뒤 아래 Secrets를 등록하세요.

```text
KIS_APP_KEY
KIS_APP_SECRET
KIS_VIRTUAL
KIS_ACCOUNT_NO
KIS_PRODUCT_CODE
DART_API_KEY
```

필수는 `KIS_APP_KEY`, `KIS_APP_SECRET`입니다. `KIS_VIRTUAL`은 모의투자 키면 `true`, 실전 키면 `false`로 넣습니다.

GitHub Pages 설정:

1. 저장소 `Settings`로 이동
2. `Pages` 메뉴 선택
3. `Source`를 `GitHub Actions`로 선택
4. `Actions` 탭에서 `Mobile stock analysis` 실행 확인

자동 분석 주기:

- 평일 한국 장중에 약 10분 간격으로 실행됩니다.
- 휴대폰 페이지의 `결과 새로고침` 버튼은 최신 배포 결과를 다시 불러옵니다.
- `데이터 분석 실행` 버튼은 GitHub Actions 실행 화면을 엽니다. 평소에는 자동 주기만으로 충분합니다.

## KRX CSV 넣는 위치

전종목 시세는 아래 파일로 맞추면 됩니다.

```text
data/sample_market.csv
```

투자자별 순매수상위종목은:

```text
data/krx/investor_net_buy.csv
```

한국어 헤더와 영문 헤더를 일부 함께 지원합니다.

## 테스트

```bash
python3 -m unittest discover
```

## 공식 참고

- 한국투자증권 Open API: https://apiportal.koreainvestment.com/
- KRX Data Marketplace: https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd
- OpenDART 개발가이드: https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001
