import pytest

from scripts import build_cache as build_cache_module


class FailingClient:
    def tradable_usdt_symbols(self):
        return {"BTCUSDT"}

    def get_24h_tickers(self):
        return []

    def get_klines(self, symbol, interval, limit):
        raise RuntimeError("451 Client Error: restricted location")


def test_benchmark_failure_includes_fetch_error(monkeypatch):
    monkeypatch.setattr(
        build_cache_module,
        "load_config",
        lambda: {
            "benchmark": "BTCUSDT",
            "core_symbols": [{"symbol": "BTCUSDT", "name": "Bitcoin"}],
            "dynamic_universe": {"enabled": False},
            "history": {"interval": "1d", "limit": 180},
        },
    )
    monkeypatch.setattr(build_cache_module, "BinanceSpotData", FailingClient)

    with pytest.raises(RuntimeError) as exc:
        build_cache_module.build_cache()

    message = str(exc.value)
    assert "benchmark BTCUSDT missing from market data" in message
    assert "451 Client Error" in message
