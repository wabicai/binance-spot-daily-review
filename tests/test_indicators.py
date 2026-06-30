from scripts.indicators import ma, rsi, rel_strength, trend_label


def test_ma_uses_tail_window():
    assert ma([1, 2, 3, 4, 5], 3) == 4


def test_rsi_returns_100_when_no_losses():
    assert rsi(list(range(1, 20)), 14) == 100.0


def test_rel_strength_subtracts_benchmark_return():
    assert round(rel_strength([100, 110], [100, 105], 1), 4) == 5.0


def test_trend_label():
    assert trend_label(120, 110, 100) == "上升"
    assert trend_label(80, 90, 100) == "下降"
    assert trend_label(100, 100, 100) == "盘整"

