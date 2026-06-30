#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import replace
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import APP_DIR, DATA_DIR, KisConfig
from data_loader import discover_investor_csvs, discover_market_csv, load_history_dir, load_market_snapshots, merge_krx_investor_csvs
from config import get_kis_config
from global_signals import fetch_global_signals, load_global_signals
from kis_client import KisApiError, KisClient, save_json
from kis_supply import aggregate_supply_rows, load_market_csv_rows, market_rows_from_price_payload, market_rows_from_supply_rows, market_rows_from_volume_rank, merge_market_rows, normalize_live_rows, save_market_csv, save_supply_csv
from krx_downloader import fetch_krx_bundle
from paper_trading import DEFAULT_CASH, buy as paper_buy, buy_quantity as paper_buy_quantity, evaluate as evaluate_paper, load_portfolio, new_portfolio, save_portfolio, sell as paper_sell
from price_normalizer import normalize_history_to_snapshots, price_unit_factor
from professional_analysis import build_professional_view
from scoring import ForceTracker


PAPER_PORTFOLIO_PATH = DATA_DIR / "paper_portfolio.json"
PUBLIC_MOBILE_DATA_URL = os.environ.get(
    "K_STOCK_PUBLIC_DATA_URL",
    "https://thdndpk-svg.github.io/k-stock-force-tracker/data.json",
)


def _number(value, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _parse_payload_date(payload: dict[str, object]) -> date:
    generated_at = str(payload.get("generatedAt") or "")
    if generated_at:
        try:
            return datetime.fromisoformat(generated_at).date()
        except ValueError:
            pass
    return date.today()


def _load_public_mobile_payload() -> dict[str, object]:
    request = urllib.request.Request(
        PUBLIC_MOBILE_DATA_URL,
        headers={"User-Agent": "KStockForceTracker/1.0"},
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        return json.loads(response.read().decode("utf-8"))


def _apply_public_quote_overrides(
    snapshots,
    payload: dict[str, object],
) -> tuple[list, dict[str, object]]:
    items = payload.get("items", [])
    if not isinstance(items, list):
        return snapshots, {"quoteSource": "로컬 CSV", "quoteUpdatedAt": "", "quoteOverrideCount": 0}
    quote_by_code = {
        str(item.get("code", "")).zfill(6): item
        for item in items
        if isinstance(item, dict) and str(item.get("code", "")).strip()
    }
    payload_date = _parse_payload_date(payload)
    updated: list = []
    override_count = 0
    for snapshot in snapshots:
        quote = quote_by_code.get(snapshot.code)
        close = _number(quote.get("close") if quote else None)
        if not quote or close <= 0:
            updated.append(snapshot)
            continue
        chart = quote.get("chart", [])
        last_bar = chart[-1] if isinstance(chart, list) and chart and isinstance(chart[-1], dict) else {}
        chart_date = str(last_bar.get("date") or "")
        try:
            trade_date = date.fromisoformat(chart_date)
        except ValueError:
            trade_date = payload_date
        change_rate = _number(quote.get("changeRate"), snapshot.change_rate)
        prev_close = close / (1.0 + change_rate / 100.0) if change_rate > -99 else snapshot.prev_close
        volume = _number(last_bar.get("volume"), snapshot.volume)
        trading_value = _number(quote.get("value"), close * volume)
        updated.append(
            replace(
                snapshot,
                trade_date=trade_date,
                close=close,
                prev_close=prev_close,
                open=_number(last_bar.get("open"), close),
                high=_number(last_bar.get("high"), close),
                low=_number(last_bar.get("low"), close),
                volume=volume,
                trading_value=trading_value if trading_value > 0 else close * volume,
                change_rate=change_rate,
            )
        )
        override_count += 1
    return updated, {
        "quoteSource": "KIS 최신(휴대폰 배포)",
        "quoteUpdatedAt": str(payload.get("generatedAt") or ""),
        "quoteOverrideCount": override_count,
    }


def synthetic_chart_points(item, count: int = 36) -> list[dict[str, float | str]]:
    start = item.prev_close if item.prev_close > 0 else item.close / (1 + item.change_rate / 100.0) if item.change_rate > -99 else item.close
    end = item.close
    span = end - start
    volume_base = item.volume if item.volume > 0 else 1.0
    points: list[dict[str, float | str]] = []
    for idx in range(count):
        t = idx / max(1, count - 1)
        smooth_t = t * t * (3 - 2 * t)
        close = start + span * smooth_t
        if idx == count - 1:
            close = end
        open_price = float(points[-1]["close"] if points else start)
        wick = max(abs(close - open_price) * 0.32, end * 0.004)
        high = max(open_price, close) + wick
        low = max(0.0, min(open_price, close) - wick)
        if idx == count - 1:
            open_price = item.open if item.open > 0 else open_price
            high = max(item.high, open_price, close) if item.high > 0 else high
            low = min(item.low, open_price, close) if item.low > 0 else low
        trade_date = item.trade_date - timedelta(days=count - idx - 1)
        points.append(
            {
                "date": trade_date.isoformat(),
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": round(volume_base * (0.62 + 0.38 * t)),
                "synthetic": True,
            }
        )
    return points


def chart_points(item, history) -> list[dict[str, float | str]]:
    bars = history.get(item.code, [])[-119:]
    points = [
        {
            "date": bar.trade_date.isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]
    if len(points) < 5:
        return synthetic_chart_points(item)
    if points[-1]["date"] != item.trade_date.isoformat():
        points.append({"date": item.trade_date.isoformat(), "open": item.open, "high": item.high, "low": item.low, "close": item.close, "volume": item.volume})
    return points[-120:]


def score_item_payload(item, idx: int, snapshots_by_code, history, history_scale_by_code) -> dict[str, object]:
    snapshot = snapshots_by_code.get(item.code)
    return {
        "rank": idx,
        "code": item.code,
        "name": item.name,
        "market": item.market,
        "sector": item.sector,
        "theme": item.theme,
        "grade": item.grade,
        "recommendation": item.recommendation,
        "tradeAction": item.trade_action,
        "tradeReason": item.trade_reason,
        "risk": item.risk_label,
        "score": item.score,
        "discoveryScore": item.discovery_score,
        "close": item.close,
        "changeRate": item.change_rate,
        "value": item.trading_value,
        "volumeRatio": item.volume_ratio,
        "flowRatio": item.flow_ratio,
        "usImpact": item.us_impact,
        "issueScore": item.issue_score,
        "bottomScore": item.bottom_score,
        "bottomStage": item.bottom_stage,
        "bottomSupport": item.bottom_support,
        "bottomLongLine": item.bottom_long_line,
        "bottomTouchCount": item.bottom_touch_count,
        "bottomReasons": item.bottom_reasons,
        "bottomWarnings": item.bottom_warnings,
        "historyScale": history_scale_by_code.get(item.code, 1.0),
        "foreign": item.foreign_net_value,
        "institution": item.institution_net_value,
        "foreignAvailable": item.foreign_net_available,
        "institutionAvailable": item.institution_net_available,
        "tags": item.tags,
        "reasons": item.reasons,
        "penalties": item.penalties,
        "chart": chart_points(snapshot, history) if snapshot else [],
        "pro": build_professional_view(item, history.get(item.code, [])),
    }


def analyze_payload(use_public_fallback: bool = False) -> dict[str, object]:
    market_csv = discover_market_csv(DATA_DIR)
    investor_paths = discover_investor_csvs(DATA_DIR)
    snapshots = load_market_snapshots(market_csv)
    snapshots = merge_krx_investor_csvs(snapshots, investor_paths) if investor_paths else snapshots
    quote_meta = {"quoteSource": "로컬 CSV", "quoteUpdatedAt": "", "quoteOverrideCount": 0}
    if use_public_fallback and get_kis_config() is None:
        try:
            public_payload = _load_public_mobile_payload()
            public_date = _parse_payload_date(public_payload)
            local_date = max((item.trade_date for item in snapshots), default=date.min)
            if public_date >= local_date:
                snapshots, quote_meta = _apply_public_quote_overrides(snapshots, public_payload)
        except Exception as error:
            quote_meta = {"quoteSource": "로컬 CSV", "quoteUpdatedAt": "", "quoteOverrideCount": 0, "quoteError": str(error)}
    raw_history = load_history_dir(DATA_DIR / "history")
    history_scale_by_code = {
        item.code: price_unit_factor(item.close, raw_history.get(item.code, [])[-1].close if raw_history.get(item.code) else 0.0)
        for item in snapshots
    }
    history = normalize_history_to_snapshots(snapshots, raw_history)
    signal_payload = load_global_signals()
    signals = signal_payload.get("signals", {})
    all_results = ForceTracker(snapshots, history, signals).score_all(limit=max(40, len(snapshots)))
    results = all_results[:40]
    bottom_results = sorted(
        [item for item in all_results if item.bottom_score >= 60.0],
        key=lambda item: (item.bottom_score, item.discovery_score),
        reverse=True,
    )[:8]
    result_codes = {item.code for item in results}
    results.extend([item for item in bottom_results if item.code not in result_codes])
    snapshots_by_code = {item.code: item for item in snapshots}
    sector_groups: dict[str, list[dict[str, object]]] = {}
    for item in results:
        sector_groups.setdefault(item.sector, []).append(
            {
                "code": item.code,
                "name": item.name,
                "score": item.discovery_score,
                "recommendation": item.recommendation,
                "theme": item.theme,
            }
        )
    return {
        "count": len(results),
        "marketCsv": str(market_csv.name),
        "investorCsv": ", ".join(path.name for path in investor_paths),
        "quoteSource": quote_meta.get("quoteSource", "로컬 CSV"),
        "quoteUpdatedAt": quote_meta.get("quoteUpdatedAt", ""),
        "quoteOverrideCount": quote_meta.get("quoteOverrideCount", 0),
        "quoteError": quote_meta.get("quoteError", ""),
        "signals": signals,
        "signalUpdatedAt": signal_payload.get("updated_at", 0),
        "bottomCandidates": [
            {
                "code": item.code,
                "name": item.name,
                "score": item.bottom_score,
                "tradeAction": item.trade_action,
                "tradeReason": item.trade_reason,
                "stage": item.bottom_stage,
                "support": item.bottom_support,
                "longLine": item.bottom_long_line,
                "touchCount": item.bottom_touch_count,
                "reasons": item.bottom_reasons,
                "warnings": item.bottom_warnings,
                "discoveryScore": item.discovery_score,
                "historyScale": history_scale_by_code.get(item.code, 1.0),
                "chart": chart_points(snapshots_by_code[item.code], history) if item.code in snapshots_by_code else [],
            }
            for item in bottom_results
        ],
        "sectors": [
            {"sector": sector, "items": items[:3], "topScore": items[0]["score"] if items else 0}
            for sector, items in sorted(sector_groups.items(), key=lambda pair: pair[1][0]["score"], reverse=True)
            if sector != "기타"
        ][:8],
        "items": [score_item_payload(item, idx, snapshots_by_code, history, history_scale_by_code) for idx, item in enumerate(results, 1)],
        "searchItems": [
            score_item_payload(item, idx, snapshots_by_code, history, history_scale_by_code)
            for idx, item in enumerate(all_results, 1)
        ],
    }


def current_quote_map() -> dict[str, dict[str, object]]:
    payload = analyze_payload(use_public_fallback=True)
    quotes = {}
    for item in [*payload.get("searchItems", []), *payload.get("items", [])]:
        quotes[str(item.get("code", "")).zfill(6)] = item
    return quotes


def paper_payload() -> dict[str, object]:
    return evaluate_paper(load_portfolio(PAPER_PORTFOLIO_PATH), current_quote_map())


def with_kis_retry(call, retries: int = 4):
    for attempt in range(retries + 1):
        try:
            return call()
        except KisApiError as error:
            if "EGW00201" not in str(error) or attempt >= retries:
                raise
            time.sleep(1.2 + attempt * 0.8)


ENV_PATH = APP_DIR / ".env"
KIS_ENV_KEYS = ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO", "KIS_PRODUCT_CODE", "KIS_VIRTUAL"]


def _read_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _write_env_values(updates: dict[str, str]) -> None:
    values = _read_env_values()
    for key, value in updates.items():
        if key in KIS_ENV_KEYS and value is not None:
            values[key] = str(value).strip()
    values.setdefault("KIS_PRODUCT_CODE", "01")
    values.setdefault("KIS_VIRTUAL", "false")
    ordered_keys = KIS_ENV_KEYS + [key for key in values if key not in KIS_ENV_KEYS]
    ENV_PATH.write_text("\n".join(f"{key}={values.get(key, '')}" for key in ordered_keys) + "\n", encoding="utf-8")
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass
    for key in KIS_ENV_KEYS:
        os.environ[key] = values.get(key, "")


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def kis_status_payload() -> dict[str, object]:
    env_values = _read_env_values()
    config = get_kis_config()
    return {
        "ok": True,
        "configured": config is not None,
        "appKey": _mask_secret(env_values.get("KIS_APP_KEY", "")),
        "virtual": config.virtual if config else env_values.get("KIS_VIRTUAL", "false").lower() in {"1", "true", "yes", "y"},
        "accountNo": _mask_secret(env_values.get("KIS_ACCOUNT_NO", "")),
        "productCode": env_values.get("KIS_PRODUCT_CODE", "01") or "01",
        "mode": "모의투자 서버" if config and config.virtual else "실전투자 서버",
    }


def save_kis_settings(payload: dict[str, object]) -> dict[str, object]:
    app_key = str(payload.get("appKey") or "").strip()
    app_secret = str(payload.get("appSecret") or "").strip()
    if not app_key or not app_secret:
        raise RuntimeError("KIS APP KEY와 APP SECRET을 모두 입력해야 합니다.")
    account_no = str(payload.get("accountNo") or "").strip()
    product_code = str(payload.get("productCode") or "01").strip() or "01"
    virtual = str(payload.get("virtual", "false")).lower() in {"1", "true", "yes", "y"}
    test_config = KisConfig(app_key=app_key, app_secret=app_secret, account_no=account_no, product_code=product_code, virtual=virtual)
    test_payload = with_kis_retry(lambda: KisClient(test_config).inquire_price("000660"), retries=1)
    test_rows = market_rows_from_price_payload(test_payload, fallback_code="000660", fallback_name="SK하이닉스")
    _write_env_values(
        {
            "KIS_APP_KEY": app_key,
            "KIS_APP_SECRET": app_secret,
            "KIS_ACCOUNT_NO": account_no,
            "KIS_PRODUCT_CODE": product_code,
            "KIS_VIRTUAL": "true" if virtual else "false",
        }
    )
    return kis_status_payload() | {"tested": True, "sampleQuote": test_rows[0] if test_rows else {}}


def refresh_kis_quotes(codes: list[str]) -> dict[str, object]:
    config = get_kis_config()
    if config is None:
        raise RuntimeError("KIS APP KEY/SECRET을 먼저 저장해야 합니다.")
    clean_codes = []
    for code in codes:
        clean = str(code or "").strip().zfill(6)
        if clean.strip("0") and clean not in clean_codes:
            clean_codes.append(clean)
    if not clean_codes:
        current = analyze_payload(use_public_fallback=False)
        clean_codes = [str(item.get("code", "")).zfill(6) for item in current.get("items", [])[:40]]
    clean_codes = clean_codes[:60]
    client = KisClient(config)
    existing_rows = load_market_csv_rows(DATA_DIR / "kis_market.csv")
    existing_by_code = {str(row.get("code", "")).zfill(6): row for row in existing_rows}
    price_rows = []
    for code in clean_codes:
        fallback = existing_by_code.get(code, {})
        payload = with_kis_retry(lambda code=code: client.inquire_price(code))
        save_json(DATA_DIR / "kis_raw" / f"live_price_{code}.json", payload)
        price_rows.extend(
            market_rows_from_price_payload(
                payload,
                fallback_code=code,
                fallback_name=str(fallback.get("name") or code),
            )
        )
        time.sleep(0.25)
    market_path = save_market_csv(
        DATA_DIR / "kis_market.csv",
        merge_market_rows(existing_rows + price_rows, prefer_latest=True),
    )
    sample = next((row for row in price_rows if str(row.get("code", "")).zfill(6) == "000660"), None)
    return {
        "ok": True,
        "market": market_path.name,
        "priceRows": len(price_rows),
        "codes": clean_codes,
        "updatedAt": int(time.time()),
        "sampleQuote": sample or {},
    }


INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>윤희야 대박나라 화이팅</title>
<style>
:root {
  color-scheme: light;
  --bg: #f4f6f8;
  --ink: #18212f;
  --muted: #697586;
  --line: #d8dee8;
  --panel: #ffffff;
  --blue: #1f6feb;
  --green: #12805c;
  --red: #d1242f;
  --amber: #9a6700;
  --purple: #6f42c1;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; background: var(--bg); color: var(--ink); }
header { background: #0f172a; color: white; padding: 22px 28px; border-bottom: 4px solid #2dd4bf; }
h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
main { max-width: 1220px; margin: 0 auto; padding: 24px; }
.toolbar { display: flex; gap: 12px; align-items: center; justify-content: space-between; margin-bottom: 18px; }
button { border: 0; border-radius: 8px; background: var(--blue); color: white; padding: 10px 14px; font-weight: 700; cursor: pointer; }
button.live-on { background: #dc2626; }
.note { color: var(--muted); font-size: 13px; }
.kis-panel { display:grid; grid-template-columns: 260px 1fr; gap:12px; align-items:center; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; margin-bottom:16px; }
.kis-panel h2 { margin:0 0 5px; font-size:16px; }
.kis-form { display:flex; gap:8px; align-items:center; flex-wrap:wrap; justify-content:flex-end; }
.kis-form input { min-width:160px; }
.kis-form select { border:1px solid var(--line); border-radius:8px; padding:9px 10px; font:inherit; background:white; }
.kis-form .status { min-width:190px; text-align:right; }
.search-panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; margin-bottom:16px; display:grid; grid-template-columns: 1fr auto auto; gap:10px; align-items:center; }
.search-results { grid-column:1 / -1; display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:8px; }
.search-card { border:1px solid var(--line); border-radius:8px; padding:10px; background:#f8fafc; cursor:pointer; }
.search-card b { display:block; font-size:14px; }
.search-card span { display:block; margin-top:4px; color:var(--muted); font-size:12px; line-height:1.35; }
.live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; margin-right: 5px; vertical-align: 1px; }
.live-dot.on { background: #ef4444; box-shadow: 0 0 0 4px rgba(239, 68, 68, .15); }
.grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.metric { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }
.metric b { display: block; font-size: 22px; margin-top: 6px; }
.insight-grid { display: grid; grid-template-columns: 1.2fr .8fr; gap: 14px; margin-bottom: 16px; }
.insight-panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }
.insight-panel h2 { margin: 0 0 10px; font-size: 16px; }
.bottom-alert { display:none; background:#fff7ed; border:1px solid #fed7aa; border-left:5px solid #ea580c; border-radius:8px; padding:14px; margin-bottom:16px; }
.bottom-alert.open { display:block; }
.bottom-alert h2 { margin:0 0 8px; font-size:17px; }
.bottom-list { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:10px; }
.bottom-card { border:1px solid #fed7aa; border-radius:8px; background:#fffbeb; padding:10px; cursor:pointer; }
.bottom-card b { display:block; }
.bottom-card span { display:block; color:#7c2d12; font-size:12px; margin-top:4px; line-height:1.45; }
.sector-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.sector-card { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #f8fafc; }
.sector-card b { display:block; font-size: 15px; margin-bottom: 6px; }
.sector-card span { display:block; color: var(--muted); font-size: 12px; line-height: 1.45; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.signal-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.signal { display:flex; justify-content:space-between; gap:8px; border:1px solid var(--line); border-radius:8px; padding:8px; background:#f8fafc; font-size:12px; }
.signal strong { font-size:13px; }
.signal .pos { color: var(--red); font-weight:800; }
.signal .neg { color: var(--blue); font-weight:800; }
.paper-panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; margin-bottom: 16px; }
.paper-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:12px; }
.paper-head h2 { margin:0; font-size:16px; }
.paper-controls { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
input { border:1px solid var(--line); border-radius:8px; padding:9px 10px; font:inherit; min-width:110px; }
.mini-btn { padding:7px 10px; border-radius:8px; font-size:12px; }
.mini-btn.sell { background: var(--red); }
.paper-grid { display:grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap:10px; margin-bottom:12px; }
.paper-metric { border:1px solid var(--line); border-radius:8px; padding:10px; background:#f8fafc; }
.paper-metric span { display:block; color:var(--muted); font-size:11px; }
.paper-metric b { display:block; margin-top:4px; font-size:16px; }
.paper-body { display:grid; grid-template-columns: 1.25fr .75fr; gap:12px; }
.paper-table { width:100%; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
.paper-table h3 { margin:0; padding:9px 10px; font-size:13px; background:#edf2f7; border-bottom:1px solid var(--line); }
.paper-table table { font-size:12px; }
.holding-row { cursor:pointer; }
.holding-row:hover { background:#f8fafc; }
.paper-empty { padding:18px 10px; color:var(--muted); font-size:12px; text-align:center; }
.table { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 11px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { background: #edf2f7; font-size: 12px; color: #42526b; }
tr.stock-row { cursor: pointer; }
tr.stock-row:hover { background: #f8fafc; }
.score { font-weight: 800; }
.tag { display: inline-block; border-radius: 999px; padding: 3px 8px; margin: 2px 3px 2px 0; background: #e8f2ff; color: #0b5cad; font-size: 12px; }
.grade { font-weight: 800; }
.rec { display: inline-flex; min-width: 72px; justify-content: center; border-radius: 999px; padding: 5px 9px; color: white; font-size: 12px; font-weight: 800; }
.rec-strong { background: var(--red); }
.rec-buy { background: #cf5b00; }
.rec-watch { background: var(--green); }
.rec-neutral { background: #64748b; }
.rec-risk { background: #111827; }
.action { display: inline-flex; min-width: 78px; justify-content: center; border-radius: 8px; padding: 5px 9px; color: white; font-size: 12px; font-weight: 900; }
.action-buy { background: var(--green); }
.action-sell { background: var(--red); }
.action-hold { background: #64748b; }
.up { color: var(--red); font-weight: 700; }
.down { color: var(--blue); font-weight: 700; }
.flow.pos { color: var(--red); font-weight: 700; }
.flow.neg { color: var(--blue); font-weight: 700; }
.flow.none { color: var(--muted); }
.spark { width: 128px; height: 46px; display: block; cursor: pointer; }
.reasons { color: #334155; line-height: 1.45; max-width: 360px; }
.penalty { color: var(--amber); }
.modal { position: fixed; inset: 0; display: none; align-items: center; justify-content: center; padding: 24px; background: rgba(15, 23, 42, .52); z-index: 10; }
.modal.open { display: flex; }
.dialog { width: min(1120px, 96vw); max-height: 92vh; overflow: auto; background: white; border-radius: 10px; box-shadow: 0 24px 80px rgba(15,23,42,.35); }
.dialog-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; padding: 20px 22px; border-bottom: 1px solid var(--line); }
.dialog-head h2 { margin: 0 0 6px; font-size: 22px; }
.icon-btn { width: 34px; height: 34px; padding: 0; border-radius: 50%; background: #e5e7eb; color: #111827; }
.detail-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; padding: 16px 22px 4px; }
.detail-card { border: 1px solid var(--line); border-radius: 8px; padding: 11px; background: #f8fafc; }
.detail-card span { display: block; color: var(--muted); font-size: 12px; }
.detail-card b { display: block; margin-top: 5px; font-size: 17px; }
.chart-wrap { padding: 14px 22px 8px; }
.big-chart { width: 100%; height: 360px; border: 1px solid var(--line); border-radius: 8px; background: #fbfdff; }
.detail-body { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; padding: 10px 22px 22px; }
.detail-box { border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
.detail-box h3 { margin: 0 0 8px; font-size: 14px; }
.detail-list { margin: 0; padding-left: 18px; line-height: 1.55; color: #334155; }
.pro-strip { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; padding: 10px 22px 0; }
.pro-tile { border: 1px solid var(--line); border-radius: 8px; background: #fff; padding: 10px; }
.pro-tile span { display: block; color: var(--muted); font-size: 11px; }
.pro-tile b { display: block; margin-top: 4px; font-size: 15px; }
.pro-tile.positive { border-color: #9ae6b4; background: #f0fff4; }
.pro-tile.warning { border-color: #fecaca; background: #fff5f5; }
.pro-box { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfdff; }
.pro-box h3 { margin: 0 0 8px; font-size: 14px; }
.pro-note { padding: 0 22px 14px; color: #64748b; font-size: 12px; line-height: 1.45; }
.paper-trade { margin: 10px 22px 0; border:1px solid var(--line); border-radius:8px; padding:12px; background:#f8fafc; display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap; }
.paper-trade b { display:block; margin-bottom:4px; }
.paper-trade-actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
@media (max-width: 860px) {
  .grid, .detail-grid, .detail-body, .insight-grid, .sector-strip, .signal-list, .pro-strip, .paper-grid, .paper-body { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .kis-panel { grid-template-columns: 1fr; }
  .kis-form { justify-content:flex-start; }
  .search-panel, .search-results { grid-template-columns: 1fr; }
  .bottom-list { grid-template-columns: 1fr; }
  table { font-size: 12px; }
  th:nth-child(7), td:nth-child(7), th:nth-child(8), td:nth-child(8) { display: none; }
}
</style>
</head>
<body>
<header><h1>윤희야 대박나라 화이팅</h1></header>
<main>
  <div class="toolbar">
    <div>
      <button id="refresh">분석 새로고침</button>
      <button id="fetchKis" style="background:#6f42c1">KIS 수급 자동수집</button>
      <button id="liveToggle">실시간 OFF</button>
      <button id="fetchGlobal" style="background:#0f766e">미국 흐름 업데이트</button>
      <button id="fetchKrx" style="background:#12805c">KRX 자동수집</button>
      <span class="note">맥 전문가판 = 실시간 수급 + 차트 지표 + 손익비 플랜</span>
    </div>
    <span class="note" id="stamp"><span class="live-dot" id="liveDot"></span><span id="liveStatus">대기</span></span>
  </div>
  <section class="kis-panel">
    <div>
      <h2>KIS 한국투자증권 연결</h2>
      <div class="note" id="kisStatus">연결 상태 확인 중</div>
    </div>
    <div class="kis-form">
      <input id="kisAppKey" type="password" autocomplete="off" placeholder="APP KEY">
      <input id="kisAppSecret" type="password" autocomplete="off" placeholder="APP SECRET">
      <input id="kisAccountNo" autocomplete="off" placeholder="계좌번호">
      <select id="kisVirtual">
        <option value="false">실전투자 서버</option>
        <option value="true">모의투자 서버</option>
      </select>
      <button id="kisSave" style="background:#0f766e">키 저장/확인</button>
      <button id="kisRefresh" style="background:#b45309">현재가 KIS 갱신</button>
    </div>
  </section>
  <section class="search-panel">
    <input id="stockSearch" autocomplete="off" placeholder="종목명 또는 종목코드 검색">
    <button id="stockSearchBtn">검색/분석</button>
    <button id="stockSearchClear" style="background:#64748b">전체 보기</button>
    <div class="search-results" id="searchResults"></div>
  </section>
  <section class="grid">
    <div class="metric">분석 종목<b id="m-count">-</b></div>
    <div class="metric">발굴 강함<b id="m-strong">-</b></div>
    <div class="metric">섹터 후보<b id="m-sector">-</b></div>
    <div class="metric">수급 집중<b id="m-flow">-</b></div>
    <div class="metric">실전 후보<b id="m-pro">-</b></div>
    <div class="metric">위험 표시<b id="m-risk">-</b></div>
  </section>
  <section class="paper-panel">
    <div class="paper-head">
      <div>
        <h2>맥 모의투자 계좌</h2>
        <div class="note">가상머니로 앱 신호를 검증합니다. 데이터는 이 Mac의 로컬 파일에만 저장됩니다.</div>
      </div>
      <div class="paper-controls">
        <input id="paperInitialCash" type="number" min="100000" step="100000" value="10000000">
        <button class="mini-btn" id="paperReset">예수금 재설정</button>
        <button class="mini-btn" id="paperRefresh">계좌 새로고침</button>
      </div>
    </div>
    <div class="paper-grid">
      <div class="paper-metric"><span>총자산</span><b id="p-equity">-</b></div>
      <div class="paper-metric"><span>예수금</span><b id="p-cash">-</b></div>
      <div class="paper-metric"><span>평가손익</span><b id="p-unreal">-</b></div>
      <div class="paper-metric"><span>총수익률</span><b id="p-return">-</b></div>
      <div class="paper-metric"><span>매도 승률</span><b id="p-win">-</b></div>
    </div>
    <div class="paper-body">
      <div class="paper-table">
        <h3>보유 종목</h3>
        <div id="paperHoldings" class="paper-empty">보유 종목 없음</div>
      </div>
      <div class="paper-table">
        <h3>최근 거래</h3>
        <div id="paperTrades" class="paper-empty">거래 내역 없음</div>
      </div>
    </div>
  </section>
  <section class="bottom-alert" id="bottomAlert">
    <h2>바닥매집 알림</h2>
    <div class="bottom-list" id="bottomCards"></div>
  </section>
  <section class="insight-grid">
    <div class="insight-panel">
      <h2>섹터별 유망 후보</h2>
      <div class="sector-strip" id="sectorCards"></div>
    </div>
    <div class="insight-panel">
      <h2>미국/글로벌 영향</h2>
      <div class="signal-list" id="signalCards"></div>
    </div>
  </section>
  <section class="table">
    <table>
      <thead>
        <tr><th>순위</th><th>종목</th><th>섹터</th><th>추천</th><th>타이밍</th><th>셋업</th><th>발굴</th><th>등락</th><th>그래프</th><th>수급집중</th><th>미국</th><th>신호</th><th>근거</th></tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </section>
</main>
<section class="modal" id="detailModal">
  <div class="dialog">
    <div class="dialog-head">
      <div>
        <h2 id="d-title">종목 상세</h2>
        <div class="note" id="d-sub"></div>
      </div>
      <button class="icon-btn" id="closeDetail">×</button>
    </div>
    <div class="detail-grid">
      <div class="detail-card"><span>추천</span><b id="d-rec">-</b></div>
      <div class="detail-card"><span>매매 타이밍</span><b id="d-action">-</b></div>
      <div class="detail-card"><span>현가격</span><b id="d-price">-</b></div>
      <div class="detail-card"><span>발굴 점수</span><b id="d-score">-</b></div>
      <div class="detail-card"><span>위험</span><b id="d-risk">-</b></div>
      <div class="detail-card"><span>거래량</span><b id="d-vol">-</b></div>
      <div class="detail-card"><span>외국인 순매수</span><b id="d-foreign">-</b></div>
      <div class="detail-card"><span>기관 순매수</span><b id="d-inst">-</b></div>
      <div class="detail-card"><span>수급 집중</span><b id="d-flow">-</b></div>
      <div class="detail-card"><span>미국 영향</span><b id="d-us">-</b></div>
      <div class="detail-card"><span>바닥매집</span><b id="d-bottom">-</b></div>
      <div class="detail-card"><span>차트단위</span><b id="d-price-scale">-</b></div>
    </div>
    <div class="chart-wrap"><div class="big-chart" id="d-chart"></div></div>
    <div class="paper-trade">
      <div>
        <b>모의투자 주문</b>
        <span class="note" id="d-paper-status">현시세 기준으로 가상 체결합니다.</span>
      </div>
      <div class="paper-trade-actions">
        <input id="d-paper-amount" type="number" min="10000" step="10000" value="1000000" title="매수 금액">
        <button class="mini-btn" id="d-paper-buy">금액 매수</button>
        <input id="d-paper-buy-qty" type="number" min="1" step="1" placeholder="매수 수량" title="매수 수량">
        <button class="mini-btn" id="d-paper-buy-qty-btn">수량 매수</button>
        <input id="d-paper-qty" type="number" min="1" step="1" placeholder="수량" title="매도 수량">
        <button class="mini-btn sell" id="d-paper-sell">수량 매도</button>
        <button class="mini-btn sell" id="d-paper-sell-all">전량 매도</button>
      </div>
    </div>
    <div class="pro-strip" id="d-pro-strip"></div>
    <div class="pro-note">이 화면은 투자 판단 보조도구입니다. 실제 주문 전에는 호가, 공시, 뉴스, 시장 급변, 본인 손절 기준을 반드시 다시 확인하세요.</div>
    <div class="detail-body">
      <div class="detail-box"><h3>상승 근거</h3><ul class="detail-list" id="d-reasons"></ul></div>
      <div class="detail-box"><h3>위험/주의</h3><ul class="detail-list" id="d-penalties"></ul></div>
      <div class="pro-box"><h3>전문가 체크리스트</h3><ul class="detail-list" id="d-pro-checks"></ul></div>
      <div class="pro-box"><h3>매매 플랜 경고</h3><ul class="detail-list" id="d-pro-warnings"></ul></div>
    </div>
  </div>
</section>
<script>
const fmt = new Intl.NumberFormat("ko-KR");
let currentItems = [];
let rankedItems = [];
let searchableItems = [];
let lastPayload = null;
let currentDetailCode = "";
let paperState = null;
let liveTimer = null;
let liveRunning = false;
let lastDataLabel = "";
let bottomAutoOpened = false;
const LIVE_INTERVAL_MS = 30000;
function money(v) {
  const n = Number(v || 0);
  if (Math.abs(n) >= 100000000) return `${fmt.format(Math.round(n / 100000000))}억`;
  if (Math.abs(n) >= 10000) return `${fmt.format(Math.round(n / 10000))}만`;
  return fmt.format(Math.round(n));
}
function signedMoney(v) {
  const n = Number(v || 0);
  const cls = n >= 0 ? "up" : "down";
  const sign = n >= 0 ? "+" : "-";
  return `<span class="${cls}">${sign}${money(Math.abs(n))}</span>`;
}
function signedPct(v) {
  const n = Number(v || 0);
  const cls = n >= 0 ? "up" : "down";
  return `<span class="${cls}">${n >= 0 ? "+" : ""}${n.toFixed(2)}%</span>`;
}
function flow(v, available) {
  if (!available) return `<span class="flow none">데이터 없음</span>`;
  const n = Number(v || 0);
  const cls = n >= 0 ? "pos" : "neg";
  return `<span class="flow ${cls}">${money(n)}</span>`;
}
function rate(v) {
  const n = Number(v || 0);
  const cls = n >= 0 ? "up" : "down";
  return `<span class="${cls}">${n.toFixed(2)}%</span>`;
}
function price(v) {
  const n = Number(v || 0);
  return n ? `${fmt.format(Math.round(n))}원` : "-";
}
function timeText(ts) {
  const n = Number(ts || 0);
  if (!n) return "-";
  return new Date(n * 1000).toLocaleString("ko-KR", { month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit" });
}
function recClass(label) {
  if (label === "강한매수") return "rec-strong";
  if (label === "매수") return "rec-buy";
  if (label === "관심") return "rec-watch";
  if (label === "위험") return "rec-risk";
  return "rec-neutral";
}
function actionClass(label) {
  if (label === "매수권장") return "action-buy";
  if (label === "매도") return "action-sell";
  return "action-hold";
}
function sparkline(points, width = 118, height = 42, big = false) {
  const values = (points || []).map(p => Number(p.close || 0)).filter(v => Number.isFinite(v));
  if (values.length < 2) return `<svg class="${big ? "big-chart" : "spark"}" viewBox="0 0 ${width} ${height}"></svg>`;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = big ? 18 : 4;
  const span = max - min || 1;
  const coords = values.map((v, i) => {
    const x = pad + i * ((width - pad * 2) / Math.max(1, values.length - 1));
    const y = height - pad - ((v - min) / span) * (height - pad * 2);
    return [x, y];
  });
  const line = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = values[values.length - 1];
  const first = values[0];
  const color = last >= first ? "#d1242f" : "#1f6feb";
  const circles = big ? coords.map(([x,y]) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.6" fill="${color}"/>`).join("") : "";
  return `<svg class="${big ? "big-chart" : "spark"}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="${big ? 3 : 2.2}" stroke-linecap="round" stroke-linejoin="round"/>
    ${circles}
  </svg>`;
}
function detailedChart(points, item = {}, width = 1040, height = 360) {
  const data = (points || []).filter(p => Number.isFinite(Number(p.close)));
  if (data.length < 2) return `<svg class="big-chart" viewBox="0 0 ${width} ${height}"></svg>`;
  const pro = item.pro || {};
  const indicators = pro.indicators || {};
  const prices = data.map(p => Number(p.close));
  const opens = data.map(p => Number(p.open || p.close));
  const highs = data.map(p => Number(p.high || p.close));
  const lows = data.map(p => Number(p.low || p.close));
  const volumes = data.map(p => Number(p.volume || 0));
  const ma = (arr, period) => arr.map((_, i) => {
    if (i + 1 < period) return null;
    const slice = arr.slice(i + 1 - period, i + 1);
    return slice.reduce((a, b) => a + b, 0) / period;
  });
  const ma5 = ma(prices, 5);
  const ma20 = ma(prices, 20);
  const ma60 = ma(prices, 60);
  const volMa5 = ma(volumes, 5);
  const support = Number(item.bottomSupport || 0);
  const longLine = Number(item.bottomLongLine || 0);
  const resistance = prices.length > 2 ? Math.max(...prices.slice(0, -1)) : 0;
  const bbUpper = Number(indicators.bbUpper || 0);
  const bbMiddle = Number(indicators.bbMiddle || 0);
  const bbLower = Number(indicators.bbLower || 0);
  const vwap20 = Number(indicators.vwap20 || 0);
  const entry = Number(pro.entry || 0);
  const stop = Number(pro.stop || 0);
  const target1 = Number(pro.target1 || 0);
  const target2 = Number(pro.target2 || 0);
  const levels = [support, longLine, resistance, bbUpper, bbMiddle, bbLower, vwap20, entry, stop, target1, target2].filter(v => Number.isFinite(v) && v > 0);
  const indicatorValues = prices.concat(highs, lows, ma5.filter(Boolean), ma20.filter(Boolean), ma60.filter(Boolean), levels);
  const min = Math.min(...indicatorValues);
  const max = Math.max(...indicatorValues);
  const vmax = Math.max(...volumes, 1);
  const left = 64, right = 22, top = 22, bottom = 56;
  const chartH = height - top - bottom;
  const priceH = chartH * .68;
  const volTop = top + priceH + 20;
  const volH = chartH * .24;
  const span = max - min || 1;
  const xFor = i => left + i * ((width - left - right) / Math.max(1, data.length - 1));
  const yFor = v => top + priceH - ((v - min) / span) * priceH;
  const coords = prices.map((v, i) => [xFor(i), yFor(v)]);
  const line = coords.map(([x,y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const lineFor = arr => arr.map((v, i) => v ? `${xFor(i).toFixed(1)},${yFor(v).toFixed(1)}` : "").filter(Boolean).join(" ");
  const pointsForConst = value => data.map((_, i) => `${xFor(i).toFixed(1)},${yFor(value).toFixed(1)}`).join(" ");
  const levelLine = (value, color, label, dash = "6 4") => {
    if (!Number.isFinite(value) || value <= 0) return "";
    const y = yFor(value);
    return `<line x1="${left}" y1="${y.toFixed(1)}" x2="${width-right}" y2="${y.toFixed(1)}" stroke="${color}" stroke-width="1.8" stroke-dasharray="${dash}"/>
      <text x="${width-right-4}" y="${(y - 5).toFixed(1)}" text-anchor="end" font-size="12" font-weight="800" fill="${color}">${label} ${fmt.format(Math.round(value))}</text>`;
  };
  const color = prices[prices.length - 1] >= prices[0] ? "#d1242f" : "#1f6feb";
  const candleWidth = Math.max(3, Math.min(11, (width - left - right) / data.length * .58));
  const candles = data.map((_, i) => {
    const up = prices[i] >= opens[i];
    const color = up ? "#d1242f" : "#1f6feb";
    const x = xFor(i);
    const highY = yFor(highs[i]);
    const lowY = yFor(lows[i]);
    const openY = yFor(opens[i]);
    const closeY = yFor(prices[i]);
    const bodyY = Math.min(openY, closeY);
    const bodyH = Math.max(2, Math.abs(openY - closeY));
    return `<line x1="${x.toFixed(1)}" y1="${highY.toFixed(1)}" x2="${x.toFixed(1)}" y2="${lowY.toFixed(1)}" stroke="${color}" stroke-width="1.2" opacity=".8"/>
      <rect x="${(x - candleWidth / 2).toFixed(1)}" y="${bodyY.toFixed(1)}" width="${candleWidth.toFixed(1)}" height="${bodyH.toFixed(1)}" fill="${color}" opacity=".72"/>`;
  }).join("");
  const bars = volumes.map((v, i) => {
    const x = xFor(i) - 4;
    const h = Math.max(1, (v / vmax) * volH);
    return `<rect x="${x.toFixed(1)}" y="${(volTop + volH - h).toFixed(1)}" width="8" height="${h.toFixed(1)}" fill="#94a3b8" opacity=".55"/>`;
  }).join("");
  const grid = [0, .25, .5, .75, 1].map(t => {
    const y = top + priceH * t;
    const price = max - span * t;
    return `<line x1="${left}" y1="${y}" x2="${width-right}" y2="${y}" stroke="#e2e8f0"/><text x="8" y="${y+4}" font-size="12" fill="#64748b">${fmt.format(Math.round(price))}</text>`;
  }).join("");
  const baseY = yFor(prices[0]);
  const volAvgLine = volMa5.map((v, i) => {
    if (!v) return "";
    const x = xFor(i);
    const y = volTop + volH - (v / vmax) * volH;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).filter(Boolean).join(" ");
  const labels = data.map((p, i) => {
    if (i !== 0 && i !== data.length - 1 && i % Math.ceil(data.length / 4) !== 0) return "";
    return `<text x="${xFor(i).toFixed(1)}" y="${height - 18}" text-anchor="middle" font-size="11" fill="#64748b">${String(p.date).slice(5)}</text>`;
  }).join("");
  return `<svg class="big-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
    <rect x="0" y="0" width="${width}" height="${height}" fill="#fbfdff"/>
    ${grid}
    <line x1="${left}" y1="${baseY}" x2="${width-right}" y2="${baseY}" stroke="#64748b" stroke-width="1.2" stroke-dasharray="5 5"/>
    ${bbUpper && bbLower ? `<polygon points="${pointsForConst(bbUpper)} ${data.map((_, i) => `${xFor(data.length - 1 - i).toFixed(1)},${yFor(bbLower).toFixed(1)}`).join(" ")}" fill="#fef3c7" opacity=".36"/>` : ""}
    ${levelLine(support, "#0f766e", "지지")}
    ${levelLine(longLine, "#0891b2", "장기선")}
    ${levelLine(resistance, "#dc2626", "저항", "3 4")}
    ${levelLine(entry, "#111827", "진입", "2 3")}
    ${levelLine(stop, "#b91c1c", "손절", "8 4")}
    ${levelLine(target1, "#16a34a", "1차목표", "8 4")}
    ${levelLine(target2, "#15803d", "2차목표", "3 4")}
    ${bars}
    <polyline points="${volAvgLine}" fill="none" stroke="#0f766e" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity=".8"/>
    <line x1="${left}" y1="${volTop + volH}" x2="${width-right}" y2="${volTop + volH}" stroke="#cbd5e1"/>
    ${candles}
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" opacity=".72"/>
    <polyline points="${lineFor(ma5)}" fill="none" stroke="#f59e0b" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
    <polyline points="${lineFor(ma20)}" fill="none" stroke="#7c3aed" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
    <polyline points="${lineFor(ma60)}" fill="none" stroke="#0891b2" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
    ${bbUpper ? `<polyline points="${pointsForConst(bbUpper)}" fill="none" stroke="#d97706" stroke-width="1.4" stroke-dasharray="4 4" opacity=".95"/>` : ""}
    ${bbMiddle ? `<polyline points="${pointsForConst(bbMiddle)}" fill="none" stroke="#f59e0b" stroke-width="1.2" stroke-dasharray="2 4" opacity=".75"/>` : ""}
    ${bbLower ? `<polyline points="${pointsForConst(bbLower)}" fill="none" stroke="#d97706" stroke-width="1.4" stroke-dasharray="4 4" opacity=".95"/>` : ""}
    ${vwap20 ? `<polyline points="${pointsForConst(vwap20)}" fill="none" stroke="#0f766e" stroke-width="2.4" opacity=".9"/>` : ""}
    ${coords.map(([x,y]) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="${color}"/>`).join("")}
    ${labels}
    <g font-size="12" font-weight="700">
      <rect x="${left}" y="6" width="720" height="22" rx="5" fill="rgba(255,255,255,.85)" stroke="#e2e8f0"/>
      <text x="${left + 10}" y="21" fill="${color}">종가</text>
      <text x="${left + 62}" y="21" fill="#f59e0b">MA5</text>
      <text x="${left + 112}" y="21" fill="#7c3aed">MA20</text>
      <text x="${left + 174}" y="21" fill="#0891b2">MA60/장기선</text>
      <text x="${left + 280}" y="21" fill="#0f766e">지지선</text>
      <text x="${left + 342}" y="21" fill="#dc2626">저항선</text>
      <text x="${left + 412}" y="21" fill="#d97706">볼린저</text>
      <text x="${left + 474}" y="21" fill="#0f766e">VWAP</text>
      <text x="${left + 524}" y="21" fill="#111827">진입/손절/목표</text>
    </g>
    <text x="${left}" y="${volTop - 6}" font-size="12" fill="#64748b">거래량</text>
  </svg>`;
}
function esc(text) {
  return String(text || "").replace(/[&<>"']/g, s => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;" }[s]));
}
function renderKisStatus(data) {
  const status = document.querySelector("#kisStatus");
  if (!data.configured) {
    status.textContent = "미연결";
    return;
  }
  document.querySelector("#kisVirtual").value = data.virtual ? "true" : "false";
  status.textContent = `${data.mode || "KIS"} · KEY ${data.appKey || "저장됨"}`;
}
async function loadKisStatus() {
  const res = await fetch(`/api/kis-status?t=${Date.now()}`);
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || "KIS 상태 확인 실패");
  renderKisStatus(data);
  return data;
}
async function saveKisSettings() {
  const btn = document.querySelector("#kisSave");
  btn.disabled = true;
  btn.textContent = "확인 중";
  try {
    const res = await fetch("/api/kis-settings", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        appKey: document.querySelector("#kisAppKey").value,
        appSecret: document.querySelector("#kisAppSecret").value,
        accountNo: document.querySelector("#kisAccountNo").value,
        productCode: "01",
        virtual: document.querySelector("#kisVirtual").value === "true",
      }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "KIS 연결 실패");
    document.querySelector("#kisAppKey").value = "";
    document.querySelector("#kisAppSecret").value = "";
    renderKisStatus(data);
    const sample = data.sampleQuote || {};
    alert(`KIS 연결 확인 완료${sample.close ? ` · SK하이닉스 ${price(sample.close)}` : ""}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "키 저장/확인";
  }
}
async function refreshKisPrices() {
  const btn = document.querySelector("#kisRefresh");
  btn.disabled = true;
  btn.textContent = "현재가 갱신 중";
  try {
    const codes = currentItems.map(item => item.code).join(",");
    const res = await fetch(`/api/kis-refresh?codes=${encodeURIComponent(codes)}`);
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "KIS 현재가 갱신 실패");
    await load();
    const sample = data.sampleQuote || {};
    setLiveStatus(`KIS 현재가 ${data.priceRows || 0}종목`);
    if (sample.close) alert(`KIS 현재가 갱신 완료 · SK하이닉스 ${price(sample.close)}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "현재가 KIS 갱신";
  }
}
async function loadPaper() {
  const res = await fetch(`/api/paper?t=${Date.now()}`);
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || "모의투자 계좌 조회 실패");
  paperState = data.portfolio;
  renderPaper(paperState);
}
function renderPaper(paper) {
  document.querySelector("#p-equity").innerHTML = price(paper.totalEquity);
  document.querySelector("#p-cash").innerHTML = price(paper.cash);
  document.querySelector("#p-unreal").innerHTML = signedMoney(paper.unrealizedPnl);
  document.querySelector("#p-return").innerHTML = signedPct(paper.returnPct);
  document.querySelector("#p-win").textContent = paper.closedTrades ? `${Number(paper.winRate || 0).toFixed(1)}%` : "-";
  const holdings = paper.holdings || [];
  document.querySelector("#paperHoldings").className = holdings.length ? "" : "paper-empty";
  document.querySelector("#paperHoldings").innerHTML = holdings.length ? `
    <table><thead><tr><th>종목</th><th>수량</th><th>평단</th><th>현재</th><th>손익</th><th>현재신호</th><th>주문</th></tr></thead>
    <tbody>${holdings.map(item => `
      <tr class="holding-row" onclick="openDetail('${esc(item.code)}')">
        <td><b>${esc(item.name)}</b><br><span class="note">${esc(item.code)} · ${esc(item.setup || "")}</span></td>
        <td>${fmt.format(item.qty || 0)}</td>
        <td>${price(item.avg_price)}</td>
        <td>${price(item.current_price)}</td>
        <td>${signedMoney(item.pnl)}<br>${signedPct(item.pnl_pct)}</td>
        <td>${esc(item.current_signal || "-")}<br><span class="note">${esc(item.current_setup || "")}</span></td>
        <td>
          <button class="mini-btn" onclick="event.stopPropagation(); openDetail('${esc(item.code)}')">추가/매도</button>
          <button class="mini-btn sell" onclick="event.stopPropagation(); paperSell('${esc(item.code)}', 0, true)">전량</button>
        </td>
      </tr>
    `).join("")}</tbody></table>
  ` : "보유 종목 없음";
  const trades = paper.trades || [];
  document.querySelector("#paperTrades").className = trades.length ? "" : "paper-empty";
  document.querySelector("#paperTrades").innerHTML = trades.length ? `
    <table><thead><tr><th>시간</th><th>구분</th><th>종목</th><th>금액</th><th>손익</th></tr></thead>
    <tbody>${trades.slice(0, 10).map(trade => `
      <tr>
        <td>${timeText(trade.time)}</td>
        <td><b class="${trade.side === "BUY" ? "up" : "down"}">${trade.side === "BUY" ? "매수" : "매도"}</b></td>
        <td>${esc(trade.name)}<br><span class="note">${esc(trade.signal || "")}</span></td>
        <td>${fmt.format(trade.qty || 0)}주<br>${price(trade.amount)}</td>
        <td>${trade.side === "SELL" ? `${signedMoney(trade.pnl)}<br>${signedPct(trade.pnl_pct)}` : "-"}</td>
      </tr>
    `).join("")}</tbody></table>
  ` : "거래 내역 없음";
}
async function paperCall(path) {
  const res = await fetch(path);
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || "모의투자 처리 실패");
  paperState = data.portfolio;
  renderPaper(paperState);
  return data;
}
async function paperReset() {
  const cash = Number(document.querySelector("#paperInitialCash").value || 10000000);
  if (!confirm(`${fmt.format(cash)}원으로 모의투자 계좌를 새로 시작할까요? 기존 거래내역은 초기화됩니다.`)) return;
  await paperCall(`/api/paper-reset?cash=${encodeURIComponent(cash)}`);
}
async function paperBuy(code, amount) {
  const data = await paperCall(`/api/paper-buy?code=${encodeURIComponent(code)}&amount=${encodeURIComponent(amount)}`);
  document.querySelector("#d-paper-status").textContent = "모의매수 완료";
  return data;
}
async function paperBuyQty(code, qty) {
  const data = await paperCall(`/api/paper-buy?code=${encodeURIComponent(code)}&qty=${encodeURIComponent(qty)}`);
  document.querySelector("#d-paper-status").textContent = "수량 모의매수 완료";
  return data;
}
async function paperSell(code, qty = 0, sellAll = false) {
  const url = sellAll
    ? `/api/paper-sell?code=${encodeURIComponent(code)}&all=1`
    : `/api/paper-sell?code=${encodeURIComponent(code)}&qty=${encodeURIComponent(qty)}`;
  const data = await paperCall(url);
  document.querySelector("#d-paper-status").textContent = "모의매도 완료";
  return data;
}
function stockMatches(item, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return true;
  const haystack = [
    item.code,
    item.name,
    item.market,
    item.sector,
    item.theme,
    item.recommendation,
    item.tradeAction,
    ...(item.tags || []),
  ].join(" ").toLowerCase();
  return haystack.includes(q);
}
function renderRows(items) {
  currentItems = items || [];
  document.querySelector("#rows").innerHTML = currentItems.map(item => `
    <tr class="stock-row" onclick="openDetail('${item.code}')">
      <td>${item.rank}</td>
      <td><b>${esc(item.name)}</b><br><span class="note">${esc(item.code)} · ${esc(item.market)}</span></td>
      <td><b>${esc(item.sector)}</b><br><span class="note">${esc(item.theme)}</span></td>
      <td><span class="rec ${recClass(item.recommendation)}">${esc(item.recommendation)}</span><br><span class="note">위험 ${esc(item.risk)}</span></td>
      <td><span class="action ${actionClass(item.tradeAction)}">${esc(item.tradeAction || "보류")}</span><br><span class="note">${esc(item.tradeReason || "")}</span></td>
      <td><b>${esc(item.pro?.bias || "-")}</b><br><span class="note">${esc(item.pro?.setup || "")}</span></td>
      <td class="score">${Number(item.discoveryScore).toFixed(1)}<br><span class="note">기본 ${Number(item.score).toFixed(1)}</span></td>
      <td><b>${price(item.close)}</b><br>${rate(item.changeRate)}</td>
      <td onclick="event.stopPropagation(); openDetail('${item.code}')">${sparkline(item.chart)}</td>
      <td><b>${(Number(item.flowRatio || 0) * 100).toFixed(1)}%</b><br><span class="note">외 ${flow(item.foreign, item.foreignAvailable)} / 기 ${flow(item.institution, item.institutionAvailable)}</span></td>
      <td>${Number(item.usImpact || 0) >= 0 ? "+" : ""}${Number(item.usImpact || 0).toFixed(1)}</td>
      <td>${(item.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join("")}</td>
      <td class="reasons">${(item.reasons || []).map(esc).join("<br>")}${item.penalties?.length ? `<br><span class="penalty">${item.penalties.map(esc).join("<br>")}</span>` : ""}</td>
    </tr>
  `).join("");
}
async function runStockSearch(autoOpen = true) {
  const query = document.querySelector("#stockSearch").value.trim();
  if (!query) {
    document.querySelector("#searchResults").innerHTML = "";
    renderRows(rankedItems);
    return;
  }
  let matches = searchableItems.filter(item => stockMatches(item, query));
  const codeLike = /^\\d{5,6}$/.test(query);
  if (!matches.length && codeLike && autoOpen) {
    try {
      const res = await fetch(`/api/search-stock?q=${encodeURIComponent(query)}`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "검색 실패");
      lastPayload = data.payload;
      rankedItems = lastPayload.items || [];
      searchableItems = lastPayload.searchItems || rankedItems;
      matches = searchableItems.filter(item => stockMatches(item, query));
    } catch (err) {
      document.querySelector("#searchResults").innerHTML = `<div class="search-card"><b>검색 실패</b><span>${esc(err.message)}</span></div>`;
      return;
    }
  }
  renderRows(matches.slice(0, 40));
  document.querySelector("#searchResults").innerHTML = matches.length ? matches.slice(0, 8).map(item => `
    <div class="search-card" onclick="openDetail('${item.code}')">
      <b>${esc(item.name)} (${esc(item.code)})</b>
      <span>${price(item.close)} · ${rate(item.changeRate)} · ${esc(item.tradeAction || "보류")} · ${Number(item.discoveryScore || 0).toFixed(1)}점</span>
    </div>
  `).join("") : `<div class="search-card"><b>검색 결과 없음</b><span>종목코드 6자리로 검색하면 KIS 현재가 조회를 시도합니다.</span></div>`;
  if (autoOpen && matches[0]) openDetail(matches[0].code);
}
async function load() {
  const res = await fetch("/api/analyze");
  const data = await res.json();
  lastPayload = data;
  rankedItems = data.items || [];
  searchableItems = data.searchItems || rankedItems;
  document.querySelector("#m-count").textContent = data.count;
  document.querySelector("#m-strong").textContent = data.items.filter(x => Number(x.discoveryScore) >= 75).length;
  document.querySelector("#m-sector").textContent = (data.sectors || []).length;
  document.querySelector("#m-flow").textContent = data.items.filter(x => Number(x.flowRatio) >= .05).length;
  document.querySelector("#m-pro").textContent = data.items.filter(x => ["공격매수 후보", "분할매수 후보"].includes(x.pro?.bias)).length;
  document.querySelector("#m-risk").textContent = data.items.filter(x => x.recommendation === "위험").length;
  const quoteLabel = data.quoteSource ? `${data.quoteSource}${data.quoteOverrideCount ? ` ${data.quoteOverrideCount}종목` : ""}` : data.marketCsv || "";
  lastDataLabel = `${quoteLabel} ${data.quoteUpdatedAt || ""}`;
  setLiveStatus(liveRunning ? "실시간 대기" : "대기");
  const bottomCandidates = data.bottomCandidates || [];
  const bottomAlert = document.querySelector("#bottomAlert");
  bottomAlert.classList.toggle("open", bottomCandidates.length > 0);
  document.querySelector("#bottomCards").innerHTML = bottomCandidates.map(item => `
    <div class="bottom-card" onclick="openDetail('${item.code}')">
      <b>${esc(item.name)} · ${Number(item.score || 0).toFixed(1)}점</b>
      <span>${esc(item.stage || "바닥매집 후보")} · 지지 ${fmt.format(item.support || 0)}원 · 터치 ${item.touchCount || 0}회</span>
      <span>${(item.reasons || []).slice(0, 2).map(esc).join(" / ")}</span>
    </div>
  `).join("");
  document.querySelector("#sectorCards").innerHTML = (data.sectors || []).map(group => `
    <div class="sector-card">
      <b>${esc(group.sector)} · ${Number(group.topScore).toFixed(1)}</b>
      ${(group.items || []).map(x => `<span>${esc(x.name)} ${Number(x.score).toFixed(1)}점 · ${esc(x.theme)}</span>`).join("")}
    </div>
  `).join("") || `<span class="note">섹터 후보 없음</span>`;
  const signalOrder = ["kospi", "kosdaq", "gold", "usdkrw", "nasdaq", "sox", "nvidia", "tesla", "bio", "oil", "china"];
  document.querySelector("#signalCards").innerHTML = signalOrder.map(key => data.signals?.[key]).filter(Boolean).map(sig => {
    const chg = Number(sig.change_pct || 0);
    const close = Number(sig.close || 0);
    return `<div class="signal"><strong>${esc(sig.label || sig.symbol)}</strong><span class="${chg >= 0 ? "pos" : "neg"}">${close ? `${fmt.format(close)} · ` : ""}${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</span></div>`;
  }).join("");
  const query = document.querySelector("#stockSearch").value.trim();
  if (query) runStockSearch(false);
  else renderRows(rankedItems);
  if (!bottomAutoOpened && bottomCandidates.length) {
    const first = bottomCandidates.find(candidate => currentItems.some(item => item.code === candidate.code));
    if (first) {
      bottomAutoOpened = true;
      setTimeout(() => openDetail(first.code), 250);
    }
  }
  loadPaper().catch(err => {
    document.querySelector("#paperHoldings").className = "paper-empty";
    document.querySelector("#paperHoldings").textContent = err.message;
  });
}
function setLiveStatus(text) {
  const dot = document.querySelector("#liveDot");
  const status = document.querySelector("#liveStatus");
  dot.classList.toggle("on", liveRunning);
  status.textContent = `${text} · ${lastDataLabel} · ${new Date().toLocaleTimeString("ko-KR")}`;
}
async function liveRefresh() {
  if (!liveRunning) return;
  const codes = currentItems.slice(0, 18).map(item => item.code).join(",");
  setLiveStatus("실시간 갱신 중");
  try {
    const res = await fetch(`/api/live-refresh?codes=${encodeURIComponent(codes)}`);
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "실시간 갱신 실패");
    await load();
    setLiveStatus(`실시간 ON ${data.priceRows || 0}종목`);
  } catch (err) {
    setLiveStatus(`실시간 오류: ${err.message}`);
  }
}
function startLive() {
  liveRunning = true;
  document.querySelector("#liveToggle").classList.add("live-on");
  document.querySelector("#liveToggle").textContent = "실시간 ON";
  liveRefresh();
  liveTimer = setInterval(liveRefresh, LIVE_INTERVAL_MS);
}
function stopLive() {
  liveRunning = false;
  if (liveTimer) clearInterval(liveTimer);
  liveTimer = null;
  document.querySelector("#liveToggle").classList.remove("live-on");
  document.querySelector("#liveToggle").textContent = "실시간 OFF";
  setLiveStatus("대기");
}
function openDetail(code) {
  const item = currentItems.find(x => x.code === code) || searchableItems.find(x => x.code === code) || rankedItems.find(x => x.code === code);
  if (!item) return;
  currentDetailCode = code;
  const pro = item.pro || {};
  const indicators = pro.indicators || {};
  document.querySelector("#d-title").textContent = `${item.name} (${item.code})`;
  document.querySelector("#d-sub").textContent = `${item.market} · ${item.tags.join(" · ") || "신호 없음"} · ${pro.bias || "전문가판 대기"}`;
  document.querySelector("#d-rec").innerHTML = `<span class="rec ${recClass(item.recommendation)}">${item.recommendation}</span>`;
  document.querySelector("#d-action").innerHTML = `<span class="action ${actionClass(item.tradeAction)}">${item.tradeAction || "보류"}</span><br><span class="note">${esc(item.tradeReason || "")}</span>`;
  document.querySelector("#d-price").textContent = price(item.close);
  document.querySelector("#d-score").textContent = `${Number(item.discoveryScore).toFixed(1)}점`;
  document.querySelector("#d-risk").textContent = item.risk;
  document.querySelector("#d-vol").textContent = `${item.volumeRatio.toFixed(1)}배`;
  document.querySelector("#d-foreign").innerHTML = flow(item.foreign, item.foreignAvailable);
  document.querySelector("#d-inst").innerHTML = flow(item.institution, item.institutionAvailable);
  document.querySelector("#d-flow").textContent = `${(Number(item.flowRatio || 0) * 100).toFixed(1)}%`;
  document.querySelector("#d-us").textContent = `${Number(item.usImpact || 0) >= 0 ? "+" : ""}${Number(item.usImpact || 0).toFixed(1)}`;
  document.querySelector("#d-bottom").textContent = item.bottomScore >= 45 ? `${Number(item.bottomScore).toFixed(1)}점` : "해당 없음";
  document.querySelector("#d-price-scale").textContent = Number(item.historyScale || 1) !== 1 ? `차트 ×${Number(item.historyScale).toFixed(0)}` : "원본";
  document.querySelector("#d-paper-status").textContent = `${price(item.close)} 현시세 기준 가상 체결`;
  document.querySelector("#d-paper-qty").value = "";
  document.querySelector("#d-paper-buy-qty").value = "";
  document.querySelector("#d-chart").innerHTML = detailedChart(item.chart, item);
  document.querySelector("#d-pro-strip").innerHTML = [
    ["셋업", pro.setup || "-", pro.bias === "공격매수 후보" || pro.bias === "분할매수 후보" ? "positive" : ""],
    ["진입 후보", price(pro.entry), ""],
    ["손절 기준", price(pro.stop), "warning"],
    ["1차 목표", price(pro.target1), "positive"],
    ["2차 목표", price(pro.target2), "positive"],
    ["손익비", pro.riskReward ? `${Number(pro.riskReward).toFixed(2)}배` : "-", Number(pro.riskReward || 0) >= 1.5 ? "positive" : "warning"],
    ["RSI 14", indicators.rsi14 ? Number(indicators.rsi14).toFixed(1) : "-", Number(indicators.rsi14 || 0) >= 75 ? "warning" : ""],
    ["MACD", indicators.macdHist ? `${Number(indicators.macdHist).toFixed(1)} 히스토그램` : "-", Number(indicators.macdHist || 0) > 0 ? "positive" : "warning"],
    ["VWAP20", price(indicators.vwap20), item.close >= Number(indicators.vwap20 || 0) ? "positive" : "warning"],
    ["ATR14", price(indicators.atr14), ""],
    ["볼린저폭", indicators.bbWidth ? `${Number(indicators.bbWidth).toFixed(1)}%` : "-", ""],
    ["추세점수", `${indicators.trendScore || 0}/5`, Number(indicators.trendScore || 0) >= 4 ? "positive" : ""],
  ].map(([label, value, cls]) => `<div class="pro-tile ${cls}"><span>${label}</span><b>${esc(value)}</b></div>`).join("");
  const detailReasons = [...(item.reasons || [])];
  if (item.bottomScore >= 45) detailReasons.push(...(item.bottomReasons || []).map(x => `바닥매집: ${x}`));
  const detailWarnings = [...(item.penalties || []), ...(item.bottomWarnings || [])];
  document.querySelector("#d-reasons").innerHTML = (detailReasons.length ? detailReasons : ["상승 근거 부족"]).map(x => `<li>${esc(x)}</li>`).join("");
  document.querySelector("#d-penalties").innerHTML = (detailWarnings.length ? detailWarnings : ["특별한 감점 없음"]).map(x => `<li>${esc(x)}</li>`).join("");
  document.querySelector("#d-pro-checks").innerHTML = (pro.checklist?.length ? pro.checklist : ["전문가 체크 신호 부족"]).map(x => `<li>${esc(x)}</li>`).join("");
  document.querySelector("#d-pro-warnings").innerHTML = (pro.warnings?.length ? pro.warnings : ["추가 경고 없음"]).map(x => `<li>${esc(x)}</li>`).join("");
  document.querySelector("#detailModal").classList.add("open");
}
document.querySelector("#closeDetail").addEventListener("click", () => document.querySelector("#detailModal").classList.remove("open"));
document.querySelector("#detailModal").addEventListener("click", event => {
  if (event.target.id === "detailModal") document.querySelector("#detailModal").classList.remove("open");
});
document.querySelector("#refresh").addEventListener("click", load);
document.querySelector("#paperRefresh").addEventListener("click", () => loadPaper().catch(err => alert(err.message)));
document.querySelector("#paperReset").addEventListener("click", () => paperReset().catch(err => alert(err.message)));
document.querySelector("#d-paper-buy").addEventListener("click", () => {
  const amount = Number(document.querySelector("#d-paper-amount").value || 0);
  if (!currentDetailCode) return;
  paperBuy(currentDetailCode, amount).catch(err => alert(err.message));
});
document.querySelector("#d-paper-buy-qty-btn").addEventListener("click", () => {
  const qty = Number(document.querySelector("#d-paper-buy-qty").value || 0);
  if (!currentDetailCode) return;
  paperBuyQty(currentDetailCode, qty).catch(err => alert(err.message));
});
document.querySelector("#d-paper-sell").addEventListener("click", () => {
  const qty = Number(document.querySelector("#d-paper-qty").value || 0);
  if (!currentDetailCode) return;
  paperSell(currentDetailCode, qty, false).catch(err => alert(err.message));
});
document.querySelector("#d-paper-sell-all").addEventListener("click", () => {
  if (!currentDetailCode) return;
  paperSell(currentDetailCode, 0, true).catch(err => alert(err.message));
});
document.querySelector("#liveToggle").addEventListener("click", () => {
  if (liveRunning) stopLive();
  else startLive();
});
document.querySelector("#fetchKis").addEventListener("click", async () => {
  const btn = document.querySelector("#fetchKis");
  btn.disabled = true;
  btn.textContent = "KIS 수집 중";
  try {
    const res = await fetch("/api/fetch-kis-supply");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "KIS 수집 실패");
    btn.textContent = "KIS 완료";
    await load();
  } catch (err) {
    alert(err.message);
    btn.textContent = "KIS 수급 자동수집";
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "KIS 수급 자동수집";
    }, 1200);
  }
});
document.querySelector("#fetchGlobal").addEventListener("click", async () => {
  const btn = document.querySelector("#fetchGlobal");
  btn.disabled = true;
  btn.textContent = "미국 업데이트 중";
  try {
    const res = await fetch("/api/fetch-global");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "글로벌 지표 수집 실패");
    btn.textContent = "업데이트 완료";
    await load();
  } catch (err) {
    alert(err.message);
    btn.textContent = "미국 흐름 업데이트";
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "미국 흐름 업데이트";
    }, 1200);
  }
});
document.querySelector("#fetchKrx").addEventListener("click", async () => {
  const btn = document.querySelector("#fetchKrx");
  btn.disabled = true;
  btn.textContent = "수집 중";
  try {
    const res = await fetch("/api/fetch-krx");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "KRX 수집 실패");
    btn.textContent = "수집 완료";
    await load();
  } catch (err) {
    alert(err.message);
    btn.textContent = "KRX 자동수집";
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "KRX 자동수집";
    }, 1200);
  }
});
document.querySelector("#kisSave").addEventListener("click", () => saveKisSettings().catch(err => alert(err.message)));
document.querySelector("#kisRefresh").addEventListener("click", () => refreshKisPrices().catch(err => alert(err.message)));
document.querySelector("#stockSearchBtn").addEventListener("click", () => runStockSearch(true));
document.querySelector("#stockSearch").addEventListener("keydown", event => {
  if (event.key === "Enter") runStockSearch(true);
});
document.querySelector("#stockSearch").addEventListener("input", () => runStockSearch(false));
document.querySelector("#stockSearchClear").addEventListener("click", () => {
  document.querySelector("#stockSearch").value = "";
  document.querySelector("#searchResults").innerHTML = "";
  renderRows(rankedItems);
});
loadKisStatus().catch(err => {
  document.querySelector("#kisStatus").textContent = err.message;
});
load();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict[str, object], status: int | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status if status is not None else 200 if payload.get("ok", True) else 500)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def do_POST(self) -> None:
        if self.path.startswith("/api/kis-settings"):
            try:
                payload = {"ok": True} | save_kis_settings(self._read_json_body())
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            self._send_json(payload)
            return
        self._send_json({"ok": False, "error": "지원하지 않는 요청입니다."}, status=404)

    def do_GET(self) -> None:
        if self.path.startswith("/api/kis-status"):
            try:
                payload = kis_status_payload()
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            self._send_json(payload)
            return
        if self.path.startswith("/api/kis-refresh"):
            try:
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                codes = [
                    code.strip().zfill(6)
                    for code in ",".join(query.get("codes", [])).split(",")
                    if code.strip()
                ]
                payload = refresh_kis_quotes(codes)
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            self._send_json(payload)
            return
        if self.path.startswith("/api/search-stock"):
            try:
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                search = str(query.get("q", [""])[0]).strip()
                if search.isdigit() and 5 <= len(search) <= 6:
                    refresh_kis_quotes([search])
                payload = {"ok": True, "payload": analyze_payload(use_public_fallback=True)}
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            self._send_json(payload)
            return
        if self.path.startswith("/api/analyze"):
            payload = analyze_payload(use_public_fallback=True)
            self._send_json(payload)
            return
        if self.path.startswith("/api/paper"):
            try:
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                quotes = current_quote_map()
                portfolio = load_portfolio(PAPER_PORTFOLIO_PATH)
                if parsed.path == "/api/paper-reset":
                    cash = int(float(query.get("cash", [DEFAULT_CASH])[0] or DEFAULT_CASH))
                    portfolio = new_portfolio(cash)
                    save_portfolio(PAPER_PORTFOLIO_PATH, portfolio)
                elif parsed.path == "/api/paper-buy":
                    code = str(query.get("code", [""])[0]).zfill(6)
                    amount = float(query.get("amount", [0])[0] or 0)
                    qty = int(float(query.get("qty", [0])[0] or 0))
                    if code not in quotes:
                        raise RuntimeError("현재 앱 분석 목록에 있는 종목만 매수할 수 있습니다.")
                    portfolio = paper_buy_quantity(portfolio, quotes[code], qty) if qty > 0 else paper_buy(portfolio, quotes[code], amount)
                    save_portfolio(PAPER_PORTFOLIO_PATH, portfolio)
                elif parsed.path == "/api/paper-sell":
                    code = str(query.get("code", [""])[0]).zfill(6)
                    qty_text = query.get("qty", [""])[0]
                    sell_all = query.get("all", ["0"])[0] == "1"
                    if code not in quotes:
                        raise RuntimeError("현재 앱 분석 목록에 있는 종목만 매도 평가할 수 있습니다.")
                    portfolio = paper_sell(portfolio, quotes[code], int(qty_text) if qty_text else None, sell_all=sell_all)
                    save_portfolio(PAPER_PORTFOLIO_PATH, portfolio)
                payload = {"ok": True, "portfolio": evaluate_paper(portfolio, quotes)}
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if payload["ok"] else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/fetch-krx"):
            try:
                market_path, investor_path = fetch_krx_bundle(None, DATA_DIR)
                payload = {
                    "ok": True,
                    "market": market_path.name,
                    "investor": investor_path.name,
                }
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if payload["ok"] else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/fetch-kis-supply"):
            try:
                config = get_kis_config()
                if config is None:
                    raise RuntimeError("KIS_APP_KEY/KIS_APP_SECRET 설정이 필요합니다.")
                client = KisClient(config)
                foreign_rows = []
                institution_rows = []
                for market_code in ("0000", "0001", "1001"):
                    foreign_payload = with_kis_retry(lambda market_code=market_code: client.foreign_institution_total(investor="1", market_code=market_code))
                    save_json(DATA_DIR / "kis_raw" / f"foreign_institution_foreign_{market_code}.json", foreign_payload)
                    foreign_rows.extend(aggregate_supply_rows(foreign_payload, "foreign"))
                    time.sleep(1.2)
                    institution_payload = with_kis_retry(lambda market_code=market_code: client.foreign_institution_total(investor="2", market_code=market_code))
                    save_json(DATA_DIR / "kis_raw" / f"foreign_institution_institution_{market_code}.json", institution_payload)
                    institution_rows.extend(aggregate_supply_rows(institution_payload, "institution"))
                    time.sleep(1.2)
                volume_payload = with_kis_retry(client.volume_rank)
                save_json(DATA_DIR / "kis_raw" / "volume_rank.json", volume_payload)
                volume_rows = market_rows_from_volume_rank(volume_payload)
                foreign_path = save_supply_csv(DATA_DIR / "krx" / "kis_외국인_순매수.csv", foreign_rows)
                institution_path = save_supply_csv(DATA_DIR / "krx" / "kis_기관_순매수.csv", institution_rows)
                market_path = save_market_csv(DATA_DIR / "kis_market.csv", merge_market_rows(market_rows_from_supply_rows(foreign_rows + institution_rows) + volume_rows))
                payload = {
                    "ok": True,
                    "foreign": foreign_path.name,
                    "institution": institution_path.name,
                    "market": market_path.name,
                    "foreignRows": len(foreign_rows),
                    "institutionRows": len(institution_rows),
                    "volumeRows": len(volume_rows),
                }
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if payload["ok"] else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/live-refresh"):
            try:
                config = get_kis_config()
                if config is None:
                    raise RuntimeError("KIS_APP_KEY/KIS_APP_SECRET 설정이 필요합니다.")
                client = KisClient(config)
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                codes = [
                    code.strip().zfill(6)
                    for code in ",".join(query.get("codes", [])).split(",")
                    if code.strip()
                ][:18]
                existing_rows = load_market_csv_rows(DATA_DIR / "kis_market.csv")
                existing_by_code = {str(row.get("code", "")).zfill(6): row for row in existing_rows}

                volume_payload = with_kis_retry(client.volume_rank)
                save_json(DATA_DIR / "kis_raw" / "live_volume_rank.json", volume_payload)
                volume_rows = market_rows_from_volume_rank(volume_payload)

                price_rows = []
                for code in codes:
                    fallback = existing_by_code.get(code, {})
                    price_payload = with_kis_retry(lambda code=code: client.inquire_price(code))
                    price_rows.extend(
                        market_rows_from_price_payload(
                            price_payload,
                            fallback_code=code,
                            fallback_name=str(fallback.get("name") or code),
                        )
                    )
                    time.sleep(0.25)
                price_rows = normalize_live_rows(price_rows, existing_rows)
                market_path = save_market_csv(
                    DATA_DIR / "kis_market.csv",
                    merge_market_rows(existing_rows + volume_rows + price_rows, prefer_latest=True),
                )
                payload = {
                    "ok": True,
                    "market": market_path.name,
                    "volumeRows": len(volume_rows),
                    "priceRows": len(price_rows),
                    "updatedAt": int(time.time()),
                }
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if payload["ok"] else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/fetch-global"):
            try:
                payload = {"ok": True, "data": fetch_global_signals()}
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if payload["ok"] else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = INDEX_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_server(host: str = "127.0.0.1", port: int = 8777) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"K-Stock Force Tracker: {url}")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    run_server()
