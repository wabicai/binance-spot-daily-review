#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.indicators import atr, ma, rel_strength, rsi, trend_label, volume_ratio

CACHE_DIR = ROOT / "cache"
REPORTS_DIR = ROOT / "reports"


def load_cache(path: str | None = None) -> dict:
    cache_path = Path(path) if path else CACHE_DIR / "latest.json"
    return json.loads(cache_path.read_text(encoding="utf-8"))


def action_hint(price: float, ma20: float | None, ma50: float | None, rsi14: float | None, rs: float) -> str:
    if ma20 is not None and price < ma20:
        return "risk_reduce_candidate"
    if rsi14 is not None and rsi14 < 35:
        return "risk_reduce_candidate"
    if ma20 is not None and ma50 is not None and rsi14 is not None:
        if price > ma20 > ma50 and 50 <= rsi14 <= 70 and rs >= 0:
            return "build_candidate"
    return "hold"


def analyze(cache: dict) -> dict:
    market = cache["market_data"]
    benchmark = cache["benchmark"]
    benchmark_closes = market[benchmark]["history"]["close"]
    rows = []
    for symbol, item in market.items():
        hist = item["history"]
        closes = hist["close"]
        highs = hist["high"]
        lows = hist["low"]
        volumes = hist["volume"]
        price = float(item["snapshot"]["price"])
        ma20 = ma(closes, 20)
        ma50 = ma(closes, 50)
        rsi14 = rsi(closes, 14)
        rs = rel_strength(closes, benchmark_closes, 20)
        vr = volume_ratio(volumes)
        atr14 = atr(highs, lows, closes, 14)
        trend = trend_label(price, ma20, ma50)
        rows.append({
            "symbol": symbol,
            "name": item.get("name", symbol),
            "price": price,
            "ma20": ma20,
            "ma50": ma50,
            "rsi14": rsi14,
            "relative_strength": rs,
            "volume_ratio": vr,
            "atr14": atr14,
            "trend": trend,
            "technical_action_hint": action_hint(price, ma20, ma50, rsi14, rs),
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cache_generated_at": cache.get("generated_at"),
        "benchmark": benchmark,
        "rows": rows,
    }


def print_report(result: dict) -> None:
    print("\n" + "=" * 118)
    print(f"  Binance Spot Watchlist 技术预筛  benchmark={result['benchmark']}")
    print("=" * 118)
    print(f"  {'symbol':<12} {'price':>14} {'MA20':>14} {'MA50':>14} {'RSI':>8} {'RS':>9} {'VR':>7} {'trend':>6}  hint")
    print("-" * 118)
    for row in result["rows"]:
        ma20 = f"{row['ma20']:.6f}" if row["ma20"] is not None else "-"
        ma50 = f"{row['ma50']:.6f}" if row["ma50"] is not None else "-"
        rsi14 = f"{row['rsi14']:.1f}" if row["rsi14"] is not None else "-"
        print(
            f"  {row['symbol']:<12} {row['price']:>14.6f} {ma20:>14} {ma50:>14} "
            f"{rsi14:>8} {row['relative_strength']:>+8.2f}% {row['volume_ratio']:>6.2f}x "
            f"{row['trend']:>6}  {row['technical_action_hint']}"
        )
    print()


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else None
    result = analyze(load_cache(target))
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "latest_prefilter.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

