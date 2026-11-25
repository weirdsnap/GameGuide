#!/usr/bin/env python
"""检索冒烟：用几条真实提问打真实检索路径，看 top-k 到底召回了什么。

为什么要有这个：向量库「能加载」「language 标签齐全」「pytest 全绿」都不等于
**检索可用**。2026-11-25 terraria / silksong 两个新库上传后，主套件 330 passed，
但实跑「克苏鲁之眼怎么召唤」top-4 全是 Demon's Eye / Eye of the Golem 这种碎片，
页面本身没进 top-k。所以每次重建库、换 embedding、改分块，都要跑这个。

判定口径（故意宽松，只看「该出现的条目有没有出现在 title 里」）：
    PASS  期望关键词出现在 top-k 的某个 title 里
    MISS  没出现
不判内容质量、不调 LLM，秒级完成。

用法：
    python scripts/eval/retrieval_smoke.py                    # 跑全部用例
    python scripts/eval/retrieval_smoke.py --game terraria     # 只跑某个游戏
    python scripts/eval/retrieval_smoke.py -k 6 --verbose      # 放宽 top-k、打印 title
退出码：有 MISS 则 1。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rag_agent.tools import search_knowledge_base  # noqa: E402

# (game, 查询, 期望出现在 top-k title 里的关键词)
# 用例取自线上真实问法 / 各游戏的核心实体，不追求覆盖全部内容。
CASES: list[tuple[str, str, str]] = [
    # terraria：中文 wiki 全量替换后的新库，含页内小节被切碎的问题
    ("terraria", "克苏鲁之眼怎么召唤", "克苏鲁之眼"),
    ("terraria", "血月怎么触发", "血月|Blood Moon"),
    # 该页 zh 标题是「天界柱」，但混合库里 en 页 Celestial Pillars 常排第一，两者都算命中
    ("terraria", "天界塔怎么打", "天界柱|Celestial Pillars"),
    # silksong：新库，79 页，覆盖不足
    ("silksong", "Silk Soar ability", "Silk Soar"),
    ("silksong", "Bell Beast boss fight", "Bell Beast"),
    # hollow_knight：中文块只有 2.4%
    ("hollow_knight", "螳螂爪怎么获得", "Mantis Claw"),
    ("hollow_knight", "深巢入口在哪", "Deepnest"),
    # oni / va11halla / mhw
    ("oni", "氧气怎么生产", "Oxygen"),
    ("va11halla", "Julianne Stingray 是谁", "Julianne"),
    # 旧期望 "MHWilds" 是在「全库塌成一个 title=MHWilds」的坏库上写的，已重建，改判条目名
    ("mhw", "大剑怎么玩", "Great Sword"),
    # baldurs_gate3：线上主力，块/title 正常，作为对照组
    ("baldurs_gate3", "明斯克在哪", "Minsc"),
    ("baldurs_gate3", "至圣斩怎么用", "Smite"),
]

HIT_RE = re.compile(r"【参考 \d+】\[(\w+)\] (.+)")


def run(game: str, question: str, expect: str, k: int, verbose: bool) -> bool:
    """expect 支持 `中文|English` 形式：任一命中即算 PASS（库里有的是英文 title 时用）。"""
    out = search_knowledge_base.invoke({"query": question, "game": game, "k": k})
    titles = [t.strip() for _lang, t in HIT_RE.findall(out)]
    needles = [n.strip().lower() for n in expect.split("|") if n.strip()]
    ok = any(n in t.lower() for t in titles for n in needles)
    mark = "PASS" if ok else "MISS"
    print(f"[{mark}] {game:16s} {question}")
    if verbose or not ok:
        for i, t in enumerate(titles[:k], 1):
            print(f"         {i}. {t[:60]}")
        if not titles:
            print(f"          (无结果: {out[:100]})")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", help="只跑指定游戏，多个用逗号分隔（分批跑可控制内存）")
    ap.add_argument("-k", type=int, default=4, help="top-k（默认 4）")
    ap.add_argument("--verbose", action="store_true", help="打印每条用例的 top-k title")
    args = ap.parse_args()

    only = {g.strip() for g in args.game.split(",")} if args.game else None
    cases = [c for c in CASES if not only or c[0] in only]
    if not cases:
        print(f"没有匹配的用例（--game {args.game}）")
        return 2

    passed = 0
    for game, q, expect in cases:
        try:
            if run(game, q, expect, args.k, args.verbose):
                passed += 1
        except Exception as e:                        # 库缺失等
            print(f"[ERR ] {game:16s} {q}  -> {e}")

    total = len(cases)
    print(f"\n通过 {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
