from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
import re

from csv_utils import clean_number, first_value, parse_date, read_csv_rows
from models import HistoryBar, StockSnapshot


def infer_date_from_path(path: Path) -> date:
    match = re.search(r"(20\d{6})", path.name)
    if match:
        return parse_date(match.group(1))
    return date.today()


def discover_market_csv(data_dir: Path) -> Path:
    for preferred in (data_dir / "kis_market.csv", data_dir / "sample_market.csv"):
        if preferred.exists():
            return preferred
    candidates = [
        path
        for path in data_dir.glob("*.csv")
        if path.is_file() and not path.name.startswith(".")
    ]
    if not candidates:
        raise FileNotFoundError(f"전종목 시세 CSV가 없습니다: {preferred}")
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def discover_search_universe_csv(data_dir: Path) -> Path | None:
    candidates = [
        path
        for pattern in ("data_*.csv", "krx_market_*.csv")
        for path in data_dir.glob(pattern)
        if path.is_file()
    ]
    if not candidates:
        return None
    candidates = sorted(set(candidates), key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    return candidates[0]


def discover_investor_csv(data_dir: Path) -> Path | None:
    krx_dir = data_dir / "krx"
    preferred = krx_dir / "investor_net_buy.csv"
    if preferred.exists():
        return preferred
    if not krx_dir.exists():
        return None
    candidates = [
        path
        for pattern in ("investor*.csv", "*순매수*.csv", "*.csv")
        for path in krx_dir.glob(pattern)
        if path.is_file()
    ]
    if not candidates:
        return None
    candidates = sorted(set(candidates), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def discover_investor_csvs(data_dir: Path) -> list[Path]:
    krx_dir = data_dir / "krx"
    if not krx_dir.exists():
        return []
    candidates = [
        path
        for pattern in ("*기관*.csv", "*institution*.csv", "*inst*.csv", "*외국*.csv", "*foreign*.csv", "investor*.csv", "*순매수*.csv", "*.csv")
        for path in krx_dir.glob(pattern)
        if path.is_file()
    ]
    return sorted(set(candidates), key=lambda path: path.stat().st_mtime)


def investor_file_kind(path: Path) -> str:
    name = path.name.lower()
    if "기관" in name or "institution" in name or "inst" in name:
        return "institution"
    if "개인" in name or "individual" in name:
        return "individual"
    return "foreign"


def load_market_snapshots(path: Path) -> list[StockSnapshot]:
    rows = read_csv_rows(path)
    snapshots: list[StockSnapshot] = []
    default_date = infer_date_from_path(path)
    for row in rows:
        code = first_value(row, ["code", "종목코드", "단축코드", "ISU_SRT_CD"]).zfill(6)
        if not code.strip("0"):
            continue
        name = first_value(row, ["name", "종목명", "한글 종목명", "ISU_ABBRV"], code)
        date_text = first_value(row, ["date", "일자", "기준일", "TRD_DD"])
        trade_date = parse_date(date_text) if date_text else default_date
        close = clean_number(first_value(row, ["close", "종가", "TDD_CLSPRC"]))
        prev_close = clean_number(first_value(row, ["prev_close", "전일종가", "CMPPREVDD_PRC"]), close)
        open_price = clean_number(first_value(row, ["open", "시가", "TDD_OPNPRC"]), close)
        high = clean_number(first_value(row, ["high", "고가", "TDD_HGPRC"]), close)
        low = clean_number(first_value(row, ["low", "저가", "TDD_LWPRC"]), close)
        volume = clean_number(first_value(row, ["volume", "거래량", "ACC_TRDVOL"]))
        trading_value = clean_number(first_value(row, ["trading_value", "거래대금", "ACC_TRDVAL"]))
        if not trading_value:
            trading_value = close * volume
        change_rate = clean_number(first_value(row, ["change_rate", "등락률", "FLUC_RT"]))
        if change_rate == 0 and prev_close:
            change_rate = (close / prev_close - 1.0) * 100.0
        foreign_text = first_value(row, ["foreign_net_value", "외국인순매수", "외국인 순매수", "FORN_NTBY_TRDVAL"])
        institution_text = first_value(row, ["institution_net_value", "기관순매수", "기관 순매수", "ORG_NTBY_TRDVAL"])
        individual_text = first_value(row, ["individual_net_value", "개인순매수", "개인 순매수"])
        snapshots.append(
            StockSnapshot(
                code=code,
                name=name,
                market=first_value(row, ["market", "시장구분", "MKT_NM"], "KOSPI"),
                trade_date=trade_date,
                open=open_price,
                high=high,
                low=low,
                close=close,
                prev_close=prev_close,
                volume=volume,
                trading_value=trading_value,
                change_rate=change_rate,
                foreign_net_value=clean_number(foreign_text),
                institution_net_value=clean_number(institution_text),
                individual_net_value=clean_number(individual_text),
                foreign_net_available=foreign_text != "",
                institution_net_available=institution_text != "",
                individual_net_available=individual_text != "",
                foreign_holding_rate=clean_optional(first_value(row, ["foreign_holding_rate", "외국인보유율", "외국인 보유율"])),
                short_sale_value=clean_number(first_value(row, ["short_sale_value", "공매도거래대금", "공매도 거래대금"])),
                warning=first_value(row, ["warning", "투자주의", "지정내역", "비고"]),
                volume_ratio_hint=clean_number(first_value(row, ["volume_ratio", "volume_ratio_hint", "vol_inrt", "nday_vol_tnrt"])),
                value_ratio_hint=clean_number(first_value(row, ["value_ratio", "value_ratio_hint", "tr_pbmn_tnrt", "nday_tr_pbmn_tnrt"])),
            )
        )
    return snapshots


def clean_optional(value: str) -> float | None:
    if value.strip() == "":
        return None
    return clean_number(value)


def load_history_dir(path: Path) -> dict[str, list[HistoryBar]]:
    history: dict[str, list[HistoryBar]] = defaultdict(list)
    if not path.exists():
        return history
    for csv_path in sorted(path.glob("*.csv")):
        for row in read_csv_rows(csv_path):
            code = first_value(row, ["code", "종목코드"], csv_path.stem).zfill(6)
            close = clean_number(first_value(row, ["close", "종가"]))
            volume = clean_number(first_value(row, ["volume", "거래량"]))
            trading_value = clean_number(first_value(row, ["trading_value", "거래대금"]))
            if not trading_value:
                trading_value = close * volume
            history[code].append(
                HistoryBar(
                    code=code,
                    trade_date=parse_date(first_value(row, ["date", "일자"])),
                    open=clean_number(first_value(row, ["open", "시가"]), close),
                    high=clean_number(first_value(row, ["high", "고가"]), close),
                    low=clean_number(first_value(row, ["low", "저가"]), close),
                    close=close,
                    volume=volume,
                    trading_value=trading_value,
                )
            )
    for code in history:
        history[code].sort(key=lambda bar: bar.trade_date)
    return dict(history)


def merge_krx_investor_csv(snapshots: list[StockSnapshot], path: Path) -> list[StockSnapshot]:
    if not path or not path.exists():
        return snapshots
    kind = investor_file_kind(path)
    lookup: dict[str, float] = {}
    for row in read_csv_rows(path):
        code = first_value(row, ["code", "종목코드", "단축코드", "ISU_SRT_CD"]).zfill(6)
        if not code.strip("0"):
            continue
        lookup[code] = clean_number(first_value(row, ["net_value", "foreign_net_value", "institution_net_value", "외국인순매수", "외국인 순매수", "기관순매수", "기관 순매수", "FORN_NTBY_TRDVAL", "ORG_NTBY_TRDVAL", "거래대금_순매수"]))
    merged: list[StockSnapshot] = []
    for item in snapshots:
        if item.code not in lookup:
            merged.append(item)
            continue
        value = lookup[item.code]
        updates = item.__dict__.copy()
        if kind == "institution":
            updates["institution_net_value"] = value
            updates["institution_net_available"] = True
        elif kind == "individual":
            updates["individual_net_value"] = value
            updates["individual_net_available"] = True
        else:
            updates["foreign_net_value"] = value
            updates["foreign_net_available"] = True
        merged.append(
            StockSnapshot(**updates)
        )
    return merged


def merge_krx_investor_csvs(snapshots: list[StockSnapshot], paths: list[Path]) -> list[StockSnapshot]:
    merged = snapshots
    for path in paths:
        merged = merge_krx_investor_csv(merged, path)
    return merged
