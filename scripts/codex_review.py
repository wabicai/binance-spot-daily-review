#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "latest.json"
PREFILTER = ROOT / "reports" / "latest_prefilter.json"
REPORTS_DIR = ROOT / "reports"
OUTPUT_SCHEMA = ROOT / "config" / "codex_decision_schema.json"

VALID_ACTIONS = {"build", "add", "reduce", "close", "stop_loss", "hold"}
VALID_SIDES = {"long", "short"}
CODEX_TIMEOUT_SECONDS = 120
CODEX_RETRY_DELAYS_SECONDS = (30, 60)
TRANSIENT_PROVIDER_ERRORS = (
    "503 service unavailable",
    "server_is_overloaded",
    "auth_unavailable",
)


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


def compact_cache(cache: dict) -> dict:
    """仅保留决策需要的最新行情，避免把完整历史 K 线重复送入模型。"""
    market_data = {}
    for symbol, item in cache.get("market_data", {}).items():
        if not isinstance(item, dict):
            continue
        market_data[symbol] = {
            "name": item.get("name"),
            "snapshot": item.get("snapshot", {}),
        }
    return {
        "generated_at": cache.get("generated_at"),
        "source": cache.get("source"),
        "benchmark": cache.get("benchmark"),
        "quote_asset": cache.get("quote_asset"),
        "market_data": market_data,
    }


def _json_for_prompt(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_prompt(
    cache: dict,
    prefilter: dict,
    positions: dict,
    max_symbol_weight_pct: float,
    max_gross_weight_pct: float,
    *,
    market: str = "spot",
) -> str:
    shared_rules = (
        "共享硬规则：未持仓标的只允许 build；已持仓同方向标的才允许 add、reduce、close、stop_loss；"
        "不得用 build 代替 add，也不得在同一轮直接反向开仓。"
        "新闻只能增强或否决技术信号，不能单独触发 build/add。"
        "数据过期、来源不足、价格异常、信心不足或目标不满足执行约束时必须 hold；hold 不写入 decisions。"
        "每个 build/add 必须有有效 stop_loss_price 和 sources。"
        "target_weight_pct 不得超过 max_symbol_weight_pct，所有目标名义敞口合计不得超过 max_gross_weight_pct。"
        "若账户状态给出 recent_filled_actions，同一 symbol 最近 24 小时发生过 build/add/reduce/close/stop_loss 时默认 hold；"
        "只有止损触发或日线趋势明确失效且 confidence>=0.8 时才允许反向调整，并在 technical_reason 说明失效证据。回撤规则：趋势为回撤时禁止 build/add。已持有的多单必须输出 stop_loss，不能因为价格仍高于均线就继续当作上升持有。空单不要因为这个规则被回补。\n"
        "build/add 的 stop_loss_price 离现价不得超过 2 倍 ATR，更近的止损保留。\n"
    )
    if market == "futures":
        market_text = (
            "你是 Binance USDT-M 合约自动复盘策略。\n"
            "硬规则：只允许 USDT-M 永续合约；允许 long/short，但每条 build/add 决策必须输出 side=long 或 side=short；"
            "stop_loss_price 对 long 必须低于现价，对 short 必须高于现价；如果不确定就 hold。\n"
            "target_weight_pct 表示目标名义敞口占账户权益百分比，不是保证金占比；"
            "max_order_usdt 表示本次最大名义下单金额。"
            "必须遵守 review_context.execution_constraints 与 symbol_order_constraints；"
            "max_order_usdt 低于该标的 effective_min_order_usdt、超过单笔上限或无法满足数量步长时必须 hold。\n"
        )
    else:
        market_text = (
            "你是 Binance 现货自动复盘策略。\n"
            "硬规则：只允许现货，不允许合约、杠杆、借贷和做空；如果不确定就 hold。\n"
        )
    return (
        market_text +
        shared_rules +
        "执行边界：最多进行一轮内置联网搜索并获取不超过 12 条标题；禁止执行 shell、curl 或读取仓库文件。"
        "联网不可用时立即基于技术预筛给出保守决策，不要重试其他新闻源。\n"
        "输出边界：内部判断不超过 150 个汉字；不要输出进度、分析过程或 Markdown，直接输出单个 JSON 对象。"
        "decisions 只列出 build/add/reduce/close/stop_loss，hold 不逐币列出；没有明确动作时必须为 []。"
        "每个原因不超过 80 个汉字，每个决策最多 2 个来源。\n"
        "JSON 顶层字段必须且只能是 as_of、summary、decisions。每个 decisions 项必须且只能包含："
        "symbol、action、target_weight_pct、max_order_usdt、stop_loss_price、side、confidence、"
        "technical_reason、fundamental_reason、news_reason、sources。禁止猜测或增加其他字段。\n"
        '空操作示例：{"as_of":"2026-07-27T00:00:00Z","summary":"无明确动作，保持不变","decisions":[]}\n\n'
        f"max_symbol_weight_pct={max_symbol_weight_pct}\n"
        f"max_gross_weight_pct={max_gross_weight_pct}\n\n"
        "最新行情快照（历史指标已汇总在技术预筛中）:\n"
        f"{_json_for_prompt(compact_cache(cache))}\n\n"
        "技术预筛:\n"
        f"{_json_for_prompt(prefilter)}\n\n"
        "账户状态、当前持仓与执行上下文:\n"
        f"{_json_for_prompt(positions)}\n"
    )


def _attempt_raw_path(out: Path, attempt_name: str) -> Path:
    return out.with_name(f"{out.stem}_raw_{attempt_name}.txt")


def _write_attempt_diagnostic(
    path: Path,
    *,
    last_message: str,
    stdout: str,
    stderr: str,
) -> None:
    sections = [last_message.rstrip()]
    if stdout.strip() and stdout.strip() != last_message.strip():
        sections.extend(["--- STDOUT ---", stdout.rstrip()])
    if stderr.strip():
        sections.extend(["--- STDERR ---", stderr.rstrip()])
    path.write_text("\n".join(section for section in sections if section) + "\n", encoding="utf-8")


def deterministic_hold(reason: str) -> dict:
    """外部模型不可用或输出不合规时，返回可验证的安全空操作。"""
    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": f"Codex 复盘不可用，本轮按安全规则保持全部仓位不变（{reason}）",
        "decisions": [],
    }


def _is_transient_provider_error(stderr: str) -> bool:
    normalized = stderr.lower()
    return any(marker in normalized for marker in TRANSIENT_PROVIDER_ERRORS)


def run_codex(prompt: str, out: Path) -> dict:
    codex_bin = os.environ.get("CODEX_BIN", "codex")
    codex_home = os.environ.get("CODEX_REVIEW_HOME", str(Path.home() / ".codex-review"))
    codex_env = os.environ.copy()
    codex_env["CODEX_HOME"] = codex_home
    attempts = [
        ("online", True, prompt, CODEX_TIMEOUT_SECONDS),
    ]
    failures = []

    last_raw_path = None
    for attempt_name, enable_search, attempt_prompt, timeout in attempts:
        attempt_number = 0
        while True:
            raw_name = attempt_name if attempt_number == 0 else f"{attempt_name}_retry"
            raw_path = _attempt_raw_path(out, raw_name)
            last_raw_path = raw_path
            raw_path.unlink(missing_ok=True)
            cmd = [codex_bin]
            if enable_search:
                cmd.append("--search")
            cmd.extend([
                "exec",
                "--ephemeral",
                "--ignore-rules",
                "--skip-git-repo-check",
                "-C",
                str(out.parent),
                "--output-schema",
                str(OUTPUT_SCHEMA),
                "--output-last-message",
                str(raw_path),
                attempt_prompt,
            ])
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=codex_env)
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout if isinstance(exc.stdout, str) else ""
                stderr = exc.stderr if isinstance(exc.stderr, str) else ""
                _write_attempt_diagnostic(
                    raw_path,
                    last_message="",
                    stdout=stdout,
                    stderr=stderr,
                )
                failures.append(f"{raw_name} timed out after {timeout}s")
                break

            last_message = raw_path.read_text(encoding="utf-8") if raw_path.exists() else ""
            _write_attempt_diagnostic(
                raw_path,
                last_message=last_message,
                stdout=result.stdout,
                stderr=result.stderr,
            )
            if result.returncode != 0:
                failures.append(f"{raw_name} exited {result.returncode} without a valid final JSON")
                if (
                    attempt_number < len(CODEX_RETRY_DELAYS_SECONDS)
                    and _is_transient_provider_error(result.stderr)
                ):
                    retry_delay = CODEX_RETRY_DELAYS_SECONDS[attempt_number] + (
                        sum(str(out.parent).encode("utf-8")) % 7
                    )
                    print(
                        f"[warn] {attempt_name} hit a transient provider error; retrying in "
                        f"{retry_delay}s",
                        file=sys.stderr,
                    )
                    time.sleep(retry_delay)
                    attempt_number += 1
                    continue
                break
            try:
                payload = extract_json_object(last_message or result.stdout)
                return validate_decision_payload(payload)
            except Exception as exc:
                failures.append(f"{raw_name} returned an invalid decision: {exc}")
                break

    failure_summary = "; ".join(failures)
    diagnostics_path = last_raw_path or _attempt_raw_path(out, "online")
    print(
        f"[warn] codex review degraded to deterministic hold: {failure_summary}; "
        f"diagnostics: {diagnostics_path}",
        file=sys.stderr,
    )
    return deterministic_hold(failure_summary)


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
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.analyze import enforce_pullback_stops, tighten_entry_stops

    payload = tighten_entry_stops(payload, prefilter, positions)
    payload = enforce_pullback_stops(payload, prefilter, positions)
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
