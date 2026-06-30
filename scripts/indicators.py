from __future__ import annotations


def ma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    gains = [max(delta, 0) for delta in deltas]
    losses = [max(-delta, 0) for delta in deltas]
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    for i in range(n, len(deltas)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def rel_strength(closes: list[float], benchmark_closes: list[float], n: int = 20) -> float:
    if len(closes) < n + 1 or len(benchmark_closes) < n + 1:
        return 0.0
    symbol_return = (closes[-1] - closes[-n - 1]) / closes[-n - 1] * 100
    benchmark_return = (benchmark_closes[-1] - benchmark_closes[-n - 1]) / benchmark_closes[-n - 1] * 100
    return symbol_return - benchmark_return


def volume_ratio(volumes: list[float], short: int = 5, long: int = 20) -> float:
    if len(volumes) < long:
        return 1.0
    long_avg = sum(volumes[-long:]) / long
    if long_avg == 0:
        return 1.0
    return (sum(volumes[-short:]) / short) / long_avg


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> float | None:
    if len(highs) < n + 1 or len(lows) < n + 1 or len(closes) < n + 1:
        return None
    true_ranges: list[float] = []
    for i in range(1, len(closes)):
        true_ranges.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    return sum(true_ranges[-n:]) / n


def trend_label(price: float, ma20: float | None, ma50: float | None) -> str:
    if ma20 is None or ma50 is None:
        return "未知"
    if price > ma20 > ma50:
        return "上升"
    if price < ma20 < ma50:
        return "下降"
    return "盘整"

