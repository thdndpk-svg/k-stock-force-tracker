# 컴퓨터가 꺼져도 휴대폰에서 보기

Mac이 꺼져 있어도 보려면 로컬 주소가 아니라 인터넷에 공개되는 정적 사이트가 필요합니다. 이 프로젝트는 GitHub Pages에 올리면 휴대폰에서 항상 열 수 있습니다.

## 1. GitHub 저장소 만들기

GitHub에서 새 저장소를 하나 만듭니다.

예:

```text
k-stock-force-tracker
```

저장소를 만든 뒤 HTTPS 주소를 복사합니다.

예:

```text
https://github.com/YOUR_ID/k-stock-force-tracker.git
```

## 2. 프로젝트 업로드

이 폴더에서 아래 명령을 실행합니다.

```bash
cd /Users/mac/Documents/Codex/2026-06-20/new-chat/outputs/k-stock-force-tracker
./scripts/publish_github_pages.sh https://github.com/YOUR_ID/k-stock-force-tracker.git
```

## 3. GitHub Secrets 등록

GitHub 저장소에서 `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`로 이동해 아래 값을 등록합니다.

필수:

```text
KIS_APP_KEY
KIS_APP_SECRET
```

권장:

```text
KIS_VIRTUAL
KIS_ACCOUNT_NO
KIS_PRODUCT_CODE
DART_API_KEY
```

`KIS_VIRTUAL`은 모의투자 키면 `true`, 실전 키면 `false`입니다.

## 4. Pages 켜기

GitHub 저장소에서 `Settings` -> `Pages`로 이동합니다.

```text
Source: GitHub Actions
```

로 설정합니다.

## 5. 첫 배포 실행

GitHub 저장소의 `Actions` 탭에서 `Mobile stock analysis`를 선택하고 `Run workflow`를 누릅니다.

성공하면 휴대폰 주소가 생깁니다.

```text
https://YOUR_ID.github.io/k-stock-force-tracker/
```

이 주소는 Mac이 꺼져 있어도 열립니다.

## 자동 갱신

GitHub Actions가 평일 한국 장중에 약 10분 간격으로 실행되어 `docs/data.json`을 새로 만들고 Pages에 배포합니다. 휴대폰 화면의 `결과 새로고침` 버튼은 GitHub Pages에 올라온 최신 결과를 다시 불러옵니다.

## 로컬 미리보기와 차이

`python3 stock_force_tracker.py serve-mobile`은 같은 Wi-Fi에서만 보이는 임시 확인용입니다. Mac이 꺼지면 접속할 수 없습니다.

GitHub Pages 주소는 인터넷에 올라간 정적 사이트입니다. Mac이 꺼져도 접속할 수 있습니다.
