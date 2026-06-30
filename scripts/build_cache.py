#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.spot_data import BinanceSpotData

CONFIG = ROOT / "config" / "watchlist.json"
CACHE_DIR = ROOT / "cache"


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def select_symbols(config: dict, client: BinanceSpotData) -> list[dict]:
    valid = client.tradable_usdt_symbols()
    core = []
    seen: set[str] = set()
    for item in config.get("core_symbols", []):
        symbol = str(item.get("symbol", "")).upper()
        if symbol in valid and symbol not in seen:
            core.append({"symbol": symbol, "name": item.get("name", symbol)})
            seen.add(symbol)

    dynamic = config.get("dynamic_universe", {})
    if not dynamic.get("enabled", True):
        return core

    excluded = {str(s).upper() for s in dynamic.get("exclude_symbols", [])}
    min_volume = float(dynamic.get("min_quote_volume_usdt", 0) or 0)
    top_n = int(dynamic.get("top_n", 0) or 0)
    candidates: list[tuple[str, float]] = []
    for ticker in client.get_24h_tickers():
        symbol = str(ticker.get("symbol", "")).upper()
        base_asset = symbol.removesuffix("USDT")
        if symbol not in valid or symbol in seen or symbol in excluded:
            continue
        if base_asset in {"USDC", "FDUSD", "TUSD", "DAI", "USD1", "RLUSD", "EUR"}:
            continue
        quote_volume = float(ticker.get("quoteVolume", 0) or 0)
        if quote_volume >= min_volume:
            candidates.append((symbol, quote_volume))
    candidates.sort(key=lambda item: item[1], reverse=True)
    for symbol, _ in candidates[:top_n]:
        core.append({"symbol": symbol, "name": symbol.replace("USDT", "")})
        seen.add(symbol)
    return core


def parse_klines(raw: list[list]) -> dict:
    dates: list[str] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    quote_volumes: list[float] = []
    for item in raw:
        dt = datetime.fromtimestamp(int(item[0]) / 1000, tz=timezone.utc)
        dates.append(dt.strftime("%Y-%m-%d"))
        opens.append(float(item[1]))
        highs.append(float(item[2]))
        lows.append(float(item[3]))
        closes.append(float(item[4]))
        volumes.append(float(item[5]))
        quote_volumes.append(float(item[7]))
    return {
        "dates": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "quote_volume": quote_volumes,
    }


def build_cache() -> dict:
    config = load_config()
    client = BinanceSpotData()
    interval = config.get("history", {}).get("interval", "1d")
    limit = int(config.get("history", {}).get("limit", 180))
    symbols = select_symbols(config, client)
    tickers = {str(t.get("symbol", "")).upper(): t for t in client.get_24h_tickers()}
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "binance_spot",
        "benchmark": config.get("benchmark", "BTCUSDT"),
        "quote_asset": config.get("quote_asset", "USDT"),
        "history": {"interval": interval, "limit": limit},
        "market_data": {},
    }
    failures: list[str] = []
    for entry in symbols:
        symbol = entry["symbol"]
        try:
            history = parse_klines(client.get_klines(symbol, interval, limit))
            closes = history["close"]
            if len(closes) < 2:
                raise ValueError("not enough klines")
            prev_close = closes[-2]
            price = closes[-1]
            ticker = tickers.get(symbol, {})
            quote_volume = float(ticker.get("quoteVolume", history["quote_volume"][-1]) or 0)
            out["market_data"][symbol] = {
                "name": entry.get("name", symbol),
                "snapshot": {
                    "price": price,
                    "prev_close": prev_close,
                    "change_pct": round((price - prev_close) / prev_close * 100, 4) if prev_close else 0.0,
                    "quote_volume": quote_volume,
                    "as_of": history["dates"][-1],
                },
                "history": history,
            }
            print(f"  {symbol:<12} {price:>14,.6f} ({out['market_data'][symbol]['snapshot']['change_pct']:+.2f}%)")
        except Exception as exc:
            failures.append(symbol)
            print(f"[WARN] {symbol}: {exc}", file=sys.stderr)
    benchmark = out["benchmark"]
    if benchmark not in out["market_data"]:
        raise RuntimeError(f"benchmark {benchmark} missing from market data")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().strftime("%Y-%m-%d")
    dated = CACHE_DIR / f"{today}_market.json"
    latest = CACHE_DIR / "latest.json"
    payload = json.dumps(out, indent=2, ensure_ascii=False)
    dated.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    print(f"\nWrote {latest.relative_to(ROOT)} ({len(out['market_data'])} symbols)")
    if failures:
        print(f"Failures: {', '.join(failures)}")
    return out


def main() -> int:
    build_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
