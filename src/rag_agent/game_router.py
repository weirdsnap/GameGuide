#!/usr/bin/env python3
"""
Game Router — 游戏识别与工具调度。

识别用户问题指向哪个游戏，并提供对应的工具。
"""

import os
import re
from typing import Dict, List, Optional, Tuple

from langchain.tools import tool

from rag_agent.game_registry import (
    AVAILABLE_GAMES, GAME_SIGNALS, GAME_EXACT_PATTERNS,
    EXTRA_PROMPT_NOTES, GENERIC_SIGNALS,
)

# ── 路由阈值（与 multi_agent._game_from_history 共用，别各写一份）──────────
# DETECT_TH：新建会话靠它定游戏；通用词封顶 0.35，刚好能过。
# SWITCH_TH：会话中段从 A 切到 B，必须同时有专有命中（见 graph.detect_game_node）。
DETECT_TH = 0.30
SWITCH_TH = 0.60
# 旧名兼容（外部调用方/测试还在 import）；新代码请用 DETECT_TH/SWITCH_TH。
GAME_CONFIDENCE_TH = DETECT_TH

# 信号权重：专有词是硬证据，跨游戏通用词只做弱证据。
W_EXCLUSIVE = 1.0
W_GENERIC = 0.25
# 只有通用词时 confidence 的上限：能过 DETECT_TH，但过不了 SWITCH_TH。
GEN_ONLY_CAP = 0.35



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


def detect_game_detail(query: str) -> Tuple[Optional[str], float, bool]:
    """检测游戏并返回 (game_key, confidence, exclusive_hit)。

    exclusive_hit 表示最佳候选命中了**专有词**（含 exact_patterns）。
    会话中段切换只允许 exclusive_hit=True 且 confidence >= SWITCH_TH 的情况，
    通用词（GENERIC_SIGNALS）不能单独把用户从已确认的游戏里拽走。

    规则与算例见 docs/P0_ROUTING_GATE_PLAN.md §4.2。
    """
    if not query or not query.strip():
        return None, 0, False

    q = query.lower().strip()

    # 1) 精确模式（游戏名/整句简写）是硬证据，直接短路
    for game, patterns in GAME_EXACT_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, q):
                return game, 1.0, True

    # 2) 信号词加权：专有 1.0，跨游戏通用 0.25
    scores: Dict[str, float] = {}
    exclusive_hits: Dict[str, bool] = {}
    for game, signals in GAME_SIGNALS.items():
        score = 0.0
        has_exclusive = False
        for signal in signals:
            if not _match_signal(signal, q):
                continue
            if signal in GENERIC_SIGNALS:
                score += W_GENERIC
            else:
                score += W_EXCLUSIVE
                has_exclusive = True
        if score > 0:
            scores[game] = score
            exclusive_hits[game] = has_exclusive

    if not scores:
        return None, 0, False

    total = sum(scores.values())
    best_game = max(scores, key=scores.get)
    best_score = scores[best_game]
    confidence = best_score / total

    # 打平（通常只发生在两边都只有通用词时）沿用旧的折半规则
    sorted_scores = sorted(scores.values(), reverse=True)
    if len(sorted_scores) > 1 and sorted_scores[0] == sorted_scores[1]:
        confidence *= 0.5

    exclusive = exclusive_hits.get(best_game, False)

    # 全靠通用词时：
    #   - 两个游戏互相打架 → 结论是「不知道」，不许挑一个；
    #   - 只有一个候选 → 允许定游戏（新会话「这个护符怎么拿」），但封顶 0.35。
    if not exclusive:
        if len(scores) > 1:
            return None, confidence, False
        confidence = min(confidence, GEN_ONLY_CAP)

    if confidence < DETECT_TH:
        return None, confidence, False

    return best_game, min(confidence, 1.0), exclusive


def detect_game(query: str) -> Tuple[Optional[str], float]:
    """兼容旧签名：返回 (game_key, confidence)。

    需要区分「专有命中/通用词」时用 `detect_game_detail`。
    """
    game, confidence, _exclusive = detect_game_detail(query)
    return game, confidence


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


def build_common_rules(game_key: Optional[str] = None) -> str:
    """构建通用规则（回答规范、剧透管理、游戏边界）。

    剧透上界的语义按游戏注册表的 `spoiler_axis` 标注决定，不再靠举例：
      act     — 按幕/章节区域推进的游戏（BG3、丝之歌），上界按 [act N]
      chapter — 按天/章节推进的叙事游戏（CP2077、VA-11 Hall-A）
      ability — 按能力/Boss 推进的探索游戏（空洞骑士、泰拉瑞亚）
      none    — 进度不敏感的游戏（缺氧、MHW）
    所有轴统一「不追问进度」（方案 C），用户报过才套上界。进度
    """
    axis_prompt = ""
    if game_key:
        axis = AVAILABLE_GAMES.get(game_key, {}).get("spoiler_axis")
        # I-17：统一口径是「不追问进度」（追问会反复打断用户，SPOILER_CONTROL_PLAN
        # 方案 C 已定）——旧版三个轴都写「先追问玩到第几章」，与
        # progress_prompt_note 的「不要替用户假设章节」在 prompt 里打架。
        if axis == "act":
            axis_prompt = ("- 用户报过进度时，进度是硬上界；未报进度时不追问，"
                           "按条目自身的 [act N] 标注作答。\n"
                           # I-10：两轮点踩实证——模型会把 act3 藏品改写到用户所在的第二章
                           "- 检索条目的章节归属以条目自身为准（工具返回里的 [act N] 标注），"
                           "不得把条目所属章节改写为用户当前进度所在的章节。\n"
                           "- 条目章节高于用户当前进度时，只说明「存在 + 获取前置条件」，"
                           "不写具体地点、不给任务链。\n"
                           "- 字段缺失（如没有位置信息）时直接说「库里没有」，"
                           "不留「可能在某处」这种半句。")
        elif axis == "chapter":
            axis_prompt = ("- 用户报过进度（第几天/第几章）时，进度是硬上界；"
                           "未报进度时不追问，按问题所指作答。")
        elif axis == "ability":
            axis_prompt = ("- 用户报过进度（已获得的能力/已击败的 Boss）时，"
                           "进度是硬上界；未报进度时不追问，按问题所指作答。")
        # axis == "none"：进度不敏感，不追问

    return f"""
## 回答规则
- 用中文回答。游戏中的道具、技能、地点、角色等专有名词使用「中文（英文）」格式
  展示（如「亡者之怒（Fury of the Fallen）」），而非英文在前。
- 回答时注明信息来源（知识库或数据库），必要时同时使用两个工具。
- 简洁明了，不超过 3-4 段。绝不编造信息，不确定时说"我不确定"。
- 如果某个工具不可用，降级为仅使用可用来源，如实告知用户。
- **如果一个工具的查询没有返回有效结果，必须换另一个工具再试一次。** 例如：`query_structured_data` 返回空结果时，用 `search_knowledge_base` 再查一次，反之亦然。两个工具都无结果时，再基于自身知识回答并说明"这部分是通用知识，可能存在版本差异"。
- **断言性结论（位置、数值、任务归属）落笔前，结构化库与向量库至少各查一次**；
  只有一路命中时，在答案里标明（如「仅结构化库命中」）。
- **不允许「先给结论、下轮再更正」**：一次答完，缺什么当场说缺什么。

## 剧透管理
- 默认不主动透露关键剧情节点、后期 Boss、隐藏结局等剧透内容。
{axis_prompt}
- **进度即硬上界**：用户在对话中报过进度后，后续回答不得越过该进度，
  也不要每轮重复追问进度。必须提及上界之后的内容时，只给占位提示
  （「还有后续，等你到那儿再说」）——不给名字、不给结果、不做正向描述。
- 用户明确说「别剧透/别讲后面」时，作答前先复述边界
  （如「你现在在最后之光旅店，我只讲这一带」），再作答。
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
