"""中英专名映射（query 扩展用）。

数据来自 `scripts/enrich/build_glossary.py` 的产物：
`games/<game>/data/<game>_glossary.jsonl`，每行字段
`name_en / base_en / kind / name_zh / conf / variants / wiki_freq / model / ts`。

用途：不改向量库，检索前把中文问句里命中的中文专名追加英文原名，
让纯英文 wiki 文档也能被中文问题命中（跨语专名是当前主要检索弱点）。

本模块只提供查询与扩写，不主动接入检索链路；接入由 tools.py 显式调用。

匹配策略：按中文名长度降序做「最长优先 + 区间遮挡」，避免「奥利弗」被截成
「奥利」这类错配；收录条件双轨——高置信直接用，中置信但 wiki 高频也放行。
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Dict, List, Tuple

from rag_agent.game_registry import GAMES_DIR, GAME_REGISTRY

# 高置信：直接用
DEFAULT_MIN_CONF = 0.7
# 中置信：需要 wiki 出现次数 >= freq_cut 才放行（多为重要但模型没把握的专名）
SOFT_MIN_CONF = 0.5
DEFAULT_FREQ_CUT = 500
# 中文名过短（1 字）几乎都是噪声，比如单字地名/姓
MIN_ZH_LEN = 2

_PAREN_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")


def glossary_path(game: str) -> str:
    cfg = GAME_REGISTRY[game]
    return str(GAMES_DIR / cfg["dir"] / "data" / f"{cfg['dir']}_glossary.jsonl")


@lru_cache(maxsize=16)
def load_glossary(game: str) -> Tuple[Dict, ...]:
    """读取某游戏的术语表；文件不存在时返回空元组（不抛错）。"""
    path = glossary_path(game)
    if not os.path.isfile(path):
        return ()
    rows: List[Dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return tuple(rows)


def _clean_en(en: str) -> str:
    """去掉建表时加的类别后缀，如 `Darkvision (spell)` → `Darkvision`。"""
    return _PAREN_SUFFIX.sub("", en).strip()


@lru_cache(maxsize=16)
def _zh_index(game: str) -> Tuple[Tuple[str, str, float, int], ...]:
    """(中文名, 英文原名, 置信度, wiki 频次)，按中文名长度降序，用于最长优先匹配。

    除 name_zh 外也索引 variants——玩家外号/别名（如 养鸡妹→Lae'zel）走这里。
    """
    pairs = []
    for r in load_glossary(game):
        en = _clean_en((r.get("name_en") or "").strip())
        if not en:
            continue
        zh_names = [r.get("name_zh")] + list(r.get("variants") or [])
        for zh in zh_names:
            zh = (zh or "").strip()
            if not zh or len(zh) < MIN_ZH_LEN:
                continue
            pairs.append((zh, en, float(r.get("conf") or 0), int(r.get("wiki_freq") or 0)))
    pairs.sort(key=lambda x: (-len(x[0]), -x[2]))
    return tuple(pairs)


def expand_query(query: str, game: str, min_conf: float = DEFAULT_MIN_CONF,
                 freq_cut: int = DEFAULT_FREQ_CUT, max_terms: int = 6
                 ) -> Tuple[str, List[Tuple[str, str, float, int]]]:
    """把问句里命中的中文专名补上英文原名。

    返回 (扩展后的查询, 命中列表)；命中列表元素为 (中文, 英文, 置信度, 频次)。
    没有命中时原样返回。已出现在问句里的英文原样保留，不重复追加。
    """
    if not query:
        return query, []
    hits: List[Tuple[str, str, float, int]] = []
    masked = query                      # 已匹配区间抹掉，避免短名重复命中
    seen_en = set()
    lower_q = query.lower()

    for zh, en, conf, freq in _zh_index(game):
        if zh not in masked:
            continue
        masked = masked.replace(zh, "\u3000")
        usable = conf >= min_conf or (conf >= SOFT_MIN_CONF and freq >= freq_cut)
        if not usable:
            continue
        if en.lower() in lower_q or en.lower() in seen_en:
            continue
        hits.append((zh, en, conf, freq))
        seen_en.add(en.lower())
        if len(hits) >= max_terms:
            break

    if not hits:
        return query, []
    # 英文原名**前置**而不是括号后缀追加：BG3 实测「卡菈克是谁 (Karlach)」
    # 里 Karlach 排不进 top-10（后缀权重太弱，被中文问句稀释），
    # 前置「Karlach 卡菈克是谁」则影心/卡菈克双双 rank 1（2026-11-12 实测）。
    prefix = " ".join(en for _zh, en, _c, _f in hits)
    return f"{prefix} {query}", hits


def dropped_terms(query: str, game: str, min_conf: float = DEFAULT_MIN_CONF,
                  freq_cut: int = DEFAULT_FREQ_CUT) -> List[Tuple[str, str, float, int]]:
    """诊断用：问句里命中了但被收录条件挡下的中文专名，供人工校订抽检。"""
    out = []
    masked = query
    for zh, en, conf, freq in _zh_index(game):
        if zh not in masked:
            continue
        masked = masked.replace(zh, "\u3000")
        usable = conf >= min_conf or (conf >= SOFT_MIN_CONF and freq >= freq_cut)
        if not usable:
            out.append((zh, en, conf, freq))
    return out


def format_hits(game: str) -> str:
    """诊断用：术语表规模与置信度分布。"""
    rows = load_glossary(game)
    if not rows:
        return f"{game}: 没有术语表"
    high = sum(1 for r in rows if r.get("conf", 0) >= 0.9)
    mid = sum(1 for r in rows if 0.7 <= r.get("conf", 0) < 0.9)
    low = len(rows) - high - mid
    soft = sum(1 for r in rows if 0.5 <= r.get("conf", 0) < 0.7 and r.get("wiki_freq", 0) >= DEFAULT_FREQ_CUT)
    return (f"{game}: {len(rows)} 条（高 {high} / 中 {mid} / 低 {low}），"
            f"可用扩展 = 高+中 {high + mid} 条 + 高频低置信 {soft} 条")
