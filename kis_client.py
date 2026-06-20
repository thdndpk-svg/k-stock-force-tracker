from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from config import APP_DIR, KisConfig


TOKEN_CACHE = APP_DIR / ".kis_token_cache.json"


class KisApiError(RuntimeError):
    pass


class KisClient:
    def __init__(self, config: KisConfig):
        self.config = config
        self.access_token = self._load_cached_token()

    def _load_cached_token(self) -> str | None:
        if not TOKEN_CACHE.exists():
            return None
        try:
            data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if data.get("expires_at", 0) <= time.time() + 300:
            return None
        return str(data.get("access_token") or "") or None

    def _save_token(self, token: str, expires_in: int) -> None:
        TOKEN_CACHE.write_text(
            json.dumps(
                {"access_token": token, "expires_at": time.time() + max(60, expires_in - 60)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def ensure_token(self) -> str:
        if self.access_token:
            return self.access_token
        url = f"{self.config.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
        }
        token = self._post_json(url, payload, headers={"content-type": "application/json; charset=utf-8"})
        access_token = str(token.get("access_token") or "")
        if not access_token:
            raise KisApiError(f"KIS token response has no access_token: {token}")
        self.access_token = access_token
        self._save_token(access_token, int(token.get("expires_in") or 86400))
        return access_token

    def _headers(self, tr_id: str) -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.ensure_token()}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        url = f"{self.config.base_url}{path}?{query}"
        request = urllib.request.Request(url, headers=self._headers(tr_id), method="GET")
        return self._open_json(request)

    @staticmethod
    def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        return KisClient._open_json(request)

    @staticmethod
    def _open_json(request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise KisApiError(f"KIS HTTP {error.code}: {body}") from error

    def inquire_price(self, code: str, market_div_code: str = "J") -> dict[str, Any]:
        return self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": market_div_code, "FID_INPUT_ISCD": code.zfill(6)},
        )

    def daily_chart(self, code: str, start: str, end: str, period: str = "D") -> dict[str, Any]:
        return self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code.zfill(6),
                "FID_INPUT_DATE_1": start,
                "FID_INPUT_DATE_2": end,
                "FID_PERIOD_DIV_CODE": period,
                "FID_ORG_ADJ_PRC": "0",
            },
        )

    def volume_rank(self, market: str = "J") -> dict[str, Any]:
        return self._get(
            "/uapi/domestic-stock/v1/quotations/volume-rank",
            "FHPST01710000",
            {
                "FID_COND_MRKT_DIV_CODE": market,
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "000000",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_INPUT_DATE_1": "",
            },
        )

    def inquire_investor(self, code: str, market_div_code: str = "J") -> dict[str, Any]:
        return self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {"FID_COND_MRKT_DIV_CODE": market_div_code, "FID_INPUT_ISCD": code.zfill(6)},
        )

    def investor_trend_estimate(self, code: str) -> dict[str, Any]:
        return self._get(
            "/uapi/domestic-stock/v1/quotations/investor-trend-estimate",
            "HHPTJ04160200",
            {"MKSC_SHRN_ISCD": code.zfill(6)},
        )

    def foreign_institution_total(
        self,
        investor: str = "0",
        market_code: str = "0000",
        sort_by_amount: bool = True,
        net_buy: bool = True,
    ) -> dict[str, Any]:
        return self._get(
            "/uapi/domestic-stock/v1/quotations/foreign-institution-total",
            "FHPTJ04400000",
            {
                "FID_COND_MRKT_DIV_CODE": "V",
                "FID_COND_SCR_DIV_CODE": "16449",
                "FID_INPUT_ISCD": market_code,
                "FID_DIV_CLS_CODE": "1" if sort_by_amount else "0",
                "FID_RANK_SORT_CLS_CODE": "0" if net_buy else "1",
                "FID_ETC_CLS_CODE": investor,
            },
        )


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
