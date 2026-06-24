from __future__ import annotations

import unittest
from datetime import date, timedelta

from models import HistoryBar, ScoreBreakdown
from professional_analysis import build_professional_view


def trend_bars(code: str = "000001") -> list[HistoryBar]:
    start = date(2026, 1, 1)
    bars: list[HistoryBar] = []
    for idx in range(80):
        close = 10_000 + idx * 80
        bars.append(
            HistoryBar(
                code=code,
                trade_date=start + timedelta(days=idx),
                open=close - 40,
                high=close + 120,
                low=close - 160,
                close=close,
                volume=500_000 + idx * 2_000,
                trading_value=close * (500_000 + idx * 2_000),
            )
        )
    return bars


class ProfessionalAnalysisTest(unittest.TestCase):
    def test_builds_practical_trade_plan_with_indicators(self) -> None:
        bars = trend_bars()
        item = ScoreBreakdown(
            code="000001",
            name="테스트",
            market="KOSPI",
            score=75,
            discovery_score=82,
            close=16_400,
            change_rate=4.0,
            trading_value=18_000_000_000,
            volume_ratio=2.0,
            foreign_net_value=1_000_000_000,
            institution_net_value=600_000_000,
            flow_ratio=0.09,
            bottom_score=62,
            bottom_support=15_500,
        )

        pro = build_professional_view(item, bars)

        self.assertIn(pro["bias"], {"공격매수 후보", "분할매수 후보", "관심/대기", "리스크관리", "보류"})
        self.assertGreater(pro["target1"], pro["entry"])
        self.assertLess(pro["stop"], pro["entry"])
        self.assertGreaterEqual(pro["riskReward"], 0)
        self.assertIn("rsi14", pro["indicators"])
        self.assertIn("macdHist", pro["indicators"])
        self.assertTrue(pro["checklist"])


if __name__ == "__main__":
    unittest.main()
