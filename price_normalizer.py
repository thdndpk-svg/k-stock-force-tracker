from __future__ import annotations

from dataclasses import replace

from models import HistoryBar, StockSnapshot


def price_unit_factor(current: float, reference: float) -> float:
    if current <= 0 or reference <= 0:
        return 1.0
    ratio = current / reference
    if 6.0 <= ratio <= 14.0:
        return 10.0
    if 60.0 <= ratio <= 140.0:
        return 100.0
    if 0.06 <= ratio <= 0.14:
        return 0.1
    if 0.006 <= ratio <= 0.014:
        return 0.01
    return 1.0


def normalize_snapshots_to_history(
    snapshots: list[StockSnapshot],
    history: dict[str, list[HistoryBar]],
) -> list[StockSnapshot]:
    return list(snapshots)


def normalize_history_to_snapshots(
    snapshots: list[StockSnapshot],
    history: dict[str, list[HistoryBar]],
) -> dict[str, list[HistoryBar]]:
    snapshot_by_code = {item.code: item for item in snapshots}
    normalized: dict[str, list[HistoryBar]] = {}
    for code, bars in history.items():
        item = snapshot_by_code.get(code)
        reference = bars[-1].close if bars else 0.0
        factor = price_unit_factor(item.close if item else 0.0, reference)
        if factor == 1.0:
            normalized[code] = list(bars)
            continue
        normalized[code] = [
            replace(
                bar,
                open=bar.open * factor,
                high=bar.high * factor,
                low=bar.low * factor,
                close=bar.close * factor,
                trading_value=bar.trading_value * factor,
            )
            for bar in bars
        ]
    return normalized
