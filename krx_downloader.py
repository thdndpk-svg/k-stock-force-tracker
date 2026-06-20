from __future__ import annotations

import urllib.parse
import urllib.request
import http.cookiejar
from datetime import date, timedelta
from pathlib import Path


KRX_BASE_URL = "https://data.krx.co.kr"
OTP_URL = f"{KRX_BASE_URL}/comm/fileDn/GenerateOTP/generate.cmd"
DOWNLOAD_URL = f"{KRX_BASE_URL}/comm/fileDn/download_csv/download.cmd"
REFERER = "https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd"


class KrxDownloadError(RuntimeError):
    pass


def previous_business_day(today: date | None = None) -> date:
    current = today or date.today()
    if current.weekday() == 5:
        return current - timedelta(days=1)
    if current.weekday() == 6:
        return current - timedelta(days=2)
    return current


def krx_date_text(target: date | str | None = None) -> str:
    if target is None:
        return previous_business_day().strftime("%Y%m%d")
    if isinstance(target, date):
        return target.strftime("%Y%m%d")
    cleaned = target.replace("-", "").replace(".", "").replace("/", "").strip()
    if len(cleaned) != 8:
        raise ValueError("KRX 날짜는 YYYYMMDD 또는 YYYY-MM-DD 형식이어야 합니다.")
    return cleaned


def make_opener() -> urllib.request.OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))


def post_form(opener: urllib.request.OpenerDirector, url: str, data: dict[str, str]) -> bytes:
    body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Mozilla/5.0",
            "Referer": REFERER,
            "Origin": KRX_BASE_URL,
        },
        method="POST",
    )
    with opener.open(request, timeout=30) as response:
        return response.read()


def download_csv(params: dict[str, str]) -> bytes:
    opener = make_opener()
    otp = post_form(opener, OTP_URL, params).decode("utf-8", errors="replace").strip()
    if not otp or "<" in otp or "error" in otp.lower() or otp == "LOGOUT":
        raise KrxDownloadError(f"KRX OTP 발급 실패: {otp[:200]}")
    data = post_form(opener, DOWNLOAD_URL, {"code": otp})
    if len(data) < 20:
        raise KrxDownloadError("KRX CSV 다운로드 실패: 로그인 세션 또는 KRX 사이트 정책 때문에 빈 응답을 받았습니다.")
    return data


def save_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def fetch_market_all(target_date: str, output_dir: Path) -> Path:
    params = {
        "locale": "ko_KR",
        "mktId": "ALL",
        "trdDd": target_date,
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
        "name": "fileDown",
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
    }
    return save_bytes(output_dir / f"krx_market_{target_date}.csv", download_csv(params))


def fetch_foreign_net_buy(target_date: str, output_dir: Path) -> Path:
    common = {
        "locale": "ko_KR",
        "mktId": "ALL",
        "strtDd": target_date,
        "endDd": target_date,
        "invstTpCd": "9000",
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
        "name": "fileDown",
    }
    candidates = [
        "dbms/MDC/STAT/standard/MDCSTAT02303",
        "dbms/MDC/STAT/standard/MDCSTAT02403",
        "dbms/MDC/STAT/standard/MDCSTAT02203",
    ]
    errors: list[str] = []
    for url in candidates:
        try:
            data = download_csv({**common, "bld": url})
            if b"," in data and len(data.splitlines()) > 1:
                return save_bytes(output_dir / f"krx_foreign_net_buy_{target_date}.csv", data)
        except Exception as error:
            errors.append(f"{url}: {error}")
    raise KrxDownloadError("투자자별 순매수상위종목 다운로드 실패\n" + "\n".join(errors))


def fetch_krx_bundle(target: date | str | None, data_dir: Path) -> tuple[Path, Path]:
    target_text = krx_date_text(target)
    market_path = fetch_market_all(target_text, data_dir)
    investor_path = fetch_foreign_net_buy(target_text, data_dir / "krx")
    return market_path, investor_path
