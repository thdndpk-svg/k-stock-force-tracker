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
    bottom_score: float = 0.0
    bottom_stage: str = ""
    bottom_support: float = 0.0
    bottom_long_line: float = 0.0
    bottom_touch_count: int = 0
    bottom_reasons: list[str] = field(default_factory=list)
    bottom_warnings: list[str] = field(default_factory=list)
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
    def trade_action(self) -> str:
        if self.is_risky:
            return "매도"
        if self.change_rate >= 18.0:
            return "매도"
        if any("분할매도" in warning for warning in self.bottom_warnings):
            return "매도"
        if any("당일 과열" in penalty for penalty in self.penalties):
            return "매도"
        if self.discovery_score >= 72 and self.score >= 62 and self.risk_label == "낮음":
            return "매수권장"
        if self.bottom_score >= 60 and self.change_rate <= 11.0 and self.risk_label == "낮음":
            return "매수권장"
        return "보류"

    @property
    def trade_reason(self) -> str:
        if self.trade_action == "매도":
            if self.is_risky:
                return "위험 지정 또는 감점 신호가 있어 방어 우선"
            if self.change_rate >= 18.0 or any("분할매도" in warning for warning in self.bottom_warnings):
                return "당일 급등 구간이라 추격보다 분할매도 주의"
            return "과열 신호 확인"
        if self.trade_action == "매수권장":
            if self.bottom_score >= 60:
                return "바닥매집 후보와 수급/거래 신호 동시 확인"
            return "발굴 점수와 수급 신호가 매수권"
        if self.discovery_score >= 55 or self.bottom_score >= 45:
            return "관심권이지만 추가 확인 필요"
        return "매수 신호 부족"

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
