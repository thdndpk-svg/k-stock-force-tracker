from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from data_loader import discover_market_csv
from kis_supply import aggregate_supply_rows


class DataPipelineTest(unittest.TestCase):
    def test_kis_trade_value_scales_to_implied_price_times_quantity(self) -> None:
        payload = {
            "output": [
                {
                    "hts_kor_isnm": "삼성전자",
                    "mksc_shrn_iscd": "005930",
                    "stck_prpr": "362500",
                    "frgn_ntby_qty": "243000",
                    "frgn_ntby_tr_pbmn": "88088",
                }
            ]
        }

        rows = aggregate_supply_rows(payload, "foreign", trade_date=date(2026, 6, 19))

        self.assertEqual(rows[0]["foreign_net_value"], 88_088_000_000)

    def test_discover_market_csv_prefers_kis_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            (data_dir / "sample_market.csv").write_text("code,name\n000001,샘플\n", encoding="utf-8")
            (data_dir / "kis_market.csv").write_text("code,name\n005930,삼성전자\n", encoding="utf-8")

            self.assertEqual(discover_market_csv(data_dir).name, "kis_market.csv")


if __name__ == "__main__":
    unittest.main()
