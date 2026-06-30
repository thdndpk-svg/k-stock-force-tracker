from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from paper_trading import buy, buy_quantity, evaluate, load_portfolio, new_portfolio, save_portfolio, sell


QUOTE = {
    "code": "000660",
    "name": "SK하이닉스",
    "close": 250_000,
    "tradeAction": "매수권장",
    "pro": {"bias": "분할매수 후보"},
}


class PaperTradingTest(unittest.TestCase):
    def test_buy_and_sell_updates_virtual_account(self) -> None:
        portfolio = new_portfolio(10_000_000)

        portfolio = buy(portfolio, QUOTE, 1_000_000)
        self.assertEqual(portfolio["holdings"]["000660"]["qty"], 4)
        self.assertEqual(portfolio["cash"], 9_000_000)

        sell_quote = {**QUOTE, "close": 270_000}
        portfolio = sell(portfolio, sell_quote, qty=2)
        self.assertEqual(portfolio["holdings"]["000660"]["qty"], 2)
        self.assertEqual(portfolio["realized_pnl"], 40_000)

        summary = evaluate(portfolio, {"000660": sell_quote})
        self.assertEqual(summary["marketValue"], 540_000)
        self.assertEqual(summary["totalEquity"], 10_080_000)
        self.assertGreater(summary["returnPct"], 0)

    def test_portfolio_persists_to_local_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paper.json"
            portfolio = buy(new_portfolio(1_000_000), QUOTE, 500_000)
            save_portfolio(path, portfolio)

            loaded = load_portfolio(path)

            self.assertEqual(loaded["cash"], portfolio["cash"])
            self.assertEqual(loaded["holdings"]["000660"]["qty"], 2)

    def test_buy_quantity_and_sell_partial(self) -> None:
        quote = {"code": "000660", "name": "SK하이닉스", "close": 2_622_000, "tradeAction": "보류"}
        portfolio = buy_quantity(new_portfolio(10_000_000), quote, 2)

        self.assertEqual(portfolio["holdings"]["000660"]["qty"], 2)
        self.assertEqual(portfolio["cash"], 4_756_000)

        portfolio = sell(portfolio, {**quote, "close": 2_700_000}, qty=1)

        self.assertEqual(portfolio["holdings"]["000660"]["qty"], 1)
        self.assertEqual(portfolio["cash"], 7_456_000)
        self.assertGreater(portfolio["realized_pnl"], 0)

    def test_buy_quantity_rejects_insufficient_cash(self) -> None:
        quote = {"code": "000660", "name": "SK하이닉스", "close": 2_622_000}

        with self.assertRaises(ValueError):
            buy_quantity(new_portfolio(1_000_000), quote, 1)


if __name__ == "__main__":
    unittest.main()
