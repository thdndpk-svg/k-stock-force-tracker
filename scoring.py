from __future__ import annotations

from math import log10
from statistics import mean

from models import HistoryBar, ScoreBreakdown, StockSnapshot
from theme_engine import classify_stock, is_etf_like, theme_us_impact


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def ratio_score(value: float, target: float, cap: float = 1.0) -> float:
    if target <= 0:
        return 0.0
    return clamp(value / target, 0.0, cap)


def average(values: list[float], fallback: float = 0.0) -> float:
    return mean(values) if values else fallback


class ForceTracker:
    def __init__(
        self,
        snapshots: list[StockSnapshot],
        history: dict[str, list[HistoryBar]] | None = None,
        global_signals: dict[str, dict[str, float | str]] | None = None,
    ):
        self.snapshots = snapshots
        self.history = history or {}
        self.global_signals = global_signals or {}
        self.max_foreign = max(
            (max(0.0, s.foreign_net_value) for s in snapshots if s.foreign_net_available),
            default=1.0,
        )
        self.max_institution = max(
            (max(0.0, s.institution_net_value) for s in snapshots if s.institution_net_available),
            default=1.0,
        )
        self.max_value = max((s.trading_value for s in snapshots), default=1.0)

    def score_all(self, limit: int = 30) -> list[ScoreBreakdown]:
        scored = [self.score_snapshot(item) for item in self.snapshots]
        scored.sort(key=lambda item: (item.discovery_score, item.score), reverse=True)
        return scored[:limit]

    def score_snapshot(self, item: StockSnapshot) -> ScoreBreakdown:
        bars = self.history.get(item.code, [])
        recent = bars[-20:]
        avg_volume = average([bar.volume for bar in recent], item.volume)
        avg_value = average([bar.trading_value for bar in recent], item.trading_value)
        volume_ratio = item.volume_ratio_hint if item.volume_ratio_hint > 0 else item.volume / avg_volume if avg_volume else 1.0
        value_ratio = item.value_ratio_hint if item.value_ratio_hint > 0 else item.trading_value / avg_value if avg_value else 1.0
        close_strength = (item.close - item.low) / (item.high - item.low) if item.high > item.low else 0.5
        previous_high = max((bar.high for bar in recent[:-1]), default=item.high)
        previous_close_high = max((bar.close for bar in recent[:-1]), default=item.close)
        sector, theme, issue_tags = classify_stock(item.name)
        us_impact = theme_us_impact(theme, self.global_signals)
        positive_foreign = max(0.0, item.foreign_net_value) if item.foreign_net_available else 0.0
        positive_institution = max(0.0, item.institution_net_value) if item.institution_net_available else 0.0
        supply_value = positive_foreign + positive_institution
        flow_ratio = supply_value / item.trading_value if item.trading_value else 0.0

        points = 0.0
        reasons: list[str] = []
        tags: list[str] = []
        penalties: list[str] = []

        foreign_focus = positive_foreign / item.trading_value if item.trading_value and item.foreign_net_available else 0.0
        institution_focus = positive_institution / item.trading_value if item.trading_value and item.institution_net_available else 0.0

        foreign_points = 20.0 * ratio_score(foreign_focus, 0.06) if item.foreign_net_available else 0.0
        points += foreign_points
        if foreign_points >= 12:
            tags.append("외인급매수")
            reasons.append(f"외국인 수급집중 {foreign_focus * 100:.1f}%")

        inst_points = 16.0 * ratio_score(institution_focus, 0.05) if item.institution_net_available else 0.0
        points += inst_points
        if item.foreign_net_available and item.institution_net_available and item.foreign_net_value > 0 and item.institution_net_value > 0:
            points += 8.0
            tags.append("쌍끌이")
            reasons.append("외국인+기관 동반 순매수")
        elif not item.institution_net_available:
            penalties.append("기관 순매수 데이터 없음")

        focus_points = 10.0 * ratio_score(flow_ratio, 0.09)
        points += focus_points
        if focus_points >= 6:
            tags.append("수급집중")
            reasons.append(f"거래대금 대비 순매수 {flow_ratio * 100:.1f}%")

        volume_points = 17.0 * clamp((volume_ratio - 1.0) / 2.5, 0.0, 1.0)
        points += volume_points
        if volume_ratio >= 1.8:
            tags.append("거래량이상")
            reasons.append(f"20일 평균 대비 거래량 {volume_ratio:.1f}배")

        value_points = 8.0 * clamp((value_ratio - 1.0) / 2.5, 0.0, 1.0)
        liquidity_points = 7.0 * ratio_score(log10(max(item.trading_value, 1.0)), 11.0)
        points += value_points + liquidity_points
        if value_ratio >= 1.8:
            reasons.append(f"20일 평균 대비 거래대금 {value_ratio:.1f}배")

        if item.close > previous_high:
            points += 11.0
            tags.append("전고점돌파")
            reasons.append("20일 전고점 돌파")
        elif item.close > previous_close_high:
            points += 9.0
            tags.append("돌파초입")
            reasons.append("20일 종가 고점 접근/돌파")

        strength_points = 6.0 * close_strength
        points += strength_points
        if close_strength >= 0.75:
            tags.append("고가마감")
            reasons.append(f"종가 위치 강함 {close_strength * 100:.0f}%")

        if 1.5 <= item.change_rate <= 11.0:
            momentum_points = 9.0 * (1.0 - abs(item.change_rate - 5.0) / 8.0)
            points += max(2.0, momentum_points)
            tags.append("상승초입")
        elif item.change_rate > 18.0:
            points -= 10.0
            penalties.append("당일 과열 상승")
        elif item.change_rate < -4.0:
            points -= 8.0
            penalties.append("당일 약세")

        issue_points = min(6.0, len(issue_tags) * 2.0)
        points += issue_points
        if issue_tags:
            tags.append(sector)
            reasons.append(f"{theme} 이슈 감응: {', '.join(issue_tags[:2])}")

        points += us_impact
        if us_impact >= 2.0:
            tags.append("미국순풍")
            reasons.append(f"미국 관련 지표 우호 {us_impact:+.1f}")
        elif us_impact <= -2.0:
            penalties.append(f"미국 관련 지표 부담 {us_impact:+.1f}")

        if 10_000_000_000 <= item.trading_value <= 300_000_000_000:
            points += 6.0
            tags.append("발굴권")
        elif item.trading_value > 1_000_000_000_000:
            points -= 5.0
            penalties.append("대형주 노출 과다")

        if item.short_sale_value and item.short_sale_value > item.trading_value * 0.12:
            points -= 7.0
            penalties.append("공매도 비중 부담")
        if item.trading_value < 3_000_000_000:
            points -= 12.0
            penalties.append("거래대금 부족")
        if any(word in item.warning for word in ["관리", "투자경고", "거래정지", "주의"]):
            points -= 25.0
            penalties.append(f"위험 지정: {item.warning}")
        if is_etf_like(item.name):
            points -= 35.0
            penalties.append("ETF/ETN/레버리지 상품 제외 권장")

        score = round(clamp(points), 1)
        discovery_score = round(
            clamp(
                score
                + min(14.0, flow_ratio * 140.0)
                + min(10.0, max(0.0, volume_ratio - 1.0) * 3.0)
                - (8.0 if item.trading_value > 1_000_000_000_000 else 0.0)
            ),
            1,
        )

        return ScoreBreakdown(
            code=item.code,
            name=item.name,
            market=item.market,
            score=score,
            close=item.close,
            change_rate=item.change_rate,
            trading_value=item.trading_value,
            volume_ratio=round(volume_ratio, 2),
            foreign_net_value=item.foreign_net_value,
            institution_net_value=item.institution_net_value,
            discovery_score=discovery_score,
            flow_ratio=round(flow_ratio, 4),
            sector=sector,
            theme=theme,
            us_impact=round(us_impact, 1),
            issue_score=round(issue_points, 1),
            foreign_net_available=item.foreign_net_available,
            institution_net_available=item.institution_net_available,
            tags=tags[:5],
            reasons=reasons[:6],
            penalties=penalties[:4],
        )
