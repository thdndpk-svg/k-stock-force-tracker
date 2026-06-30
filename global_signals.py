from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from config import DATA_DIR


CACHE_PATH = DATA_DIR / "global_signals.json"
SIGNALS = {
    "kospi": ("코스피", "^KS11"),
    "kosdaq": ("코스닥", "^KQ11"),
    "gold": ("금 선물", "GC=F"),
    "nasdaq": ("나스닥", "^IXIC"),
    "sp500": ("S&P500", "^GSPC"),
    "sox": ("필라델피아 반도체", "^SOX"),
    "nvidia": ("엔비디아", "NVDA"),
    "tesla": ("테슬라", "TSLA"),
    "bio": ("미국 바이오 ETF", "XBI"),
    "finance": ("미국 금융 ETF", "XLF"),
    "defense": ("미국 방산 ETF", "ITA"),
    "oil": ("WTI 원유", "CL=F"),
    "usdkrw": ("달러/원", "KRW=X"),
    "china": ("중국 대형주 ETF", "FXI"),
    "lithium": ("리튬/배터리 ETF", "LIT"),
}


def _chart_url(symbol: str) -> str:
    encoded = urllib.parse.quote(symbol, safe="")
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"


def fetch_symbol(symbol: str) -> dict[str, float | str]:
    request = urllib.request.Request(_chart_url(symbol), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    result = (data.get("chart", {}).get("result") or [{}])[0]
    closes = [value for value in result.get("indicators", {}).get("quote", [{}])[0].get("close", []) if value]
    if len(closes) < 2:
        raise ValueError(f"not enough closes for {symbol}")
    prev_close = float(closes[-2])
    close = float(closes[-1])
    change_pct = (close / prev_close - 1.0) * 100.0 if prev_close else 0.0
    return {"symbol": symbol, "close": close, "prev_close": prev_close, "change_pct": round(change_pct, 2)}


def fetch_global_signals() -> dict[str, Any]:
    payload: dict[str, Any] = {"updated_at": int(time.time()), "signals": {}}
    for key, (label, symbol) in SIGNALS.items():
        try:
            signal = fetch_symbol(symbol)
            signal["label"] = label
            payload["signals"][key] = signal
        except Exception as error:
            payload["signals"][key] = {"label": label, "symbol": symbol, "change_pct": 0.0, "error": str(error)}
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_global_signals(max_age_seconds: int = 3600 * 12) -> dict[str, Any]:
    if CACHE_PATH.exists():
        try:
            payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if int(time.time()) - int(payload.get("updated_at", 0)) <= max_age_seconds:
                return payload
        except (ValueError, json.JSONDecodeError):
            pass
    return {"updated_at": 0, "signals": {key: {"label": label, "symbol": symbol, "change_pct": 0.0} for key, (label, symbol) in SIGNALS.items()}}
