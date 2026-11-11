#!/usr/bin/env python3
"""术语表 query 扩展探针（只读，不接检索链路）。

用法：
    python3 scripts/eval/glossary_probe.py "影心喜欢什么"
    python3 scripts/eval/glossary_probe.py --game baldurs_gate3 "月出之塔怎么走"
    python3 scripts/eval/glossary_probe.py --suite          # 跑内置样本集

输出每条问句命中的中文专名、英文原名与置信度，用于人工判断扩展是否靠谱。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rag_agent.glossary import dropped_terms, expand_query, format_hits  # noqa: E402

SUITE = [
    "影心喜欢什么礼物",
    "月出之塔怎么走",
    "至圣斩怎么用",
    "阿斯代伦在哪儿招募",
    "怎么打奥利弗这个小孩",
    "钢铁卫士铸造厂在哪",
    "下城区有什么商人",
    "卡菈克的心脏怎么修",
    "黑暗术和黑暗视觉有什么区别",
    "养鸡妹是哪一位",
    "我想知道第三章有哪些支线任务",
    "这个法术要几级才能学",
    # 反例：不含专名的日常中文，应该零命中
    "今天食堂的菜太贵了，我吃了面条",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="*")
    ap.add_argument("--game", default="baldurs_gate3")
    ap.add_argument("--min-conf", type=float, default=0.7)
    ap.add_argument("--suite", action="store_true", help="跑内置样本集")
    args = ap.parse_args()

    print(format_hits(args.game))
    print()
    queries = SUITE if args.suite or not args.queries else args.queries

    for q in queries:
        expanded, hits = expand_query(q, args.game, min_conf=args.min_conf)
        dropped = dropped_terms(q, args.game, min_conf=args.min_conf)
        if not hits:
            tail = ""
            if dropped:
                tail = "  [被挡下: " + "，".join(f"{zh}→{en}({conf})" for zh, en, conf, _f in dropped) + "]"
            print(f"[0 命中] {q}{tail}")
            continue
        print(f"[{len(hits)} 命中] {q}")
        for zh, en, conf, freq in hits:
            print(f"    {zh} → {en}  (conf {conf}, freq {freq})")
        if dropped:
            print("    被挡下: " + "，".join(f"{zh}→{en}({conf})" for zh, en, conf, _f in dropped))
        print(f"    → {expanded}")
        print()


if __name__ == "__main__":
    main()
