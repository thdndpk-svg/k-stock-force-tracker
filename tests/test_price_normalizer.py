from __future__ import annotations

import unittest
from datetime import date

from models import HistoryBar, StockSnapshot
from price_normalizer import normalize_snapshots_to_history


class PriceNormalizerTest(unittest.TestCase):
    def test_normalizes_kis_tenfold_price_against_history(self) -> None:
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

        self.assertEqual(item.close, 36_250)
        self.assertEqual(item.trading_value, 36_250_000_000)
        self.assertEqual(item.foreign_net_value, 8_800_000_000)


if __name__ == "__main__":
    unittest.main()
