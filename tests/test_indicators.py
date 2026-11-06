from scripts.analyze import action_hint, enforce_pullback_stops, tighten_entry_stops
from scripts.indicators import ma, persistent_pullback, rsi, rel_strength, trend_label


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


def _rising_series(last_prices: list[float]) -> tuple[list[float], list[float]]:
    base = [100 + i for i in range(25)]
    closes = base[:-len(last_prices)] + last_prices
    highs = [price + 1 for price in closes]
    return closes, highs


def test_one_day_dip_is_not_a_pullback():
    closes, highs = _rising_series([130, 140, 150, 160, 170, 120])
    assert persistent_pullback(closes, highs) is False


def test_two_stale_days_far_below_the_high_are_a_pullback():
    # 高点在倒数第 5 根，最近两根收盘都离开它 12% 以上，且高点年龄都够 3 天。
    closes = [100.0] * 20 + [150, 140, 130, 112, 110]
    highs = [price + 1 for price in closes]
    highs[-5] = 160
    assert persistent_pullback(closes, highs) is True


def test_pullback_hint_is_not_a_build():
    assert action_hint(110, 100, 90, 60, 5, "回撤") == "risk_reduce_candidate"
    assert action_hint(120, 110, 100, 60, 5, "上升") == "build_candidate"


def test_pullback_long_gets_a_stop_and_short_does_not():
    payload = {"as_of": "2026-11-06T00:00:00Z", "summary": "无明确动作", "decisions": [
        {"symbol": "PEPEUSDT", "action": "build", "target_weight_pct": 10, "max_order_usdt": 20},
    ]}
    prefilter = {"rows": [
        {"symbol": "PEPEUSDT", "trend": "回撤"},
        {"symbol": "ONGUSDT", "trend": "回撤"},
    ]}
    account = {"positions": {
        "PEPEUSDT": {"side": "long", "value_usdt": 450},
        "ONGUSDT": {"side": "short", "value_usdt": 49},
        "DUSTUSDT": {"side": "long", "value_usdt": 2},
    }}
    result = enforce_pullback_stops(payload, prefilter, account)
    actions = {(item["symbol"], item["action"]) for item in result["decisions"]}
    assert ("PEPEUSDT", "build") not in actions
    assert ("PEPEUSDT", "stop_loss") in actions
    assert ("ONGUSDT", "stop_loss") not in actions
    assert "规则止损" in result["summary"]


def test_wide_entry_stop_is_pulled_in_to_two_atr():
    prefilter = {"rows": [
        {"symbol": "PEPEUSDT", "price": 10, "atr14": 1, "trend": "上升"},
        {"symbol": "ONGUSDT", "price": 10, "atr14": 1, "trend": "下降"},
        {"symbol": "BTCUSDT", "price": 10, "atr14": 1, "trend": "上升"},
    ]}
    payload = {"as_of": "2026-11-06T00:00:00Z", "summary": "开仓", "decisions": [
        {"symbol": "PEPEUSDT", "action": "build", "stop_loss_price": 1, "side": "long", "technical_reason": "模型止损"},
        {"symbol": "ONGUSDT", "action": "add", "stop_loss_price": 20, "side": "short", "technical_reason": "模型止损"},
        {"symbol": "BTCUSDT", "action": "build", "stop_loss_price": 9.5, "side": "long"},
    ]}
    result = tighten_entry_stops(payload, prefilter, {"prices": {"BTCUSDT": 10}})
    stops = {item["symbol"]: item["stop_loss_price"] for item in result["decisions"]}
    assert stops["PEPEUSDT"] == 8
    assert stops["ONGUSDT"] == 12
    assert stops["BTCUSDT"] == 9.5
    assert "2倍ATR" in result["decisions"][0]["technical_reason"]

