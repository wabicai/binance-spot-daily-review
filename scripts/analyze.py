#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.indicators import (
    atr,
    drawdown_from_high_pct,
    ma,
    persistent_pullback,
    rel_strength,
    rsi,
    trend_label,
    volume_ratio,
)

CACHE_DIR = ROOT / "cache"
REPORTS_DIR = ROOT / "reports"


def load_cache(path: str | None = None) -> dict:
    cache_path = Path(path) if path else CACHE_DIR / "latest.json"
    return json.loads(cache_path.read_text(encoding="utf-8"))


def action_hint(
    price: float,
    ma20: float | None,
    ma50: float | None,
    rsi14: float | None,
    rs: float,
    trend: str,
) -> str:
    if trend == "回撤" or (ma20 is not None and price < ma20):
        return "risk_reduce_candidate"
    if rsi14 is not None and rsi14 < 35:
        return "risk_reduce_candidate"
    if trend == "上升" and ma20 is not None and ma50 is not None and rsi14 is not None:
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
        series = list(closes)
        if series:
            series[-1] = price
        trend = trend_label(price, ma20, ma50)
        if trend == "上升" and persistent_pullback(series, list(highs)):
            trend = "回撤"
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
            "drawdown_from_high_pct": drawdown_from_high_pct(price, list(highs)),
            "trend": trend,
            "technical_action_hint": action_hint(price, ma20, ma50, rsi14, rs, trend),
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cache_generated_at": cache.get("generated_at"),
        "benchmark": benchmark,
        "rows": rows,
    }


_EXIT_ACTIONS = {"reduce", "close", "stop_loss"}
_ENTRY_ACTIONS = {"build", "add"}
# 日线 ATR 已经包含普通波动，2 倍只挡住跳空，慢回撤仍交给回撤规则。
ENTRY_STOP_ATR_MULT = 2.0


def _row_map(prefilter: dict) -> dict:
    return {
        row.get("symbol"): row
        for row in (prefilter or {}).get("rows", [])
        if isinstance(row, dict)
    }


def _execution_price(symbol: str, row: dict, account: dict | None) -> float:
    prices = account.get("prices") if isinstance(account, dict) else None
    positions = account.get("positions") if isinstance(account, dict) else None
    raw = None
    if isinstance(prices, dict):
        raw = prices.get(symbol)
    if raw in (None, 0, 0.0) and isinstance(positions, dict):
        held = positions.get(symbol)
        if isinstance(held, dict):
            raw = held.get("price")
    if raw in (None, 0, 0.0):
        raw = row.get("price")
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def tighten_entry_stops(payload: dict, prefilter: dict, account: dict | None = None) -> dict:
    """开仓止损离现价不得超过 2 倍 ATR。模型写得更近时保留。"""
    rows = _row_map(prefilter)
    decisions = []
    for item in list(payload.get("decisions") or []):
        if not isinstance(item, dict) or item.get("action") not in _ENTRY_ACTIONS:
            decisions.append(item)
            continue
        symbol = item.get("symbol")
        row = rows.get(symbol) or {}
        try:
            atr = float(row.get("atr14") or 0)
            stop = float(item.get("stop_loss_price"))
        except (TypeError, ValueError):
            decisions.append(item)
            continue
        price = _execution_price(symbol, row, account)
        if price <= 0 or atr <= 0 or stop <= 0:
            decisions.append(item)
            continue
        side = str(item.get("side") or "long").lower()
        distance = ENTRY_STOP_ATR_MULT * atr
        if side == "short":
            cap = price + distance
            tightened = stop > cap
        else:
            cap = price - distance
            tightened = cap > 0 and stop < cap
        if not tightened:
            decisions.append(item)
            continue
        updated = dict(item)
        updated["stop_loss_price"] = cap
        reason = str(item.get("technical_reason") or "").strip()
        note = "止损已收到2倍ATR。"
        updated["technical_reason"] = f"{reason} {note}".strip() if reason else note
        decisions.append(updated)
    if decisions == list(payload.get("decisions") or []):
        return payload
    updated_payload = dict(payload)
    updated_payload["decisions"] = decisions
    return updated_payload


def enforce_pullback_stops(payload: dict, prefilter: dict, account: dict) -> dict:
    """已持有的多单处在回撤时，补上止损。模型漏写时也不能继续当作上升持有。"""
    rows = _row_map(prefilter)
    held = account.get("positions") if isinstance(account, dict) else None
    if not isinstance(held, dict):
        held = {}
    min_order = 10.0
    context = account.get("review_context") if isinstance(account, dict) else None
    if isinstance(context, dict):
        raw_min = (context.get("execution_constraints") or {}).get("min_order_usdt")
        try:
            if raw_min is not None:
                min_order = float(raw_min)
        except (TypeError, ValueError):
            pass

    decisions = [
        item for item in list(payload.get("decisions") or [])
        if not (
            isinstance(item, dict)
            and item.get("action") in _ENTRY_ACTIONS
            and (rows.get(item.get("symbol")) or {}).get("trend") == "回撤"
        )
    ]
    acted = {
        item.get("symbol")
        for item in decisions
        if isinstance(item, dict) and item.get("action") in _EXIT_ACTIONS
    }
    added = []
    for symbol, pos in held.items():
        if not isinstance(pos, dict) or symbol in acted:
            continue
        if (rows.get(symbol) or {}).get("trend") != "回撤":
            continue
        if str(pos.get("side") or "long").lower() == "short":
            continue
        try:
            value = float(pos.get("value_usdt") or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value < min_order:
            continue
        added.append({
            "symbol": symbol,
            "action": "stop_loss",
            "target_weight_pct": 0,
            "max_order_usdt": 0,
            "stop_loss_price": None,
            "side": "long",
            "confidence": 0.9,
            "technical_reason": "连续两日远离20日高点，均线多头已是回撤，规则止损。",
            "fundamental_reason": "不沿用上升标签继续持有。",
            "news_reason": "",
            "sources": ["technical_prescreen"],
        })
    updated = dict(payload)
    updated["decisions"] = decisions + added
    if added:
        summary = str(payload.get("summary") or "").strip()
        note = "回撤持仓已按规则止损"
        updated["summary"] = f"{summary}；{note}" if summary else note
    return updated


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

