#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "latest.json"
PREFILTER = ROOT / "reports" / "latest_prefilter.json"
REPORTS_DIR = ROOT / "reports"

VALID_ACTIONS = {"build", "add", "reduce", "close", "stop_loss", "hold"}
VALID_SIDES = {"long", "short"}


def _as_float(value: Any, key: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric") from exc


def validate_decision_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if not payload.get("as_of"):
        raise ValueError("as_of is required")
    if not isinstance(payload.get("summary"), str):
        raise ValueError("summary is required")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("decisions must be a list")
    normalized = dict(payload)
    normalized_decisions = []
    for index, item in enumerate(decisions):
        if not isinstance(item, dict):
            raise ValueError(f"decisions[{index}] must be an object")
        symbol = str(item.get("symbol", "")).strip().upper()
        if not symbol.endswith("USDT") or symbol != item.get("symbol"):
            raise ValueError(f"decisions[{index}].symbol must be uppercase *USDT")
        action = str(item.get("action", "")).strip()
        if action not in VALID_ACTIONS:
            raise ValueError(f"decisions[{index}].action is invalid")
        confidence = _as_float(item.get("confidence"), f"decisions[{index}].confidence")
        if not 0 <= confidence <= 1:
            raise ValueError(f"decisions[{index}].confidence must be between 0 and 1")
        target_weight_pct = _as_float(item.get("target_weight_pct", 0), f"decisions[{index}].target_weight_pct")
        max_order_usdt = _as_float(item.get("max_order_usdt", 0), f"decisions[{index}].max_order_usdt")
        stop_loss_price = item.get("stop_loss_price")
        raw_side = item.get("side", item.get("direction"))
        side = str(raw_side).strip().lower() if raw_side is not None else "long"
        if side not in VALID_SIDES:
            raise ValueError(f"decisions[{index}].side must be long or short")
        sources = item.get("sources", [])
        if not isinstance(sources, list) or any(not isinstance(source, str) for source in sources):
            raise ValueError(f"decisions[{index}].sources must be a string list")
        if action in {"build", "add"}:
            if target_weight_pct <= 0:
                raise ValueError(f"decisions[{index}].target_weight_pct must be positive")
            if max_order_usdt <= 0:
                raise ValueError(f"decisions[{index}].max_order_usdt must be positive")
            if stop_loss_price is None or _as_float(stop_loss_price, f"decisions[{index}].stop_loss_price") <= 0:
                raise ValueError(f"decisions[{index}].stop_loss_price is required for {action}")
            if not sources:
                raise ValueError(f"decisions[{index}].sources is required for {action}")
        normalized_item = {
            "symbol": symbol,
            "action": action,
            "target_weight_pct": target_weight_pct,
            "max_order_usdt": max_order_usdt,
            "stop_loss_price": None if stop_loss_price is None else _as_float(stop_loss_price, f"decisions[{index}].stop_loss_price"),
            "side": side,
            "confidence": confidence,
            "technical_reason": str(item.get("technical_reason", "")),
            "fundamental_reason": str(item.get("fundamental_reason", "")),
            "news_reason": str(item.get("news_reason", "")),
            "sources": sources,
        }
        normalized_decisions.append(normalized_item)
    normalized["decisions"] = normalized_decisions
    return normalized


def extract_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found in Codex output")
    return json.loads(match.group(0))


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_prompt(
    cache: dict,
    prefilter: dict,
    positions: dict,
    max_symbol_weight_pct: float,
    max_gross_weight_pct: float,
    *,
    market: str = "spot",
) -> str:
    if market == "futures":
        market_text = (
            "你是 Binance USDT-M 合约自动复盘策略。请联网搜索必要新闻，并严格只输出一个 JSON 对象，不要 Markdown。\n"
            "硬规则：只允许 USDT-M 永续合约；允许 long/short，但每条 build/add 决策必须输出 side=long 或 side=short；"
            "新闻不能单独触发建仓/加仓；build/add 必须有 stop_loss_price 和 sources；"
            "stop_loss_price 对 long 必须低于现价，对 short 必须高于现价；如果不确定就 hold。\n"
            "target_weight_pct 表示目标名义敞口占账户权益百分比，max_order_usdt 表示本次最大名义下单金额。\n\n"
        )
    else:
        market_text = (
            "你是 Binance 现货自动复盘策略。请联网搜索必要新闻，并严格只输出一个 JSON 对象，不要 Markdown。\n"
            "硬规则：只允许现货；新闻不能单独触发建仓/加仓；build/add 必须有 stop_loss_price 和 sources；"
            "如果不确定就 hold。\n\n"
        )
    return (
        market_text +
        f"max_symbol_weight_pct={max_symbol_weight_pct}\n"
        f"max_gross_weight_pct={max_gross_weight_pct}\n\n"
        "CLAUDE.md 规则已在仓库根目录。\n\n"
        "行情 cache:\n"
        f"{json.dumps(cache, ensure_ascii=False)[:60000]}\n\n"
        "技术预筛:\n"
        f"{json.dumps(prefilter, ensure_ascii=False)[:30000]}\n\n"
        "当前持仓:\n"
        f"{json.dumps(positions, ensure_ascii=False)[:20000]}\n"
    )


def run_codex(prompt: str, out: Path) -> dict:
    codex_bin = os.environ.get("CODEX_BIN", "codex")
    cmd = [codex_bin, "--search", "exec", "-C", str(ROOT), prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raw_path = out.with_name(out.stem + "_raw.txt")
        raw_path.write_text(result.stdout + "\n--- STDERR ---\n" + result.stderr, encoding="utf-8")
        raise RuntimeError(f"codex failed with exit {result.returncode}; raw saved to {raw_path}")
    try:
        return extract_json_object(result.stdout)
    except Exception:
        raw_path = out.with_name(out.stem + "_raw.txt")
        raw_path.write_text(result.stdout, encoding="utf-8")
        raise


def default_positions() -> dict:
    path = ROOT / "positions.local.json"
    if path.exists():
        return load_json(path)
    return {"equity": 0, "cash_usdt": 0, "positions": {}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=str(CACHE))
    parser.add_argument("--prefilter", default=str(PREFILTER))
    parser.add_argument("--positions", default="")
    parser.add_argument("--out", default=str(REPORTS_DIR / "latest_decision.json"))
    parser.add_argument("--max-symbol-weight-pct", type=float, default=20)
    parser.add_argument("--max-gross-weight-pct", type=float, default=80)
    parser.add_argument("--market", choices=["spot", "futures"], default="spot")
    parser.add_argument("--mock-hold", action="store_true", help="Generate deterministic hold decisions without calling Codex")
    args = parser.parse_args()

    cache = load_json(Path(args.cache))
    prefilter = load_json(Path(args.prefilter))
    positions = load_json(Path(args.positions)) if args.positions else default_positions()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.mock_hold:
        payload = {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "summary": "mock hold decision",
            "decisions": [
                {
                    "symbol": row["symbol"],
                    "action": "hold",
                    "target_weight_pct": 0,
                    "max_order_usdt": 0,
                    "stop_loss_price": None,
                    "side": "long",
                    "confidence": 0.5,
                    "technical_reason": row.get("technical_action_hint", "hold"),
                    "fundamental_reason": "mock",
                    "news_reason": "mock",
                    "sources": [],
                }
                for row in prefilter.get("rows", [])
            ],
        }
    else:
        payload = run_codex(
            build_prompt(
                cache,
                prefilter,
                positions,
                args.max_symbol_weight_pct,
                args.max_gross_weight_pct,
                market=args.market,
            ),
            out,
        )
    validated = validate_decision_payload(payload)
    out.write_text(json.dumps(validated, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        display_path = out.relative_to(ROOT)
    except ValueError:
        display_path = out
    print(f"Wrote {display_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
