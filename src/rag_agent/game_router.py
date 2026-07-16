#!/usr/bin/env python3
"""
Game Router — 游戏识别与工具调度。

识别用户问题指向哪个游戏，并提供对应的工具。
"""

import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain.tools import tool

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GAMES_DIR = PROJECT_ROOT / "games"

AVAILABLE_GAMES = {
    "hollow_knight": {
        "name": "Hollow Knight (空洞骑士)",
        "db_path": str(GAMES_DIR / "hollow_knight" / "hk_data.db"),
        "vectorstore_dir": str(GAMES_DIR / "hollow_knight" / "vectorstore"),
    },
    "oni": {
        "name": "Oxygen Not Included (缺氧)",
        "db_path": str(GAMES_DIR / "oni" / "oni_data.db"),
        "vectorstore_dir": str(GAMES_DIR / "oni" / "vectorstore"),
    },
    "terraria": {
        "name": "Terraria (泰拉瑞亚)",
        "db_path": str(GAMES_DIR / "terraria" / "terraria_data.db"),
        "vectorstore_dir": str(GAMES_DIR / "terraria" / "vectorstore"),
    },
    "silksong": {
        "name": "Hollow Knight Silksong (丝之歌)",
        "db_path": str(GAMES_DIR / "silksong" / "silksong_data.db"),
        "vectorstore_dir": str(GAMES_DIR / "silksong" / "vectorstore"),
    },
    "cyberpunk2077": {
        "name": "Cyberpunk 2077 (赛博朋克2077)",
        "db_path": str(GAMES_DIR / "cyberpunk2077" / "cyberpunk2077_data.db"),
        "vectorstore_dir": str(GAMES_DIR / "cyberpunk2077" / "vectorstore"),
    },
    "va11halla": {
        "name": "VA-11 Hall-A (赛博朋克酒保行动)",
        "db_path": str(GAMES_DIR / "va11halla" / "va11halla_data.db"),
        "vectorstore_dir": str(GAMES_DIR / "va11halla" / "vectorstore"),
    },
    "mhw": {
        "name": "Monster Hunter Wilds (怪物猎人荒野)",
        "db_path": str(GAMES_DIR / "mhw" / "mhw_data.db"),
        "vectorstore_dir": str(GAMES_DIR / "mhw" / "vectorstore"),
    },
}

# ── 游戏关键词检测 ──

GAME_SIGNALS: Dict[str, List[str]] = {
    "hollow_knight": [
        "hollow knight", "空洞骑士", "hallownest", "圣巢",
        "hk",
        "辐光", "radiance", "纯粹容器", "pure vessel",
        "hornet", "大黄蜂", "grimm", "格林",
        "螳螂领主", "deepnest", "pale king", "白王",
        "虚空之心", "灵魂", "梦境", "梦钉",
        "泪水之城", "city of tears", "王国边缘", "王后驿站",
        "遗忘十字路", "十字路", "fungal wastes",
        "骨钉", "复仇之魂", "护符", "perma",
        "苦痛之路", "白宫", "神居", "godhome",
        "空洞骑士", "黑卵", "辐光者",
    ],
    "silksong": [
        "silksong", "丝之歌",
        "丝之鸽",
        "hornet", "黄蜂公主", "pharloom",
        "lace", "编织者",
        "绸缎", "丝线",
    ],
    "oni": [
        "oxygen not included", "缺氧", "oni",
        "复制人", "duplicant", "drecko", "hatch",
        "氧齿蕨", "净水", "石油", "塑料",
        "精炼", "热", "温度", "冷却",
        "管道", "电", "发电", "电池",
        "火箭", "太空", "星图",
    ],
    "terraria": [
        "terraria", "泰拉瑞亚",
        "肉山", "wall of flesh", "月总", "moon lord",
        "克苏鲁", "史莱姆", "泰拉刃", "terra blade",
        "叶绿", "神圣", "血腥", "腐化",
        "恶魔", "向导", "npc",
        "矿车", "钓鱼", "史莱姆女王",
        "日耀", "星旋", "星云",
    ],
    "va11halla": [
        "va-11 hall-a", "赛博朋克酒保", "酒保行动",
        "va11halla", "valhalla", "瓦尔哈拉", "va11",
        "jill", "吉尔", "dana", "戴娜",
        "调制", "调酒", "鸡尾酒", "bartender",
        "坏Touch", "brandtini",
        "安娜", "anime",
    ],
    "mhw": [
        "monster hunter wilds", "怪物猎人荒野", "mh wilds",
        "mhwilds", "mhws", "怪猎荒野",
        "rey dau", "uth duna", "chatacabra", "arkveld",
        "oilwell basin", "windward plains", "ruins of wyveria",
        "煌雷龙", "沼龙", "风铗龙",
        "flying wyvern", "leviathan", "fanged beast",
    ],
    "cyberpunk2077": [
        "cyberpunk 2077", "赛博朋克2077", "赛博朋克 2077",
        "2077", "cp2077",
        "v", "强尼", "johnny silverhand", "银手",
        "夜之城", "night city", "荒坂", "arasaka",
        "义体", "cyberware", "relic", "圣物",
        "超梦", "braindance", "虎爪帮",
        "大卫", "lucy", "边缘行者", "edgerunners",
        "百灵鸟", "songbird", "所罗门", "reed",
        "狗镇", "dogtown", "phantom liberty",
        "军用科技", "militech", "漩", "maelstrom",
        "黑墙", "blackwall", "黑客", "quickhack",
    ],
}


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

    # 优先精确匹配游戏全称
    exact_patterns = {
        "hollow_knight": [r"\b(?:hollow knight|空洞骑士|hk)\b"],
        "cyberpunk2077": [r"\b(?:cyberpunk 2077|赛博朋克2077|赛博朋克 2077|2077|cp2077)\b"],
        "va11halla": [r"\b(?:va-11 hall-a|va11halla|赛博朋克酒保|酒保行动|va11)\b"],
        "terraria": [r"\b(?:terraria|泰拉瑞亚|泰拉)\b"],
        "oni": [r"\b(?:oxygen not included|缺氧|oni)\b"],
        "silksong": [r"\b(?:silksong|丝之歌)\b"],
        "mhw": [r"\b(?:怪物猎人荒野|monster hunter wilds|mh wilds|mhwilds|mhws)\b"],
    }

    for game, patterns in exact_patterns.items():
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
    """根据检测到的游戏构建 system prompt。"""
    game_info = AVAILABLE_GAMES.get(game_key)
    if not game_info:
        return ""

    prompts = {
        "hollow_knight": f"""
你是一个通用游戏助手，辅助玩家查询各种游戏相关剧情数据攻略等资料，你最擅长各种游戏的信息整合等工作。

You have two knowledge sources:
1. **search_knowledge_base** — Vector search (lore, story, strategies, descriptions)
2. **query_structured_data** — SQLite database (numbers: charm cost, boss HP, skill damage, geo drops)
""",
        "oni": f"""
你是一个通用游戏助手，辅助玩家查询各种游戏相关剧情数据攻略等资料，你最擅长各种游戏的信息整合等工作。

You have two knowledge sources:
1. **search_knowledge_base** — Vector search (strategies, builds, mechanics, lore)
2. **query_structured_data** — SQLite database (numbers: building power, calories, resource properties)
""",
        "terraria": f"""
你是一个通用游戏助手，辅助玩家查询各种游戏相关剧情数据攻略等资料，你最擅长各种游戏的信息整合等工作。

You have two knowledge sources:
1. **search_knowledge_base** — Vector search (strategies, crafting, lore, biomes)
2. **query_structured_data** — SQLite database (numbers: boss HP, weapon damage, armor defense, item prices)
""",
        "silksong": f"""
你是一个通用游戏助手，辅助玩家查询各种游戏相关剧情数据攻略等资料，你最擅长各种游戏的信息整合等工作。

You have two knowledge sources:
1. **search_knowledge_base** — Vector search (lore, descriptions, strategies)
2. **query_structured_data** — SQLite database (enemy HP, boss info, items)
""",
        "cyberpunk2077": f"""
你是一个通用游戏助手，辅助玩家查询各种游戏相关剧情数据攻略等资料，你最擅长各种游戏的信息整合等工作。

You have two knowledge sources:
1. **search_knowledge_base** — Vector search (lore, quests, characters, locations, builds)
2. **query_structured_data** — SQLite database (weapons, cyberware, quickhacks, perks stats)

You also cover the Phantom Liberty expansion content.
""",
        "mhw": f"""
你是一个通用游戏助手，辅助玩家查询各种游戏相关剧情数据攻略等资料，你最擅长各种游戏的信息整合等工作。

You have two knowledge sources:
1. **search_knowledge_base** — Vector search (monster info, weapons, armor, skills, locations, quests)
        2. **query_structured_data** — SQLite database (monster stats, weaknesses, elements, species, and weapon/armor data)

Note: You specialize in Monster Hunter Wilds (released Feb 2025). For questions about other Monster Hunter games (World, Rise, etc.), briefly note you're only equipped for Wilds.
""",
        "va11halla": f"""
你是一个通用游戏助手，辅助玩家查询各种游戏相关剧情数据攻略等资料，你最擅长各种游戏的信息整合等工作。

You have two knowledge sources:
1. **search_knowledge_base** — Vector search (characters, story, drink recipes, endings)
2. **query_structured_data** — SQLite database (page info, categories)
""",
    }

    return prompts.get(game_key, "").strip()


def build_common_rules() -> str:
    """构建通用规则（回答规范、剧透管理、游戏边界）。"""
    return """
## 回答规则
- 用中文回答，保留英文专有名词原文并用括号标注中文。
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
