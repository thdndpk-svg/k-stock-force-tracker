from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_CASH = 10_000_000


def new_portfolio(initial_cash: int = DEFAULT_CASH) -> dict[str, Any]:
    cash = max(0, int(initial_cash))
    return {
        "initial_cash": cash,
        "cash": cash,
        "realized_pnl": 0.0,
        "holdings": {},
        "trades": [],
        "updated_at": int(time.time()),
    }


def load_portfolio(path: Path, initial_cash: int = DEFAULT_CASH) -> dict[str, Any]:
    if not path.exists():
        return new_portfolio(initial_cash)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return new_portfolio(initial_cash)
    portfolio = new_portfolio(int(data.get("initial_cash") or initial_cash))
    portfolio.update({key: data.get(key, portfolio[key]) for key in portfolio})
    portfolio["holdings"] = data.get("holdings") if isinstance(data.get("holdings"), dict) else {}
    portfolio["trades"] = data.get("trades") if isinstance(data.get("trades"), list) else []
    return portfolio


def save_portfolio(path: Path, portfolio: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    portfolio["updated_at"] = int(time.time())
    path.write_text(json.dumps(portfolio, ensure_ascii=False, indent=2), encoding="utf-8")


def _quote_value(quote: dict[str, Any], key: str, default: Any = "") -> Any:
    return quote.get(key, default)


def buy(portfolio: dict[str, Any], quote: dict[str, Any], amount: float) -> dict[str, Any]:
    item = deepcopy(portfolio)
    price = float(_quote_value(quote, "close", 0.0) or 0.0)
    if price <= 0:
        raise ValueError("현재가가 없어 매수할 수 없습니다.")
    budget = min(float(amount), float(item.get("cash", 0.0)))
    qty = int(budget // price)
    if qty <= 0:
        raise ValueError("예수금 또는 투자금이 부족합니다.")
    cost = qty * price
    code = str(_quote_value(quote, "code"))
    holding = dict(item["holdings"].get(code, {}))
    old_qty = int(holding.get("qty", 0))
    old_cost = old_qty * float(holding.get("avg_price", 0.0))
    new_qty = old_qty + qty
    avg_price = (old_cost + cost) / new_qty
    holding.update(
        {
            "code": code,
            "name": _quote_value(quote, "name", code),
            "qty": new_qty,
            "avg_price": avg_price,
            "cost": old_cost + cost,
            "signal": _quote_value(quote, "tradeAction", ""),
            "setup": (_quote_value(quote, "pro", {}) or {}).get("bias", ""),
            "last_price": price,
            "last_buy_at": int(time.time()),
        }
    )
    item["holdings"][code] = holding
    item["cash"] = float(item.get("cash", 0.0)) - cost
    item["trades"].insert(
        0,
        {
            "time": int(time.time()),
            "side": "BUY",
            "code": code,
            "name": holding["name"],
            "qty": qty,
            "price": price,
            "amount": cost,
            "signal": holding.get("signal", ""),
            "setup": holding.get("setup", ""),
            "cash_after": item["cash"],
        },
    )
    item["trades"] = item["trades"][:200]
    return item


def sell(portfolio: dict[str, Any], quote: dict[str, Any], qty: int | None = None, sell_all: bool = False) -> dict[str, Any]:
    item = deepcopy(portfolio)
    code = str(_quote_value(quote, "code"))
    holding = dict(item["holdings"].get(code, {}))
    current_qty = int(holding.get("qty", 0))
    if current_qty <= 0:
        raise ValueError("보유 수량이 없습니다.")
    price = float(_quote_value(quote, "close", 0.0) or 0.0)
    if price <= 0:
        raise ValueError("현재가가 없어 매도할 수 없습니다.")
    sell_qty = current_qty if sell_all or qty is None else min(current_qty, max(0, int(qty)))
    if sell_qty <= 0:
        raise ValueError("매도 수량이 올바르지 않습니다.")
    avg_price = float(holding.get("avg_price", 0.0))
    proceeds = sell_qty * price
    pnl = (price - avg_price) * sell_qty
    item["cash"] = float(item.get("cash", 0.0)) + proceeds
    item["realized_pnl"] = float(item.get("realized_pnl", 0.0)) + pnl
    remaining = current_qty - sell_qty
    if remaining:
        holding["qty"] = remaining
        holding["cost"] = avg_price * remaining
        holding["last_price"] = price
        item["holdings"][code] = holding
    else:
        item["holdings"].pop(code, None)
    item["trades"].insert(
        0,
        {
            "time": int(time.time()),
            "side": "SELL",
            "code": code,
            "name": _quote_value(quote, "name", holding.get("name", code)),
            "qty": sell_qty,
            "price": price,
            "amount": proceeds,
            "pnl": pnl,
            "pnl_pct": ((price / avg_price - 1.0) * 100.0) if avg_price else 0.0,
            "signal": holding.get("signal", ""),
            "setup": holding.get("setup", ""),
            "cash_after": item["cash"],
        },
    )
    item["trades"] = item["trades"][:200]
    return item


def evaluate(portfolio: dict[str, Any], quotes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    holdings = []
    market_value = 0.0
    cost_total = 0.0
    unrealized_pnl = 0.0
    for code, holding in portfolio.get("holdings", {}).items():
        quote = quotes.get(code, {})
        price = float(quote.get("close") or holding.get("last_price") or holding.get("avg_price") or 0.0)
        qty = int(holding.get("qty", 0))
        avg_price = float(holding.get("avg_price", 0.0))
        value = qty * price
        cost = qty * avg_price
        pnl = value - cost
        market_value += value
        cost_total += cost
        unrealized_pnl += pnl
        holdings.append(
            {
                **holding,
                "current_price": price,
                "market_value": value,
                "cost": cost,
                "pnl": pnl,
                "pnl_pct": ((price / avg_price - 1.0) * 100.0) if avg_price else 0.0,
                "current_signal": quote.get("tradeAction", ""),
                "current_setup": (quote.get("pro", {}) or {}).get("bias", ""),
            }
        )
    initial_cash = float(portfolio.get("initial_cash", DEFAULT_CASH) or DEFAULT_CASH)
    cash = float(portfolio.get("cash", 0.0))
    total_equity = cash + market_value
    sell_trades = [trade for trade in portfolio.get("trades", []) if trade.get("side") == "SELL"]
    wins = [trade for trade in sell_trades if float(trade.get("pnl", 0.0)) > 0]
    return {
        "initialCash": initial_cash,
        "cash": cash,
        "marketValue": market_value,
        "totalEquity": total_equity,
        "costTotal": cost_total,
        "unrealizedPnl": unrealized_pnl,
        "realizedPnl": float(portfolio.get("realized_pnl", 0.0)),
        "totalPnl": total_equity - initial_cash,
        "returnPct": ((total_equity / initial_cash - 1.0) * 100.0) if initial_cash else 0.0,
        "winRate": (len(wins) / len(sell_trades) * 100.0) if sell_trades else 0.0,
        "closedTrades": len(sell_trades),
        "holdings": sorted(holdings, key=lambda item: item["market_value"], reverse=True),
        "trades": portfolio.get("trades", [])[:30],
        "updatedAt": portfolio.get("updated_at", 0),
    }
