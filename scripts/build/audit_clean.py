#!/usr/bin/env python3
"""审计 clean_wiki.py 到底删了什么——按规则列出每处删除，供人工判定是否误删正文。

两条独立检查（并行看，任一条异常都要查）：
  1. 规则级：对 .bak 原文件重跑清洗（不落盘），列出每类删除的数量 + 样例
  2. 文件级：节数是否一致、清洗后是否有空白节

用法：
  python3 scripts/build/audit_clean.py                 # 概览 + 每类 3 条样例
  python3 scripts/build/audit_clean.py --type dup_line --rows 20
  python3 scripts/build/audit_clean.py --game terraria
"""
from __future__ import annotations

import argparse
import collections
import glob
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_cleaner():
    spec = importlib.util.spec_from_file_location("clean_wiki", HERE / "clean_wiki.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sections(text: str) -> list[tuple[str, str]]:
    # 只把「# 后跟空白或行尾」当标题（避免把 #Damage 这类行首 # 当标题，与 clean_wiki 一致）
    parts = re.split(r"(?m)^(#{1,3}(?:\s[^\n]*)?)$", text)
    return [(parts[i].lstrip("#").strip(), parts[i + 1]) for i in range(1, len(parts), 2)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=3)
    ap.add_argument("--type", default=None, help="只看某类，如 dup_line / nav_line / tpl_*")
    ap.add_argument("--game", default=None)
    args = ap.parse_args()

    cw = load_cleaner()
    pat = f"games/{args.game}/data/wiki_data*.md.bak-*" if args.game else "games/*/data/wiki_data*.md.bak-*"
    baks = sorted(glob.glob(pat))
    if not baks:
        print("没有 .bak-* 备份")
        return 1

    for bak in baks:
        cur = re.sub(r"\.bak-\d+$", "", bak)
        if not Path(cur).exists():
            continue
        old = Path(bak).read_text(encoding="utf-8", errors="ignore")
        new = Path(cur).read_text(encoding="utf-8", errors="ignore")

        # —— 规则级：重跑一遍清洗，收集删除明细
        cw.AUDIT.clear()
        stats: dict = {}
        cw.clean_file(Path(bak), False, 0, stats, [])
        audit = list(cw.AUDIT)

        so, sn = sections(old), sections(new)
        empty = [t for t, b in sn if len(re.sub(r"\s+", "", b)) < 40]

        print(f"\n{'='*78}\n=== {cur}")
        print(f"  体积 {len(old.encode())/1024:.0f}KB → {len(new.encode())/1024:.0f}KB | "
              f"节数 {len(so)} → {len(sn)} "
              f"{'✅' if len(so) == len(sn) else '❌ 不一致'}")
        print(f"  清洗后空白节: {len(empty)}" + (f" → {empty[:6]}" if empty else ""))

        kinds = collections.Counter(k for k, _, _ in audit)
        deleted_bytes = sum(len(t.encode()) for _, _, t in audit)
        print(f"  删除物件合计 {len(audit)} 项（记录到的内容约 {deleted_bytes/1024:.0f}KB，"
              "剩余体积差来自空白行/尾空格压缩）:")
        for k, c in kinds.most_common():
            if args.type and not k.startswith(args.type):
                continue
            print(f"    {k:22s} {c:5d}")
            shown = 0
            for kk, sec, txt in audit:
                if kk != k or shown >= args.rows:
                    continue
                print(f"        ({sec[:26]}) {txt[:150]}")
                shown += 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
