"""进度模型加载层（S1）：chain 模型从 `config/progress/*.json` 读配置。

设计文档：docs/PROGRESS_AXIS_DESIGN.md（迁移路径 S1）。
S1 只接 chain 模型（线性推进的游戏，目前 BG3）；dag 模型（丝之歌）在 S2 接入。
schema 契约测试：tests/test_progress_model_schema.py（草案与正式目录同一套校验）。

行为口径：加载层只提供数据（锚点词表 / 明确说法正则），推断逻辑
（max 合并、证据收集、只扫 user 消息）留在 progress.py，S1 不改行为。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = REPO_ROOT / "config" / "progress"


class ChainModel:
    """线性进度模型：rank 按序（章/幕），锚点把词映射到 rank。

    anchors: {rank: 词表}——推断层做 substring 匹配，命中即认为用户「至少到这一章」。
    explicit_patterns: 配置文件里的正则字符串，加载时编译（IGNORECASE）。
    """

    def __init__(self, data: dict, source_path: Path):
        self.game: str = data["game"]
        self.axis_label: str = data.get("axis_label", "章")
        self.max_rank: int = int(data.get("max_rank", 0))
        self.anchors: Dict[int, Tuple[str, ...]] = {
            int(rank): tuple(words)
            for rank, words in data.get("anchors", {}).items()
        }
        self.explicit_patterns: Tuple[re.Pattern, ...] = tuple(
            re.compile(p, re.IGNORECASE)
            for p in data.get("explicit_patterns", ())
        )
        self.reviewed: bool = bool(data.get("reviewed", False))
        self.source_path: Path = source_path


@lru_cache(maxsize=8)
def load_chain_model(game: str) -> Optional[ChainModel]:
    """按游戏加载 chain 模型；文件不存在返回 None（该游戏不做进度推断）。

    lru_cache：配置文件更新后必须重启进程才生效（与向量库 lru_cache 同口径）。
    """
    path = CONFIG_DIR / f"{game}.chain.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return ChainModel(data, path)
