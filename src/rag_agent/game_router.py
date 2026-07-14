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
}

# ── 游戏关键词检测 ──

GAME_SIGNALS: Dict[str, List[str]] = {
    "hollow_knight": [
        "hollow knight", "空洞骑士", "hallownest", "圣巢",
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
}


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
        "hollow_knight": [r"\b(?:hollow knight|空洞骑士)\b"],
        "terraria": [r"\b(?:terraria|泰拉瑞亚|泰拉)\b"],
        "oni": [r"\b(?:oxygen not included|缺氧)\b"],
        "silksong": [r"\b(?:silksong|丝之歌)\b"],
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
            if signal.lower() in q:
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
You are a Hollow Knight (《空洞骑士》) game expert assistant named nanobot 🐈.

You have two knowledge sources:
1. **search_knowledge_base** — Vector search (lore, story, strategies, descriptions)
2. **query_structured_data** — SQLite database (numbers: charm cost, boss HP, skill damage, geo drops)

Answer in Chinese (中文) keeping English game terms in parentheses.
Always cite which source provided the info.
Be concise, informative, max 3-4 paragraphs.
For questions about OTHER games: politely decline.
""",
        "oni": f"""
You are an Oxygen Not Included (《缺氧》) game expert assistant named nanobot 🐈.

You have two knowledge sources:
1. **search_knowledge_base** — Vector search (strategies, builds, mechanics, lore)
2. **query_structured_data** — SQLite database (numbers: building power, calories, resource properties)

Answer in Chinese (中文) keeping English terms in parentheses.
Always cite which source provided the info.
Be concise, informative, max 3-4 paragraphs.
For questions about OTHER games: politely decline.
""",
        "terraria": f"""
You are a Terraria (《泰拉瑞亚》) game expert assistant named nanobot 🐈.

You have two knowledge sources:
1. **search_knowledge_base** — Vector search (strategies, crafting, lore, biomes)
2. **query_structured_data** — SQLite database (numbers: boss HP, weapon damage, armor defense, item prices)

Answer in Chinese (中文) keeping English game terms in parentheses.
Always cite which source provided the info.
Be concise, informative, max 3-4 paragraphs.
For questions about OTHER games: politely decline.
""",
        "silksong": f"""
You are a Hollow Knight Silksong (《丝之歌》) game expert assistant named nanobot 🐈.

You have two knowledge sources:
1. **search_knowledge_base** — Vector search (lore, descriptions, strategies)
2. **query_structured_data** — SQLite database (enemy HP, boss info, items)

Answer in Chinese (中文) keeping English game terms in parentheses.
Note: Silksong has NOT been released yet. Information comes from demos, previews, and wiki speculation.
Always cite which source provided the info.
For questions about OTHER games: politely decline.
""",
    }

    return prompts.get(game_key, "").strip()


def build_game_description(game_key: str) -> str:
    """构建游戏显示名称。"""
    return AVAILABLE_GAMES.get(game_key, {}).get("name", game_key)
