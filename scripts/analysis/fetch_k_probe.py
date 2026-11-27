#!/usr/bin/env python
"""I-31 实测：`fetch_k` 与标题置顶对中文块命中的影响（只读，不改检索链路）。

背景（docs/ISSUES_2026-11-25.md I-31）：langchain 的
`FAISS.similarity_search_with_score` 是「先按 fetch_k 取全局最近邻，再叠加 filter」，
所以 fetch_k=200 时一个排在全局第 500 名开外的中文块无论多相关都进不了候选池。

这个脚本把 tools.py 的检索段（分语言 + 按分数合并）复刻出来，只把 fetch_k 参数化，
对同一批真实问法跑几种策略，输出命中与耗时，用来判断改法是否值得动主链路：

    fixed200   现状：两路都是 200
    lang_ratio fetch_k ≈ k * 全库块数 / 该语言块数（按语言配额放大）
    full       fetch_k = 全库块数（等价于先全量重排再过滤，最贵也最准）
    title_pin  fixed200+full 合并后，把「query 字面包含 title」的块置顶
               （tools.py 已有 pinned 机制，但只对 glossary 命中的英文名生效）

用法：
    python scripts/analysis/fetch_k_probe.py                     # 全部用例
    python scripts/analysis/fetch_k_probe.py --game hollow_knight --modes fixed200,title_pin
    python scripts/analysis/fetch_k_probe.py --query 'hollow_knight|螳螂爪怎么获得|Mantis Claw|螳螂爪'
退出码恒 0（诊断工具，不做 CI 断言；判定看输出的 PASS/MISS 列）。
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

# embedding ONNX 模型缓存在仓库 .cache/fastembed（与 api.sh、retrieval_smoke 一致）
_CACHE = ROOT / ".cache" / "fastembed"
if _CACHE.is_dir():
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(_CACHE))

from rag_agent.game_registry import AVAILABLE_GAMES  # noqa: E402
from rag_agent.glossary import expand_query  # noqa: E402
from rag_agent.vectorstore import load_vectorstore  # noqa: E402

# (game, 查询, 期望出现在 top-k title 里的正则)
# HK 三条是 I-31 的靶子；ONI/VA11 是另外两个中英混合库；BG3 是大库对照组。
CASES: list[tuple[str, str, str]] = [
    ("hollow_knight", "螳螂爪怎么获得", r"Mantis Claw|螳螂爪"),
    ("hollow_knight", "深巢入口在哪", r"Deepnest|深巢"),
    ("hollow_knight", "苍白矿石在哪拿", r"Pale Ore|苍白矿石"),
    ("oni", "氧气怎么生产", r"Oxygen"),
    ("va11halla", "Julianne Stingray 是谁", r"Julianne"),
    ("baldurs_gate3", "明斯克在哪", r"Minsc"),
    ("baldurs_gate3", "至圣斩怎么用", r"Smite"),
]

MODES = ("fixed200", "lang_ratio", "full", "title_pin")


def lang_counts(vs) -> dict[str, int]:
    """按 docstore 里的 language 元数据统计块数（与检索 filter 口径一致）。"""
    counts: dict[str, int] = {}
    for d in getattr(vs.docstore, "_dict", {}).values():
        lang = (d.metadata or {}).get("language")
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return counts


def fetch_k_for(mode: str, lang: str, k: int, total: int, counts: dict[str, int]) -> int:
    if mode in ("fixed200", "title_pin"):
        return min(200, max(total, 1))
    if mode == "full":
        return max(total, 1)
    if mode == "lang_ratio":
        blocks = counts.get(lang, 0)
        if blocks <= 0:
            return 0
        return max(k, min(total, math.ceil(k * total / blocks)))
    raise ValueError(f"未知模式 {mode}")


def vector_part(vs, query: str, game: str, k: int, mode: str, counts: dict[str, int]):
    """两路各自检索，按分数合并（= tools.py 现状逻辑，仅 fetch_k 可变）。"""
    total = vs.index.ntotal
    search_query = query
    try:
        search_query, _hits = expand_query(query, game)
    except Exception:
        pass
    scored, per_lang = [], {}
    for lang in ("en", "zh"):
        fk = fetch_k_for(mode, lang, k, total, counts)
        if fk <= 0:
            continue
        rows = vs.similarity_search_with_score(
            search_query, k=k, filter={"language": lang}, fetch_k=fk
        )
        per_lang[lang] = {"fetch_k": fk, "n": len(rows)}
        scored.extend(rows)
    scored.sort(key=lambda t: t[1])
    return scored, per_lang


def game_name_titles(cfg: dict) -> set[str]:
    """游戏名/别名构成的排除集：这些词几乎出现在每个提问里，命中也没有区分度，
    却会把整页同名块全顶上来（实测「空洞骑士护符推荐」被 4 条「空洞骑士」块霸屏）。"""
    raw = (cfg.get("name") or "").strip()
    out = set()
    m = re.match(r"^(.*?)\s*[（(](.*?)[）)]\s*$", raw)
    if m:
        out.update({m.group(1).strip(), m.group(2).strip()})
    elif raw:
        out.add(raw)
    out.add("bg3")
    return {t.lower().replace(" ", "") for t in out if t}


def title_pin(vs, query: str, k: int, base_rows, exclude: set[str] | None = None,
              max_per_title: int = 1):
    """模拟「query 字面包含 title 就置顶」。规则：

    - title 长度 ≥2 且（小写去空格后）完整出现在 query 里；
    - 排除游戏名/别名（exclude）；
    - 多条命中时 title 越长越具体、优先置顶；同一 title 最多取 max_per_title 条；
    - 按正文去重（不能按 title：一页多块会被压成一条）；
    - 不足 k 条用向量结果补齐。
    """
    exclude = exclude or set()
    q = query.lower().replace(" ", "")
    cands = []
    for d in getattr(vs.docstore, "_dict", {}).values():
        t = ((d.metadata or {}).get("title") or "").strip()
        key = t.lower().replace(" ", "")
        if len(t) >= 2 and key not in exclude and key in q:
            cands.append((len(t), t, d))
    cands.sort(key=lambda x: -x[0])
    out, per_title = [], {}
    for _len, t, d in cands:
        if per_title.get(t, 0) >= max_per_title:
            continue
        per_title[t] = per_title.get(t, 0) + 1
        out.append(d)
    out += [d for d, _s in base_rows]
    seen, final = set(), []
    for d in out:
        # 键必须是 (title, 正文)：只按正文会误合并「同内容不同页」的块——
        # HK 库里有一批 3~8 字的残块正文完全相同，按正文去重会把正常页一并吞掉
        h = ((d.metadata or {}).get("title", ""), hash(d.page_content))
        if h in seen:
            continue
        seen.add(h)
        final.append((d, 0.0))
    return final[:k], [t for _l, t, _d in cands[:5]]


def retrieve(vs, query: str, game: str, k: int, mode: str, counts: dict[str, int]):
    if mode == "title_pin":
        # 底座只用现状（fixed200）：早先把 full 合并进来会让 zh 路多出的低分块
        # 插进 top-k（实测「苍白矿石在哪拿」top4 被「店主的钥匙」顶掉一格）
        base, per_lang = vector_part(vs, query, game, k, "fixed200", counts)
        rows, pinned = title_pin(vs, query, k, base,
                                 exclude=game_name_titles(AVAILABLE_GAMES[game]))
        return rows, per_lang, pinned
    scored, per_lang = vector_part(vs, query, game, k, mode, counts)
    return scored[:k], per_lang, None


def titles(rows) -> list[str]:
    return [((d.metadata or {}).get("title") or "?") for d, _s in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", action="append", help="只跑指定游戏（可重复）")
    ap.add_argument("--k", type=int, default=4, help="top-k（默认 4，与线上截断口径一致）")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--query", action="append", help="覆盖用例，格式 game|查询|期望正则")
    args = ap.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    cases = CASES
    if args.query:
        cases = []
        for raw in args.query:
            g, q, exp = raw.split("|", 2)
            cases.append((g, q, exp))
    if args.game:
        keep = set(args.game)
        cases = [c for c in cases if c[0] in keep]

    cache: dict[str, tuple] = {}
    for game, query, expected in cases:
        cfg = AVAILABLE_GAMES.get(game)
        if not cfg:
            print(f"[跳过] 未知游戏 {game}")
            continue
        if game not in cache:
            t0 = time.time()
            vs = load_vectorstore(save_dir=cfg["vectorstore_dir"])
            counts = lang_counts(vs)
            cache[game] = (vs, counts)
            print(f"\n=== {game}: {vs.index.ntotal} 块，lang={counts}，"
                  f"载入 {time.time() - t0:.1f}s ===")
        vs, counts = cache[game]
        pat = re.compile(expected, re.I)
        print(f"\n[{game}] {query}")
        for mode in modes:
            t0 = time.time()
            rows, per_lang, pinned = retrieve(vs, query, game, args.k, mode, counts)
            cost = (time.time() - t0) * 1000
            hit = any(pat.search(t) for t in titles(rows))
            if mode == "title_pin":
                fk = "fixed200"
            else:
                fk = ",".join(f"{l}:{v['fetch_k']}/{v['n']}条" for l, v in per_lang.items())
            extra = f"  pinned={pinned}" if pinned else ""
            print(f"  {'PASS' if hit else 'MISS'}  {mode:<10} {cost:7.1f}ms  fetch_k({fk})"
                  f"  top{args.k}={titles(rows)}{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
