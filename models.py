from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class StockSnapshot:
    code: str
    name: str
    market: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    prev_close: float
    volume: float
    trading_value: float
    change_rate: float
    foreign_net_value: float = 0.0
    institution_net_value: float = 0.0
    individual_net_value: float = 0.0
    foreign_net_available: bool = False
    institution_net_available: bool = False
    individual_net_available: bool = False
    foreign_holding_rate: float | None = None
    short_sale_value: float = 0.0
    warning: str = ""
    volume_ratio_hint: float = 0.0
    value_ratio_hint: float = 0.0


@dataclass(frozen=True)
class HistoryBar:
    code: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    trading_value: float


@dataclass
class ScoreBreakdown:
    code: str
    name: str
    market: str
    score: float
    close: float
    change_rate: float
    trading_value: float
    volume_ratio: float
    foreign_net_value: float
    institution_net_value: float
    discovery_score: float = 0.0
    flow_ratio: float = 0.0
    sector: str = "기타"
    theme: str = "기타"
    us_impact: float = 0.0
    issue_score: float = 0.0
    foreign_net_available: bool = False
    institution_net_available: bool = False
    tags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)

    @property
    def grade(self) -> str:
        if self.score >= 85:
            return "A+"
        if self.score >= 75:
            return "A"
        if self.score >= 65:
            return "B"
        if self.score >= 55:
            return "C"
        return "관망"

    @property
    def recommendation(self) -> str:
        if self.is_risky:
            return "위험"
        if self.score >= 80:
            return "강한매수"
        if self.score >= 65:
            return "매수"
        if self.score >= 50:
            return "관심"
        return "관망"

    @property
    def is_risky(self) -> bool:
        risk_words = ("거래정지", "관리", "투자경고", "위험", "공매도")
        return any(any(word in penalty for word in risk_words) for penalty in self.penalties)

    @property
    def risk_label(self) -> str:
        if self.is_risky:
            return "높음"
        if self.score < 45:
            return "보통"
        return "낮음"
