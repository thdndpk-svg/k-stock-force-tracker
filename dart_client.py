from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any

from config import DartConfig


class DartClient:
    def __init__(self, config: DartConfig):
        self.config = config

    def disclosures(self, corp_code: str, days: int = 7) -> dict[str, Any]:
        end = date.today()
        start = end - timedelta(days=days)
        params = {
            "crtfc_key": self.config.api_key,
            "corp_code": corp_code,
            "bgn_de": start.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "page_count": "100",
        }
        url = f"https://opendart.fss.or.kr/api/list.json?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))


RISK_DISCLOSURE_WORDS = {
    "불성실",
    "관리종목",
    "거래정지",
    "감사의견",
    "횡령",
    "배임",
    "상장폐지",
    "유상증자",
}


def disclosure_risk_titles(items: list[dict[str, Any]]) -> list[str]:
    titles: list[str] = []
    for item in items:
        title = str(item.get("report_nm", ""))
        if any(word in title for word in RISK_DISCLOSURE_WORDS):
            titles.append(title)
    return titles
