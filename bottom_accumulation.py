from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from models import HistoryBar, StockSnapshot


@dataclass(frozen=True)
class BottomAccumulationSignal:
    score: float = 0.0
    stage: str = ""
    support_price: float = 0.0
    long_line: float = 0.0
    touch_count: int = 0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_candidate(self) -> bool:
        return self.score >= 60.0


def _with_current_bar(history: list[HistoryBar], item: StockSnapshot) -> list[HistoryBar]:
    bars = list(history)
    if bars and bars[-1].trade_date == item.trade_date:
        return bars
    bars.append(
        HistoryBar(
            code=item.code,
            trade_date=item.trade_date,
            open=item.open,
            high=item.high,
            low=item.low,
            close=item.close,
            volume=item.volume,
            trading_value=item.trading_value,
        )
    )
    return bars


def _moving_average(values: list[float], period: int) -> float:
    if len(values) < period:
        return 0.0
    return mean(values[-period:])


def score_bottom_accumulation(item: StockSnapshot, history: list[HistoryBar]) -> BottomAccumulationSignal:
    bars = _with_current_bar(history, item)
    if len(bars) < 10:
        return BottomAccumulationSignal(warnings=["차트 데이터 부족"])

    closes = [bar.close for bar in bars if bar.close > 0]
    recent = bars[-60:]
    recent_closes = [bar.close for bar in recent if bar.close > 0]
    recent_lows = [bar.low for bar in recent if bar.low > 0]
    recent_highs = [bar.high for bar in recent if bar.high > 0]
    recent_volumes = [bar.volume for bar in recent if bar.volume > 0]
    if not recent_closes or not recent_lows or not recent_highs:
        return BottomAccumulationSignal(warnings=["가격 데이터 부족"])

    support = min(recent_lows)
    range_high = max(recent_highs)
    current = item.close
    avg_volume = mean(recent_volumes) if recent_volumes else item.volume
    prior_avg_volume = mean(recent_volumes[:-1]) if len(recent_volumes) > 1 else avg_volume

    points = 0.0
    reasons: list[str] = []
    warnings: list[str] = []

    support_gap = (current / support - 1.0) if support else 9.99
    if -0.03 <= support_gap <= 0.18:
        points += 24.0
        reasons.append(f"박스권 하단 지지권 {support_gap * 100:.1f}%")
    elif 0.18 < support_gap <= 0.35:
        points += 10.0
        reasons.append(f"하단 지지권에서 {support_gap * 100:.1f}% 이격")

    touch_count = sum(1 for bar in recent if support and bar.low <= support * 1.08)
    if touch_count >= 3:
        points += 18.0
        reasons.append(f"하단 지지 반복 {touch_count}회")
    elif touch_count >= 2:
        points += 10.0
        reasons.append(f"하단 지지 재확인 {touch_count}회")

    box_width = (range_high / support - 1.0) if support else 9.99
    if box_width <= 0.75:
        points += 14.0
        reasons.append(f"박스권 압축 {box_width * 100:.0f}%")
    elif box_width <= 1.4:
        points += 7.0
        reasons.append("넓은 박스권 유지")

    long_period = 240 if len(closes) >= 240 else 60 if len(closes) >= 60 else 20
    long_line = _moving_average(closes, long_period)
    if long_line:
        long_gap = current / long_line - 1.0
        if -0.08 <= long_gap <= 0.18:
            points += 18.0
            label = "60월선 대체 장기선" if long_period >= 240 else f"{long_period}봉 장기선"
            reasons.append(f"{label} 근처 지지 {long_gap * 100:.1f}%")
        elif current > long_line and item.low <= long_line * 1.08:
            points += 11.0
            reasons.append("장기선 터치 후 회복")

    volume_ratio = item.volume / prior_avg_volume if prior_avg_volume else 1.0
    close_position = (item.close - item.low) / (item.high - item.low) if item.high > item.low else 0.5
    if volume_ratio >= 1.6 and close_position >= 0.55:
        points += 12.0
        reasons.append(f"하단권 거래량 유입 {volume_ratio:.1f}배")
    elif volume_ratio <= 0.75:
        points += 7.0
        reasons.append("거래량 건조 후 대기권")

    previous_20_high = max((bar.high for bar in bars[-21:-1]), default=0.0)
    if previous_20_high and item.close > previous_20_high:
        points += 10.0
        reasons.append("박스 상단 돌파 시도")

    if item.change_rate >= 18.0:
        points -= 18.0
        warnings.append("당일 급등: PDF 기준 분할매도 주의 구간")
    elif item.change_rate >= 11.0:
        warnings.append("급등 접근: 추격매수 주의")

    if item.trading_value < 2_000_000_000:
        points -= 8.0
        warnings.append("거래대금 부족")

    stage = ""
    if points >= 75:
        stage = "바닥매집 강함"
    elif points >= 60:
        stage = "바닥매집 후보"
    elif points >= 45:
        stage = "관찰권"

    return BottomAccumulationSignal(
        score=round(max(0.0, min(100.0, points)), 1),
        stage=stage,
        support_price=round(support),
        long_line=round(long_line) if long_line else 0.0,
        touch_count=touch_count,
        reasons=reasons[:5],
        warnings=warnings[:3],
    )
