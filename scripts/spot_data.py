from __future__ import annotations

import os
import time
from typing import Any

import requests


class BinanceSpotData:
    BASE_URL = "https://api.binance.com"

    def __init__(self, timeout: int = 10, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        proxy = os.environ.get("BINANCE_PROXY", "").strip()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        self._cache: dict[str, tuple[Any, float]] = {}
        self._cache_ttl = 300

    def _get(self, endpoint: str, params: dict | None = None) -> Any:
        url = f"{self.BASE_URL}{endpoint}"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params or {}, timeout=self.timeout)
                if resp.status_code == 429 and attempt < self.max_retries:
                    retry_after = resp.headers.get("Retry-After")
                    wait = int(retry_after) if retry_after and retry_after.isdigit() else 20
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(2 * (attempt + 1))
                    continue
        if last_error:
            raise last_error
        raise RuntimeError(f"Binance request failed: {endpoint}")

    def _get_cached(self, cache_key: str, endpoint: str, params: dict | None = None) -> Any:
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and now - cached[1] < self._cache_ttl:
            return cached[0]
        data = self._get(endpoint, params)
        self._cache[cache_key] = (data, now)
        return data

    def get_exchange_info(self) -> dict:
        return self._get_cached("exchangeInfo", "/api/v3/exchangeInfo")

    def get_24h_tickers(self) -> list[dict]:
        return self._get_cached("ticker24hr", "/api/v3/ticker/24hr")

    def get_klines(self, symbol: str, interval: str, limit: int) -> list[list]:
        return self._get("/api/v3/klines", {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": int(limit),
        })

    def tradable_usdt_symbols(self) -> set[str]:
        info = self.get_exchange_info()
        symbols: set[str] = set()
        for item in info.get("symbols", []):
            if (
                item.get("status") == "TRADING"
                and item.get("quoteAsset") == "USDT"
                and item.get("isSpotTradingAllowed", True)
            ):
                symbols.add(str(item.get("symbol", "")).upper())
        return symbols

