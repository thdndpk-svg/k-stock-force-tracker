from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from csv_utils import clean_number, first_value, read_csv_rows, write_csv


SUPPLY_FIELDNAMES = [
    "date",
    "source",
    "kind",
    "code",
    "name",
    "net_value",
    "foreign_net_value",
    "institution_net_value",
    "net_qty",
    "price",
    "change_rate",
    "volume",
]

MARKET_FIELDNAMES = [
    "date",
    "code",
    "name",
    "market",
    "close",
    "prev_close",
    "open",
    "high",
    "low",
    "volume",
    "trading_value",
    "change_rate",
    "foreign_net_value",
    "institution_net_value",
    "volume_ratio",
    "value_ratio",
]


def output_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("output", "output1", "output2"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            return [value]
    return []


def _code(row: dict[str, Any]) -> str:
    for key in ("mksc_shrn_iscd", "stck_shrn_iscd", "pdno", "code", "종목코드"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.zfill(6)
    return ""


def _name(row: dict[str, Any]) -> str:
    for key in ("hts_kor_isnm", "prdt_name", "stck_kor_isnm", "name", "종목명"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return _code(row)


def _scaled_trade_value(raw_value: Any, qty: Any, price: Any) -> float:
    raw = clean_number(raw_value)
    quantity = abs(clean_number(qty))
    current_price = abs(clean_number(price))
    implied = quantity * current_price
    if not raw or not implied:
        return raw

    sign = -1.0 if raw < 0 else 1.0
    raw_abs = abs(raw)
    # KIS 가집계 거래대금은 화면 단위로 내려오는 경우가 있어 가격*수량과 가장 가까운 단위로 보정한다.
    candidates = [raw_abs, raw_abs * 1_000, raw_abs * 10_000, raw_abs * 100_000, raw_abs * 1_000_000]
    best = min(candidates, key=lambda value: abs(value - implied))
    if abs(best - implied) < abs(raw_abs - implied):
        return sign * best
    return raw


def _normalized_price(raw_price: Any, qty: float, net_value: float) -> float:
    return clean_number(raw_price)


def aggregate_supply_rows(payload: dict[str, Any], kind: str, trade_date: date | None = None) -> list[dict[str, object]]:
    if kind not in {"foreign", "institution"}:
        raise ValueError("kind must be foreign or institution")
    value_key = "frgn_ntby_tr_pbmn" if kind == "foreign" else "orgn_ntby_tr_pbmn"
    qty_key = "frgn_ntby_qty" if kind == "foreign" else "orgn_ntby_qty"
    rows: list[dict[str, object]] = []
    today = trade_date or date.today()
    for row in output_rows(payload):
        code = _code(row)
        if not code:
            continue
        qty = clean_number(row.get(qty_key) or row.get("ntby_qty"))
        value = _scaled_trade_value(row.get(value_key), qty, row.get("stck_prpr"))
        price = _normalized_price(row.get("stck_prpr"), qty, value)
        item = {
            "date": today.isoformat(),
            "source": "KIS",
            "kind": kind,
            "code": code,
            "name": _name(row),
            "net_value": round(value),
            "foreign_net_value": round(value) if kind == "foreign" else "",
            "institution_net_value": round(value) if kind == "institution" else "",
            "net_qty": round(qty),
            "price": round(price),
            "change_rate": clean_number(row.get("prdy_ctrt")),
            "volume": round(clean_number(row.get("acml_vol"))),
        }
        rows.append(item)
    return rows


def save_supply_csv(path: Path, rows: list[dict[str, object]]) -> Path:
    write_csv(path, rows, SUPPLY_FIELDNAMES)
    return path


def market_rows_from_supply_rows(rows: list[dict[str, object]], trade_date: date | None = None) -> list[dict[str, object]]:
    today = trade_date or date.today()
    merged: dict[str, dict[str, object]] = {}
    for row in rows:
        code = str(row.get("code") or "").zfill(6)
        if not code.strip("0"):
            continue
        close = clean_number(row.get("price"))
        change_rate = clean_number(row.get("change_rate"))
        prev_close = close / (1.0 + change_rate / 100.0) if close and change_rate > -99.0 else close
        item = merged.setdefault(
            code,
            {
                "date": today.isoformat(),
                "code": code,
                "name": row.get("name") or code,
                "market": "KIS",
                "close": round(close),
                "prev_close": round(prev_close),
                "open": round(close),
                "high": round(close),
                "low": round(close),
                "volume": round(clean_number(row.get("volume"))),
                "trading_value": round(close * clean_number(row.get("volume"))),
                "change_rate": change_rate,
                "foreign_net_value": "",
                "institution_net_value": "",
                "volume_ratio": "",
                "value_ratio": "",
            },
        )
        if row.get("foreign_net_value") != "":
            item["foreign_net_value"] = row.get("foreign_net_value")
        if row.get("institution_net_value") != "":
            item["institution_net_value"] = row.get("institution_net_value")
    return list(merged.values())


def market_rows_from_volume_rank(payload: dict[str, Any], trade_date: date | None = None) -> list[dict[str, object]]:
    today = trade_date or date.today()
    rows: list[dict[str, object]] = []
    for row in output_rows(payload):
        code = _code(row)
        if not code:
            continue
        close = clean_number(row.get("stck_prpr"))
        volume = clean_number(row.get("acml_vol"))
        trading_value = clean_number(row.get("acml_tr_pbmn"))
        if not trading_value:
            trading_value = close * volume
        change_rate = clean_number(row.get("prdy_ctrt") or row.get("n_befr_clpr_vrss_prpr_rate"))
        prev_close = close / (1.0 + change_rate / 100.0) if close and change_rate > -99.0 else close
        rows.append(
            {
                "date": today.isoformat(),
                "code": code,
                "name": _name(row),
                "market": "KIS",
                "close": round(close),
                "prev_close": round(prev_close),
                "open": round(close),
                "high": round(close),
                "low": round(close),
                "volume": round(volume),
                "trading_value": round(trading_value),
                "change_rate": change_rate,
                "foreign_net_value": "",
                "institution_net_value": "",
                "volume_ratio": clean_number(row.get("vol_inrt") or row.get("nday_vol_tnrt")) / 100.0,
                "value_ratio": clean_number(row.get("tr_pbmn_tnrt") or row.get("nday_tr_pbmn_tnrt")) / 100.0,
            }
        )
    return rows


def market_rows_from_price_payload(
    payload: dict[str, Any],
    fallback_code: str = "",
    fallback_name: str = "",
    trade_date: date | None = None,
) -> list[dict[str, object]]:
    today = trade_date or date.today()
    rows: list[dict[str, object]] = []
    for row in output_rows(payload):
        code = _code(row) or fallback_code.zfill(6)
        if not code.strip("0"):
            continue
        close = clean_number(row.get("stck_prpr"))
        open_price = clean_number(row.get("stck_oprc"), close)
        high = clean_number(row.get("stck_hgpr"), close)
        low = clean_number(row.get("stck_lwpr"), close)
        volume = clean_number(row.get("acml_vol"))
        trading_value = clean_number(row.get("acml_tr_pbmn"))
        change_rate = clean_number(row.get("prdy_ctrt"))
        prev_close = close / (1.0 + change_rate / 100.0) if close and change_rate > -99.0 else clean_number(row.get("stck_sdpr"), close)
        rows.append(
            {
                "date": today.isoformat(),
                "code": code,
                "name": _name(row) if _name(row) != code else fallback_name or code,
                "market": row.get("rprs_mrkt_kor_name") or "KIS",
                "close": round(close),
                "prev_close": round(prev_close),
                "open": round(open_price),
                "high": round(high),
                "low": round(low),
                "volume": round(volume),
                "trading_value": round(trading_value or close * volume),
                "change_rate": change_rate,
                "foreign_net_value": "",
                "institution_net_value": "",
                "volume_ratio": "",
                "value_ratio": "",
            }
        )
    return rows


def load_market_csv_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for row in read_csv_rows(path):
        code = first_value(row, ["code", "종목코드", "단축코드"]).zfill(6)
        if not code.strip("0"):
            continue
        rows.append({field: row.get(field, "") for field in MARKET_FIELDNAMES} | {"code": code})
    return rows


def normalize_live_rows(rows: list[dict[str, object]], existing_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [dict(row) for row in rows]


def merge_market_rows(rows: list[dict[str, object]], prefer_latest: bool = False) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    latest_fields = {"date", "market", "close", "prev_close", "open", "high", "low", "volume", "trading_value", "change_rate"}
    for row in rows:
        code = str(row.get("code") or "").zfill(6)
        if not code.strip("0"):
            continue
        item = merged.setdefault(code, {**row, "code": code})
        for key, value in row.items():
            if prefer_latest and key in latest_fields and value not in {"", 0, 0.0, None}:
                item[key] = value
                continue
            if key not in item or item.get(key) in {"", 0, 0.0, None}:
                item[key] = value
        for key in ("foreign_net_value", "institution_net_value"):
            if row.get(key) not in {"", None}:
                item[key] = row[key]
        for key in ("volume_ratio", "value_ratio"):
            if clean_number(row.get(key)) > clean_number(item.get(key)):
                item[key] = row[key]
    return list(merged.values())


def save_market_csv(path: Path, rows: list[dict[str, object]]) -> Path:
    write_csv(path, rows, MARKET_FIELDNAMES)
    return path
