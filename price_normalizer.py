from __future__ import annotations

from dataclasses import replace

from models import HistoryBar, StockSnapshot


def _price_factor(current: float, reference: float) -> float:
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
    normalized: list[StockSnapshot] = []
    for item in snapshots:
        bars = history.get(item.code, [])
        reference = bars[-1].close if bars else 0.0
        factor = _price_factor(item.close, reference)
        if factor == 1.0:
            normalized.append(item)
            continue
        normalized.append(
            replace(
                item,
                open=item.open / factor,
                high=item.high / factor,
                low=item.low / factor,
                close=item.close / factor,
                prev_close=item.prev_close / factor,
                trading_value=item.trading_value / factor,
                foreign_net_value=item.foreign_net_value / factor,
                institution_net_value=item.institution_net_value / factor,
                individual_net_value=item.individual_net_value / factor,
                short_sale_value=item.short_sale_value / factor,
            )
        )
    return normalized
