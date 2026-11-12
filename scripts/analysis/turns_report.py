#!/usr/bin/env python3
"""turn 日志统计分析（服务器侧，不跑模型）。

数据源：data/turns/YYYY-MM-DD.jsonl（api_server.log_turn 追加写，重启不丢）。
用途：看每日用量/游戏分布/路由结果/耗时分布、挖 badcase、挑剧透回归样本。

用法:
    python3 scripts/analysis/turns_report.py                # 汇总全部已有日志
    python3 scripts/analysis/turns_report.py --days 3       # 只看最近 3 天
    python3 scripts/analysis/turns_report.py --spoilers     # 只列剧透命中样本
    python3 scripts/analysis/turns_report.py --game baldurs_gate3
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

TURNS_DIR = Path(__file__).resolve().parents[2] / "data" / "turns"


def load_records(days: int | None) -> list[dict]:
    files = sorted(TURNS_DIR.glob("*.jsonl"))
    if days:
        cutoff = datetime.now().date() - timedelta(days=days - 1)
        files = [f for f in files if datetime.strptime(f.stem, "%Y-%m-%d").date() >= cutoff]
    records = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 写一半的残行跳过
    return records


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, int(round(q * (len(values) - 1))))
    return values[idx]


def main() -> None:
    ap = argparse.ArgumentParser(description="turn 日志分析")
    ap.add_argument("--days", type=int, default=None, help="只看最近 N 天")
    ap.add_argument("--game", default=None, help="按游戏过滤")
    ap.add_argument("--spoilers", action="store_true", help="只列剧透命中样本")
    args = ap.parse_args()

    records = load_records(args.days)
    if args.game:
        records = [r for r in records if r.get("game") == args.game]
    if not records:
        print(f"没有可用记录（目录 {TURNS_DIR}）。注意：turns JSONL 从 2026-11-12 起才落盘。")
        return

    if args.spoilers:
        hits = [r for r in records if r.get("spoiler_hits")]
        print(f"剧透命中样本 {len(hits)} / {len(records)} 轮\n")
        for r in hits:
            ts = datetime.fromtimestamp(r["ts"]).strftime("%m-%d %H:%M")
            terms = ", ".join(f"{h['term']}(act{h['act']})" for h in r["spoiler_hits"])
            print(f"[{ts}] {r.get('game')} | {r.get('question','')[:40]}")
            print(f"    命中: {terms}")
            head = r.get("answer_head", "")
            if head:
                print(f"    答案: {head[:120]}")
            print()
        return

    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_day[datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d")].append(r)

    print(f"总轮次 {len(records)}，覆盖 {len(by_day)} 天\n")
    print(f"{'日期':<12}{'轮次':>6}{'独立IP':>8}{'均耗s':>8}{'P90s':>7}{'失败':>6}{'剧透命中':>8}")
    for day in sorted(by_day):
        rs = by_day[day]
        ips = {r.get("ip") for r in rs}
        elapsed = [r.get("elapsed_ms", 0) / 1000 for r in rs]
        fails = sum(1 for r in rs if r.get("error") or r.get("route") == "error")
        spoilers = sum(1 for r in rs if r.get("spoiler_hits"))
        print(f"{day:<12}{len(rs):>6}{len(ips):>8}{statistics.mean(elapsed):>8.1f}"
              f"{pct(elapsed, 0.9):>7.1f}{fails:>6}{spoilers:>8}")

    games = Counter(r.get("game") or "<未识别>" for r in records)
    routes = Counter(r.get("route") or "<空>" for r in records)
    print("\n游戏分布:", ", ".join(f"{g} {c}" for g, c in games.most_common()))
    print("路由分布:", ", ".join(f"{k} {v}" for k, v in routes.most_common()))

    tin = [r.get("tokens_in", 0) for r in records]
    tout = [r.get("tokens_out", 0) for r in records]
    tools = [r.get("tool_calls", 0) for r in records]
    print(f"\ntokens_in  合计 {sum(tin):,}  均 {statistics.mean(tin):,.0f}  最大 {max(tin):,}")
    print(f"tokens_out 合计 {sum(tout):,}  均 {statistics.mean(tout):,.0f}  最大 {max(tout):,}")
    print(f"工具调用   合计 {sum(tools)}  均 {statistics.mean(tools):.1f}  最大 {max(tools)}")

    lens = [len(r.get("question", "")) for r in records]
    print(f"提问长度   均 {statistics.mean(lens):.1f} 字")

    print("\n最近 5 轮:")
    for r in records[-5:]:
        ts = datetime.fromtimestamp(r["ts"]).strftime("%m-%d %H:%M")
        print(f"  [{ts}] {r.get('game') or '-'} | {r.get('question','')[:50]} | {r.get('elapsed_ms',0)/1000:.1f}s")


if __name__ == "__main__":
    main()
