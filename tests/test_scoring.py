from __future__ import annotations

import unittest
from datetime import date, timedelta

from models import HistoryBar, StockSnapshot
from scoring import ForceTracker


def snapshot(
    code: str,
    name: str,
    close: float,
    volume: float,
    trading_value: float,
    foreign: float,
    institution: float,
    warning: str = "",
) -> StockSnapshot:
    return StockSnapshot(
        code=code,
        name=name,
        market="KOSPI",
        trade_date=date(2026, 6, 19),
        open=close * 0.98,
        high=close,
        low=close * 0.94,
        close=close,
        prev_close=close * 0.95,
        volume=volume,
        trading_value=trading_value,
        change_rate=5.0,
        foreign_net_value=foreign,
        institution_net_value=institution,
        foreign_net_available=True,
        institution_net_available=True,
        warning=warning,
    )


def history(code: str, close: float, volume: float) -> list[HistoryBar]:
    start = date(2026, 5, 20)
    return [
        HistoryBar(
            code=code,
            trade_date=start + timedelta(days=idx),
            open=close * 0.92,
            high=close * 0.96,
            low=close * 0.9,
            close=close * 0.94,
            volume=volume,
            trading_value=close * volume,
        )
        for idx in range(20)
    ]


class ForceTrackerTest(unittest.TestCase):
    def test_top_rank_prefers_strong_foreign_volume_breakout(self) -> None:
        strong = snapshot("000660", "SK하이닉스", 268500, 6_000_000, 400_000_000_000, 38_000_000_000, 20_000_000_000)
        normal = snapshot("005930", "삼성전자", 72500, 1_000_000, 72_500_000_000, 1_000_000_000, 500_000_000)
        results = ForceTracker(
            [normal, strong],
            {
                "000660": history("000660", 240000, 1_000_000),
                "005930": history("005930", 70000, 900_000),
            },
        ).score_all(limit=2)
        self.assertEqual(results[0].code, "000660")
        self.assertGreaterEqual(results[0].score, 70)
        self.assertIn("외인급매수", results[0].tags)

    def test_risk_penalty_lowers_warning_stock(self) -> None:
        warned = snapshot("123456", "위험종목", 10000, 1_000_000, 10_000_000_000, 5_000_000_000, 2_000_000_000, "투자경고")
        score = ForceTracker([warned], {}).score_snapshot(warned)
        self.assertLess(score.score, 80)
        self.assertTrue(score.penalties)


if __name__ == "__main__":
    unittest.main()
