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
from dataclasses import dataclass
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


# ═══════════════════════════════════════════════
# ║  S2：dag 模型 + ProgressBound
# ═══════════════════════════════════════════════

@dataclass(frozen=True)
class ProgressBound:
    """模型层输出的进度区间（设计文档 §4 Protocol 的 bound 返回值）。

    lo 等价于旧 Progress.level（已确认到达的最深层）；hi 是确认没到的
    最浅层减一。chain 模型 lo == hi；dag 模型 reviewed:false 时不剪 hi。
    """
    lo: Optional[int]
    hi: Optional[int]
    axis_label: str
    source: str                       # question | history | none
    evidence: Tuple[str, ...] = ()
    not_reached: Tuple[str, ...] = ()  # reverse 证据命中的区域名（§5.1）

    @property
    def known(self) -> bool:
        return self.lo is not None


class DagModel:
    """DAG 进度模型：区域/能力节点 + 前置边（适用丝之歌这类非线性游戏）。

    当前是「锚点版」形态（§4.3）：只依赖 areas.*.act 做
    「用户提到该区域 → lo 至少抬到该 act」；reviewed:false 下不做
    可达集剪枝（§5.3），hi 恒等于 lo。边数据齐备且 reviewed:true 后
    （S4）才启用 BFS 可达集与 not_reached 剪枝。
    """

    kind = "dag"

    def __init__(self, data: dict, source_path: Path):
        self.game: str = data["game"]
        self.axis_label: str = data.get("axis_label", "幕")
        self.act_of_node: bool = bool(data.get("act_of_node", False))
        self.reviewed: bool = bool(data.get("reviewed", False))
        self.source_path: Path = source_path
        self.explicit_patterns: Tuple[re.Pattern, ...] = tuple(
            re.compile(p, re.IGNORECASE)
            for p in data.get("explicit_patterns", ())
        )
        self.areas: Dict[str, dict] = {}
        for aid, area in data.get("areas", {}).items():
            self.areas[aid] = {
                "name": area["name"],
                "act": area.get("act"),
                "aliases": tuple(area.get("aliases", ())),
                "requires_areas": tuple(area.get("requires_areas", ())),
                "requires_abilities": tuple(area.get("requires_abilities", ())),
            }
        self.abilities: Dict[str, dict] = {
            aid: {"name": a["name"], "act": a.get("act"),
                  "from": tuple(a.get("from", ()))}
            for aid, a in data.get("abilities", {}).items()
        }
        self.max_rank: int = max(
            (a["act"] or 0) for a in self.areas.values()) or 0

    def evidence_targets(self) -> Tuple[Tuple[str, Optional[int], Tuple[str, ...]], ...]:
        """(区域 id, act, 词表)——供 evidence.py 做文本匹配的词表（§4 Protocol）。

        词表 = 英文名 + aliases（中文社区译名），按词长降序，最长优先。
        """
        targets = []
        for aid, area in self.areas.items():
            terms = (area["name"],) + area["aliases"]
            targets.append((aid, area["act"], tuple(terms)))
        return tuple(targets)

    def bound(self, lo_facts, not_reached, source: str) -> ProgressBound:
        """证据 → 进度区间。

        lo_facts: [(act, 命中词)] 抬 lo 的正向证据（arrival/howto/explicit）。
        not_reached: [区域名] reverse 证据（§5.1「怎么去 X」）。
        reviewed:false：只用正向证据抬 lo；not_reached 留档渲染但不剪 hi。
        """
        lo = max((act for act, _t in lo_facts), default=None)
        # 留档的 not_reached 只保留确实「比 lo 深」的区域名，浅于 lo 的
        # 反向证据无信息量（用户都到了更深的地方，「怎么去浅区」不算反向）。
        nr = tuple(n for n in not_reached)
        if lo is None:
            return ProgressBound(lo=None, hi=None, axis_label=self.axis_label,
                                 source="none", evidence=(), not_reached=nr)
        evidence = tuple(dict.fromkeys(t for a, t in lo_facts if a == lo))
        # reviewed:true 前的简化口径：hi = lo（§5.3「hi < lo 或 None 时 hi = lo」）
        return ProgressBound(lo=lo, hi=lo, axis_label=self.axis_label,
                             source=source, evidence=evidence, not_reached=nr)


@lru_cache(maxsize=8)
def load_dag_model(game: str) -> Optional[DagModel]:
    """按游戏加载 dag 模型；文件不存在返回 None。"""
    path = CONFIG_DIR / f"{game}.dag.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return DagModel(data, path)
