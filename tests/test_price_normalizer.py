from __future__ import annotations

import unittest
from datetime import date

from models import HistoryBar, StockSnapshot
from price_normalizer import normalize_history_to_snapshots, normalize_snapshots_to_history


class PriceNormalizerTest(unittest.TestCase):
    def test_keeps_kis_quote_and_scales_history_to_quote_unit(self) -> None:
        snapshot = StockSnapshot(
            code="005930",
            name="삼성전자",
            market="KIS",
            trade_date=date(2026, 6, 19),
            open=362_500,
            high=362_500,
            low=362_500,
            close=362_500,
            prev_close=346_490,
            volume=1_000_000,
            trading_value=362_500_000_000,
            change_rate=4.62,
            foreign_net_value=88_000_000_000,
            institution_net_value=19_000_000_000,
        )
        history = {
            "005930": [
                HistoryBar(
                    code="005930",
                    trade_date=date(2026, 6, 18),
                    open=35_000,
                    high=36_500,
                    low=34_800,
                    close=36_000,
                    volume=1_000_000,
                    trading_value=36_000_000_000,
                )
            ]
        }

        [item] = normalize_snapshots_to_history([snapshot], history)
        normalized_history = normalize_history_to_snapshots([snapshot], history)

        self.assertEqual(item.close, 362_500)
        self.assertEqual(item.trading_value, 362_500_000_000)
        self.assertEqual(item.foreign_net_value, 88_000_000_000)
        self.assertEqual(normalized_history["005930"][-1].close, 360_000)
        self.assertEqual(normalized_history["005930"][-1].trading_value, 360_000_000_000)


if __name__ == "__main__":
    unittest.main()
