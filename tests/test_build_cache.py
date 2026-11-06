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


class RecordingClient:
    def __init__(self):
        self.requested = []

    def tradable_usdt_symbols(self):
        return {"BTCUSDT", "ONGUSDT", "PEPEUSDT"}

    def get_24h_tickers(self):
        return []

    def get_klines(self, symbol, interval, limit):
        self.requested.append(symbol)
        return [
            [0, "1", "1", "1", "1", "1", 0, "1"],
            [86_400_000, "1", "1", "1", "2", "1", 0, "1"],
        ]


def test_rebuild_keeps_holdings_from_both_accounts(monkeypatch, tmp_path):
    holdings = tmp_path / "holdings"
    holdings.mkdir()
    (holdings / "spot.json").write_text('{"symbols": ["PEPEUSDT", "1000PEPEUSDT"]}', encoding="utf-8")
    monkeypatch.setattr(build_cache_module, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        build_cache_module,
        "load_config",
        lambda: {
            "benchmark": "BTCUSDT",
            "core_symbols": [{"symbol": "BTCUSDT", "name": "Bitcoin"}],
            "dynamic_universe": {"enabled": False},
            "history": {"interval": "1d", "limit": 2},
        },
    )
    client = RecordingClient()
    monkeypatch.setattr(build_cache_module, "BinanceSpotData", lambda: client)

    result = build_cache_module.build_cache({"positions": {"ONGUSDT": {"value_usdt": 49}}})

    assert client.requested == ["BTCUSDT", "ONGUSDT", "PEPEUSDT"]
    assert set(result["market_data"]) == {"BTCUSDT", "ONGUSDT", "PEPEUSDT"}
    assert result["held_without_spot_market"] == ["1000PEPEUSDT"]
