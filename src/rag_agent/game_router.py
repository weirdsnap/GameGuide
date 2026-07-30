#!/usr/bin/env python3
"""
Game Router — 游戏识别与工具调度。

识别用户问题指向哪个游戏，并提供对应的工具。
"""

import os
import re
import sqlite3
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from langchain.tools import tool

from rag_agent.game_registry import AVAILABLE_GAMES, GAME_SIGNALS, GAME_EXACT_PATTERNS, EXTRA_PROMPT_NOTES, GAMES_DIR



def _match_signal(signal: str, q: str) -> bool:
    """检查信号词是否匹配查询。

    纯字母信号词使用相邻英文字母检查避免误触，
    例如 "oni" 不应匹配 "monitor"、"v" 不应匹配 "va11"。
    """
    sl = signal.lower()
    # 纯字母信号词：前后不能紧跟英文字母，避免作为其他单词的一部分被匹配
    if re.match(r'^[a-z]+$', sl):
        return bool(re.search(r'(?<![a-zA-Z])' + re.escape(sl) + r'(?![a-zA-Z])', q))
    # 含中文、空格或非字母的信号词直接用子串匹配
    return sl in q


def detect_game(query: str) -> Tuple[Optional[str], float]:
    """检测用户问题指向哪个游戏。

    Returns:
        (game_key, confidence) 或 (None, 0) 不确定时
    """
    if not query or not query.strip():
        return None, 0

    q = query.lower().strip()

    for game, patterns in GAME_EXACT_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, q):
                return game, 1.0

    # 模糊匹配信号词
    scores: Dict[str, int] = {}
    for game, signals in GAME_SIGNALS.items():
        score = 0
        for signal in signals:
            if _match_signal(signal, q):
                score += 1
        if score > 0:
            scores[game] = score

    if not scores:
        return None, 0

    total = sum(scores.values())
    best_game = max(scores, key=scores.get)
    best_score = scores[best_game]

    # 置信度 = 最佳分数 / 总分
    confidence = best_score / total

    # 如果与第二名差距很小，降低置信度
    sorted_scores = sorted(scores.values(), reverse=True)
    if len(sorted_scores) > 1 and sorted_scores[0] - sorted_scores[1] <= 0:
        confidence *= 0.5

    # 最低阈值 0.3
    if confidence < 0.3:
        return None, confidence

    return best_game, min(confidence, 1.0)


def build_game_prompt(game_key: str) -> str:
    """根据检测到的游戏构建身份声明 prompt。"""
    game_info = AVAILABLE_GAMES.get(game_key)
    if not game_info:
        return ""

    game_name_display = game_info["name"]

    extra = EXTRA_PROMPT_NOTES.get(game_key, "")

    prompt = (
        f"你是一个通用游戏助手，辅助玩家查询各种游戏相关剧情数据攻略等资料，"
        f"你最擅长各种游戏的信息整合等工作。\n"
        f"\n"
        f"你现在正在帮助玩家查询 **{game_name_display}** 的问题。\n"
        f"\n"
        f"你有三个知识来源：\n"
        f"1. **search_knowledge_base** — 向量知识库（剧情、背景、策略、描述类内容）\n"
        f"2. **query_structured_data** — 结构化数据库（数值类：属性、费用、伤害等）\n"
        f"3. **show_database_schema** — 查看数据库的表结构和列名\n"
        f"\n"
        f"在调用 search_knowledge_base 和 query_structured_data 时，"
        f'必须传入 game="{game_key}" 参数来指定游戏。'
    )
    if extra:
        prompt += extra
    return prompt.strip()


def build_common_rules() -> str:
    """构建通用规则（回答规范、剧透管理、游戏边界）。"""
    return """
## 回答规则
- 用中文回答。游戏中的道具、技能、地点、角色等专有名词使用「中文（英文）」格式
  展示（如「亡者之怒（Fury of the Fallen）」），而非英文在前。
- 回答时注明信息来源（知识库或数据库），必要时同时使用两个工具。
- 简洁明了，不超过 3-4 段。绝不编造信息，不确定时说"我不确定"。
- 如果某个工具不可用，降级为仅使用可用来源，如实告知用户。
- **如果一个工具的查询没有返回有效结果，必须换另一个工具再试一次。** 例如：`query_structured_data` 返回空结果时，用 `search_knowledge_base` 再查一次，反之亦然。两个工具都无结果时，再基于自身知识回答并说明"这部分是通用知识，可能存在版本差异"。

## 剧透管理
- 默认不主动透露关键剧情节点、后期 Boss、隐藏结局等剧透内容。
- 用户问题涉及剧情时，先通过追问了解当前游戏进度：
  - 对剧情驱动型游戏（如 VA-11 Hall-A、Cyberpunk 2077），询问玩到了第几天/第几章。
  - 对探索型游戏（如空洞骑士、泰拉瑞亚），询问已获得的能力或已击败的 Boss。
- 根据进度决定回答深度：超出的内容只做模糊提示，不做详细解答。
- 用户明确要求剧透或声明已通关时，可以放开尺度。
- 不确定某信息是否算剧透时，保守处理。

## 游戏边界
- 只回答当前游戏的提问。用户问其他游戏时礼貌说明。
""".strip()


def build_game_description(game_key: str) -> str:
    """构建游戏显示名称。"""
    return AVAILABLE_GAMES.get(game_key, {}).get("name", game_key)


# ── 切换意图检测 ──

SWITCH_PATTERNS: List[str] = [
    r"换(个|一)?(游戏|话题|别的|其他)",
    r"讲(讲|一下|一哈)?(别的|其他|下一个|新)",
    r"(换个|换个别的|换一个|查别的|看别的)",
    r"(查|看|讲)(别的|其他|下一个)游戏",
    r"其他游戏|别的游戏|下一个游戏",
    r"(不说|不问|不谈|不讲)(这个|这个了)",
    r"有没有.*(别的|其他).*(游戏|攻略)",
    r"(还有|还有什么)?(别的|其他的|其他的游戏|其他游戏).*(推荐|说说|讲讲|介绍|问)",
    r"(不聊|不谈|不说|不讲)(这个|这个了|了)",
    r"算了.*(换|别的|其他)",
]


def is_switch_query(query: str) -> bool:
    """判断用户是否想切换游戏（但不一定指向具体哪个）。"""
    for pattern in SWITCH_PATTERNS:
        if re.search(pattern, query):
            return True
    return False
