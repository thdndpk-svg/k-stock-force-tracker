from __future__ import annotations

from statistics import mean, pstdev

from models import HistoryBar, ScoreBreakdown


def _round_price(value: float) -> int:
    if value <= 0:
        return 0
    if value >= 500_000:
        unit = 1000
    elif value >= 100_000:
        unit = 500
    elif value >= 10_000:
        unit = 50
    elif value >= 1_000:
        unit = 10
    else:
        unit = 1
    return int(round(value / unit) * unit)


def _sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return 0.0
    return mean(values[-period:])


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * alpha + result[-1] * (1 - alpha))
    return result


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(values[-period - 1 : -1], values[-period:]):
        change = cur - prev
        gains.append(max(0.0, change))
        losses.append(max(0.0, -change))
    avg_gain = mean(gains) if gains else 0.0
    avg_loss = mean(losses) if losses else 0.0
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(bars: list[HistoryBar], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    true_ranges: list[float] = []
    for prev, cur in zip(bars[-period - 1 : -1], bars[-period:]):
        true_ranges.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return mean(true_ranges) if true_ranges else 0.0


def _macd(values: list[float]) -> tuple[float, float, float]:
    if len(values) < 26:
        return 0.0, 0.0, 0.0
    ema12 = _ema_series(values, 12)
    ema26 = _ema_series(values, 26)
    macd_line = [fast - slow for fast, slow in zip(ema12[-len(ema26) :], ema26)]
    signal = _ema_series(macd_line, 9)
    macd_value = macd_line[-1]
    signal_value = signal[-1] if signal else 0.0
    return macd_value, signal_value, macd_value - signal_value


def _vwap(bars: list[HistoryBar], period: int = 20) -> float:
    recent = bars[-period:]
    total_volume = sum(max(0.0, bar.volume) for bar in recent)
    if not total_volume:
        return 0.0
    weighted = sum(((bar.high + bar.low + bar.close) / 3) * max(0.0, bar.volume) for bar in recent)
    return weighted / total_volume


def _bollinger(values: list[float], period: int = 20) -> tuple[float, float, float, float]:
    if len(values) < period:
        middle = mean(values) if values else 0.0
        return middle, middle, middle, 0.0
    recent = values[-period:]
    middle = mean(recent)
    dev = pstdev(recent)
    upper = middle + dev * 2
    lower = middle - dev * 2
    width = (upper - lower) / middle if middle else 0.0
    return upper, middle, lower, width


def build_professional_view(item: ScoreBreakdown, bars: list[HistoryBar]) -> dict[str, object]:
    warnings: list[str] = []
    scale = 1.0
    if bars and bars[-1].close > 0 and item.close > 0:
        ratio = item.close / bars[-1].close
        if ratio >= 2.5 or ratio <= 0.4:
            scale = ratio
            warnings.append("현재가와 과거 차트 단위 차이가 커서 지표 단위를 자동 보정")
            bars = [
                HistoryBar(
                    code=bar.code,
                    trade_date=bar.trade_date,
                    open=bar.open * scale,
                    high=bar.high * scale,
                    low=bar.low * scale,
                    close=bar.close * scale,
                    volume=bar.volume,
                    trading_value=bar.trading_value * scale,
                )
                for bar in bars
            ]

    closes = [bar.close for bar in bars if bar.close > 0]
    current = item.close
    if not closes or closes[-1] != current:
        closes.append(current)

    ma5 = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    rsi14 = _rsi(closes)
    macd, macd_signal, macd_hist = _macd(closes)
    bb_upper, bb_mid, bb_lower, bb_width = _bollinger(closes)
    vwap20 = _vwap(bars)
    atr14 = _atr(bars)
    recent_high = max((bar.high for bar in bars[-20:]), default=current)
    recent_low = min((bar.low for bar in bars[-20:]), default=current)
    bottom_support = item.bottom_support * scale if item.bottom_support else 0.0
    support_candidates = [value for value in [bottom_support, recent_low, ma20, bb_lower] if value and value > 0]
    resistance_candidates = [value for value in [recent_high, bb_upper] if value and value > 0]
    near_supports = [value for value in support_candidates if current * 0.75 <= value <= current]
    support = max(near_supports) if near_supports else max(current - atr14 * 1.5, current * 0.93) if atr14 else current * 0.93
    above_resistances = [value for value in resistance_candidates if value >= current]
    resistance = (
        min(above_resistances)
        if above_resistances
        else max(max(resistance_candidates, default=0.0), current + atr14 * 2 if atr14 else current * 1.04, current * 1.04)
    )

    entry = current
    stop_candidates = [support * 0.985, current * 0.93]
    if atr14:
        stop_candidates.append(current - atr14 * 1.8)
    stop = max(value for value in stop_candidates if 0 < value < current)
    target1 = max(resistance, current + (current - stop) * 1.5)
    target2 = max(current + (current - stop) * 2.5, bb_upper or resistance)
    risk = max(1.0, current - stop)
    reward = max(0.0, target1 - current)
    risk_reward = reward / risk if risk else 0.0

    trend_score = 0
    if ma5 and current >= ma5:
        trend_score += 1
    if ma20 and current >= ma20:
        trend_score += 1
    if ma60 and current >= ma60:
        trend_score += 1
    if ma5 and ma20 and ma5 >= ma20:
        trend_score += 1
    if ma20 and ma60 and ma20 >= ma60:
        trend_score += 1

    if item.trade_action == "매도" or rsi14 >= 78:
        bias = "리스크관리"
        setup = "과열/분할매도 감시"
    elif trend_score >= 4 and macd_hist > 0 and item.flow_ratio >= 0.05:
        bias = "공격매수 후보"
        setup = "추세추종 돌파"
    elif item.bottom_score >= 60 and current <= max(ma20 or current, bottom_support or current) * 1.12:
        bias = "분할매수 후보"
        setup = "바닥매집 눌림목"
    elif risk_reward >= 1.5 and rsi14 < 70:
        bias = "관심/대기"
        setup = "손익비 확인 구간"
    else:
        bias = "보류"
        setup = "확인 신호 부족"

    checklist: list[str] = []
    if trend_score >= 4:
        checklist.append("단기/중기/장기 이동평균 정배열 또는 회복")
    elif trend_score <= 2:
        warnings.append("추세 정렬 약함")
    if 45 <= rsi14 <= 68:
        checklist.append("RSI 과열 전 상승 여력 구간")
    elif rsi14 >= 75:
        warnings.append("RSI 과열권")
    elif rsi14 <= 35:
        warnings.append("RSI 약세권")
    if macd_hist > 0:
        checklist.append("MACD 모멘텀 양전환/유지")
    else:
        warnings.append("MACD 모멘텀 미확인")
    if item.flow_ratio >= 0.05:
        checklist.append("외국인/기관 수급 집중 확인")
    else:
        warnings.append("수급 집중 약함")
    if current >= vwap20 > 0:
        checklist.append("20봉 VWAP 상단 유지")
    elif vwap20:
        warnings.append("VWAP 아래라 매물 소화 필요")
    if risk_reward >= 1.5:
        checklist.append(f"1차 목표 손익비 {risk_reward:.1f}배")
    else:
        warnings.append("1차 목표 손익비 부족")

    return {
        "bias": bias,
        "setup": setup,
        "entry": _round_price(entry),
        "support": _round_price(support),
        "resistance": _round_price(resistance),
        "stop": _round_price(stop),
        "target1": _round_price(target1),
        "target2": _round_price(target2),
        "riskReward": round(risk_reward, 2),
        "positionGuide": "분할 접근: 1차 40%, 지지 확인 30%, 돌파 확인 30%",
        "indicators": {
            "ma5": round(ma5, 1),
            "ma20": round(ma20, 1),
            "ma60": round(ma60, 1),
            "rsi14": round(rsi14, 1),
            "macd": round(macd, 1),
            "macdSignal": round(macd_signal, 1),
            "macdHist": round(macd_hist, 1),
            "bbUpper": round(bb_upper, 1),
            "bbMiddle": round(bb_mid, 1),
            "bbLower": round(bb_lower, 1),
            "bbWidth": round(bb_width * 100, 1),
            "vwap20": round(vwap20, 1),
            "atr14": round(atr14, 1),
            "trendScore": trend_score,
        },
        "checklist": checklist[:6],
        "warnings": warnings[:6],
    }
