"""act 标签兜底索引（防剧透 P1 的检索侧补强）。

背景：BG3 向量库只有约 10% 的 wiki 块带 `act` 元数据（结构化 locations/quests
能直接透传，wiki 正文只能从分类猜）。这会让 max_act 过滤漏掉大量同名地点/任务页。

做法：从结构化 SQLite 的 locations/quests 表读 `name → act`，检索时对**没有 act
字段**的文档按标题查一次。只做同名精确匹配（忽略大小写），不猜、不强绑。
DB 不可用/表不存在时返回空映射，行为退化为「缺 act 放行」，不影响检索可用性。

说明：这是查询期补丁，不替代建库时打标；重建 BG3 库后仍应优先使用 metadata.act。
"""

from __future__ import annotations

import os
import sqlite3
from functools import lru_cache
from typing import Dict

from rag_agent.game_registry import AVAILABLE_GAMES


@lru_cache(maxsize=8)
def act_by_title(game: str) -> Dict[str, int]:
    """返回 {规范化标题: act}；DB/表缺失时返回空 dict（永不抛错）。"""
    cfg = AVAILABLE_GAMES.get(game) or {}
    db_path = cfg.get("db_path")
    if not db_path or not os.path.isfile(str(db_path)):
        return {}

    mapping: Dict[str, int] = {}
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        for table in ("locations", "quests"):
            try:
                rows = conn.execute(
                    f"SELECT name, act FROM {table} WHERE act IS NOT NULL"
                ).fetchall()
            except sqlite3.Error:
                continue
            for name, act in rows:
                if not name or act is None:
                    continue
                try:
                    mapping[str(name).strip().lower()] = int(act)
                except (TypeError, ValueError):
                    continue
    except sqlite3.Error:
        return {}
    finally:
        if conn is not None:
            conn.close()
    return mapping


@lru_cache(maxsize=8)
def _long_names(game: str) -> tuple[tuple[str, int], ...]:
    """可做「标题包含」匹配的长名称，按长度降序，避免短词误伤。"""
    names = []
    for name, act in act_by_title(game).items():
        # 只收足够长的名字：英文 6 字符、中文 3 字，避免 "Water" 这类通用词
        if (name.isascii() and len(name) >= 6) or (not name.isascii() and len(name) >= 3):
            names.append((name, act))
    return tuple(sorted(names, key=lambda item: len(item[0]), reverse=True))


@lru_cache(maxsize=8192)
def title_act(game: str, title: str | None) -> int | None:
    """按文档标题查 act（没有就 None）；供 tools 的 max_act 过滤兜底。

    先精确匹配；再用长名称做包含匹配，覆盖
    "Lower City and Upper City" / "Civilian (Lower City)" 这类复合标题。
    """
    if not title:
        return None
    low = title.strip().lower()
    exact = act_by_title(game).get(low)
    if exact is not None:
        return exact
    for name, act in _long_names(game):
        if name in low:
            return act
    return None
