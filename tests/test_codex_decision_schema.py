from scripts.codex_review import validate_decision_payload


def test_validate_hold_decision():
    payload = {
        "as_of": "2026-06-30T09:30:00+08:00",
        "summary": "市场震荡，保持观察",
        "decisions": [{
            "symbol": "BTCUSDT",
            "action": "hold",
            "target_weight_pct": 0,
            "max_order_usdt": 0,
            "stop_loss_price": None,
            "confidence": 0.5,
            "technical_reason": "盘整",
            "fundamental_reason": "无重大变化",
            "news_reason": "无重大新闻",
            "sources": []
        }]
    }
    assert validate_decision_payload(payload)["decisions"][0]["action"] == "hold"


def test_reject_buy_without_stop_loss():
    payload = {
        "as_of": "2026-06-30T09:30:00+08:00",
        "summary": "测试",
        "decisions": [{
            "symbol": "ETHUSDT",
            "action": "build",
            "target_weight_pct": 10,
            "max_order_usdt": 100,
            "stop_loss_price": None,
            "confidence": 0.8,
            "technical_reason": "上升",
            "fundamental_reason": "强",
            "news_reason": "强",
            "sources": ["https://example.com"]
        }]
    }
    try:
        validate_decision_payload(payload)
    except ValueError as exc:
        assert "stop_loss_price" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_reject_buy_without_sources():
    payload = {
        "as_of": "2026-06-30T09:30:00+08:00",
        "summary": "测试",
        "decisions": [{
            "symbol": "ETHUSDT",
            "action": "build",
            "target_weight_pct": 10,
            "max_order_usdt": 100,
            "stop_loss_price": 3000,
            "confidence": 0.8,
            "technical_reason": "上升",
            "fundamental_reason": "强",
            "news_reason": "强",
            "sources": []
        }]
    }
    try:
        validate_decision_payload(payload)
    except ValueError as exc:
        assert "sources" in str(exc)
    else:
        raise AssertionError("expected ValueError")

