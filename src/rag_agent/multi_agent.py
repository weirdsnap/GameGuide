"""
多游戏 Agent — 支持空洞骑士、缺氧、泰拉瑞亚、丝之歌、赛博朋克2077、赛博朋克酒保行动。

自动检测用户问题指向哪个游戏，加载对应的数据库和向量库，
并路由到合适的工具。

用法：
  from multi_agent import ask
  answer = ask("泰拉瑞亚克苏鲁之眼怎么打？")
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain.tools import tool
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent as create_agent

from rag_agent.config import LLM_CONFIG
from rag_agent.game_router import AVAILABLE_GAMES, GAMES_DIR, build_common_rules, build_game_description, build_game_prompt, detect_game, is_switch_query
from rag_agent.vectorstore import get_retriever, load_vectorstore

logger = logging.getLogger(__name__)

# ── 游戏切换状态 ──
_LAST_GAME: Optional[str] = None
_LAST_GAME_CONFIRMED: bool = False


def _reset_game_state():
    """重置游戏状态（新对话时）。"""
    global _LAST_GAME, _LAST_GAME_CONFIRMED
    _LAST_GAME = None
    _LAST_GAME_CONFIRMED = False


def _resolve_game_key(name: str) -> Optional[str]:
    """将游戏显示名或内部键解析为内部键。"""
    clean = name.lower().strip().replace(' ', '_')
    if clean in AVAILABLE_GAMES:
        return clean
    for key, cfg in AVAILABLE_GAMES.items():
        if clean in cfg['name'].lower().replace(' ', '_'):
            return key
    return None


# ══════════════════════════════════════════
#  游戏数据库工具
# ══════════════════════════════════════════

def _get_db(path: str):
    """懒加载 SQLite 连接。"""
    import sqlite3
    db_path = Path(path)
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _query_db(db_path: str, sql: str, params: tuple = ()) -> List[Dict]:
    """通用的 SQLite 查询。"""
    db = _get_db(db_path)
    if not db:
        return []
    try:
        cur = db.cursor()
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        db.close()
        return rows
    except Exception as e:
        logger.warning(f"DB query error: {e}")
        return []


def _format_db_result(rows: List[Dict], table: str) -> str:
    """格式化查询结果为可读文本（与表无关的通用格式）。"""
    if not rows:
        return "（数据库未找到相关内容）"

    lines = []
    for i, r in enumerate(rows, 1):
        parts = []
        for key, val in r.items():
            if val is not None and val != "":
                label = key.replace("_", " ").title()
                parts.append(f"{label}: {val}")
        if parts:
            lines.append(f"{i}. " + " | ".join(parts[:6]))
    return "\n".join(lines[:30])  # cap at 30 rows


def _search_all_tables(db_path: str, keyword: str) -> str:
    """在所有表中搜索关键词。"""
    db = _get_db(db_path)
    if not db:
        return "(数据库不可用)"
    cur = db.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r['name'] for r in cur.fetchall()]

    results = []
    for table in tables:
        # Try name column first (common across all structured tables)
        try:
            sql = f"SELECT * FROM [{table}] WHERE name LIKE ? LIMIT 5"
            cur.execute(sql, (f"%{keyword}%",))
            for r in cur.fetchall():
                results.append(f"  [{table}] {r['name']}")
        except Exception:
            # Some tables might not have a 'name' column; try title
            try:
                sql = f"SELECT * FROM [{table}] WHERE title LIKE ? LIMIT 5"
                cur.execute(sql, (f"%{keyword}%",))
                for r in cur.fetchall():
                    results.append(f"  [{table}] {r.get('title', r.get('name', '?'))}")
            except Exception:
                pass

    db.close()
    if results:
        return "数据库中找到以下相关条目：\n" + "\n".join(results[:15]) + \
               "\n\n💡 试试更具体的查询，如「查询 Jackie」或「列出所有武器」"
    return "(数据库中未找到相关内容)"


def _fmt_table_names(db_path: str) -> str:
    """获取数据库的表名和行数列（用于提示）。"""
    db = _get_db(db_path)
    if not db:
        return ""
    cur = db.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('sqlite_sequence', 'game_meta')")
    tables = [r['name'] for r in cur.fetchall()]
    info = []
    for t in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM [{t}]")
            cnt = cur.fetchone()[0]
            info.append(f"{t}({cnt}条)")
        except:
            info.append(t)
    db.close()
    return ", ".join(info)


# ══════════════════════════════════════════
#  通用游戏工具（接受 game 参数路由到对应数据）
# ══════════════════════════════════════════


def _tool_game_cfg(game: str) -> tuple:
    """解析 game 参数并返回 (game_key, cfg) 元组。"""
    resolved = _resolve_game_key(game)
    if not resolved:
        valid = ", ".join(AVAILABLE_GAMES.keys())
        raise ValueError(f"无效游戏「{game}」，可用选项：{valid}")
    cfg = AVAILABLE_GAMES[resolved]
    return resolved, cfg


@tool
def search_knowledge_base(query: str, game: str, k: int = 8) -> str:
    """使用向量检索搜索游戏的维基知识库。

    适合查询：剧情背景、区域/地点描述、Boss 打法、游戏机制、
    NPC 对话、合成配方等描述性内容。

    在每个游戏中调用时，使用 game 参数指定目标游戏。

    Args:
        query: 搜索关键词，使用英文关键词效果更佳
        game: 目标游戏键名（如 hollow_knight、oni、terraria、silksong、cyberpunk2077、va11halla、mhw）
        k: 返回的相关结果数量（默认 8）
    """
    try:
        _game_key, cfg = _tool_game_cfg(game)
    except ValueError as e:
        return f"[参数错误] {e}"

    vs_dir = cfg["vectorstore_dir"]
    game_name = cfg["name"]
    vs_ok = os.path.isdir(vs_dir) and os.path.isfile(os.path.join(vs_dir, 'index.faiss'))

    if not vs_ok:
        return (f"[知识库暂时不可用] {game_name} 的向量库尚未构建。\n"
                f"请在 Mac 本地运行 `python3 scripts/run_on_mac.py --game {_game_key}` 来构建。")
    try:
        vs = load_vectorstore(save_dir=vs_dir)

        # 分语言检索，保证中文和英文文档都能命中
        # fetch_k 设为 200 确保过滤后的候选数充足
        en_docs = vs.similarity_search(query, k=k, filter={"language": "en"}, fetch_k=200)
        zh_docs = vs.similarity_search(query, k=k, filter={"language": "zh"}, fetch_k=200)

        # 合并，去重（按 page_content 去重）
        seen = set()
        docs = []
        for doc in en_docs + zh_docs:
            sig = doc.page_content[:100]
            if sig not in seen:
                seen.add(sig)
                docs.append(doc)

        if not docs:
            return f"(知识库未找到关于「{query}」的内容)"
        parts = []
        for i, doc in enumerate(docs, 1):
            content = doc.page_content.strip()
            meta = doc.metadata or {}
            source = meta.get("source", meta.get("title", ""))
            lang_tag = f"[{meta.get('language', '?').upper()}] " if meta.get("language") else ""
            parts.append(f"【参考 {i}】{lang_tag}{source}\n{content[:500]}")
        return "\n\n".join(parts)
    except Exception as e:
        return f"[知识库检索出错] {e}"


@tool
def query_structured_data(query: str, game: str) -> str:
    """查询游戏的结构化数据库获取精确数据。

    适合查询：Boss/角色属性、物品价格、伤害值、费用等可量化的数值数据。

    输入格式：请使用自然语言描述，例如：
    - "查询 X" — 查询特定物品/Boss/敌人的详情
    - "所有敌人" 或 "所有武器" — 列出某个分类的全部条目
    - "HP>500" 或 "cost 3" — 根据属性/数值筛选

    在每个游戏中调用时，使用 game 参数指定目标游戏。

    Args:
        query: 自然语言查询描述
        game: 目标游戏键名（如 hollow_knight、oni、terraria、silksong、cyberpunk2077、va11halla、mhw）
    """
    try:
        _game_key, cfg = _tool_game_cfg(game)
    except ValueError as e:
        return f"[参数错误] {e}"

    db_path = cfg["db_path"]
    game_name = cfg["name"]

    try:
        q = query.lower().strip()
        tbl_list = _fmt_table_names(db_path)

        # Try entity query: "查询 X Y" / "查 X"
        for prefix in ["查询", "查", "搜索", "搜"]:
            if q.startswith(prefix) and len(q) > 3:
                keyword = q[len(prefix):].strip()
                if keyword:
                    return _search_all_tables(db_path, keyword)

        # Try "所有 X" / "全部 X" / "列出所有 X"
        for cmd in ["所有", "全部", "列出所有", "列出全部"]:
            if cmd in q:
                keyword = q.split(cmd)[-1].strip()
                if keyword:
                    # Find matching table
                    for table_match in keyword.split():
                        rows = _query_db(db_path, f"SELECT * FROM [{table_match}] LIMIT 30")
                        if rows:
                            return _format_db_result(rows, table_match)
                    # Try fuzzy table name match
                    db2 = _get_db(db_path)
                    if db2:
                        cur = db2.cursor()
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        tables = [r['name'] for r in cur.fetchall()]
                        db2.close()
                        for table in tables:
                            if any(k in table for k in keyword.split()):
                                rows = _query_db(db_path, f"SELECT * FROM [{table}] LIMIT 30")
                                if rows:
                                    return _format_db_result(rows, table)
                    # Try table name aliases (跨游戏通用表别名)
                    table_aliases = {
                        "boss": ["bosses", "monsters", "monster"],
                        "bosses": ["bosses", "monsters"],
                        "敌人": ["enemies", "monsters", "monster"],
                        "enemy": ["enemies", "monsters"],
                        "enemies": ["enemies", "monsters"],
                        "怪物": ["monsters", "monster", "enemies"],
                        "monster": ["monsters"],
                        "技能": ["skills", "skill"],
                        "skill": ["skills"],
                        "武器": ["weapons", "weapon"],
                        "weapon": ["weapons"],
                        "防具": ["armor", "armors"],
                        "armour": ["armor"],
                        "物品": ["items", "item"],
                        "item": ["items"],
                        "道具": ["items"],
                        "区域": ["areas", "locations", "location"],
                        "地区": ["areas", "locations"],
                        "area": ["areas"],
                        "location": ["locations"],
                        "角色": ["characters", "character"],
                        "character": ["characters", "character"],
                        "NPC": ["characters", "character"],
                    }
                    for kw in keyword.split():
                        aliases = table_aliases.get(kw.lower(), []) + table_aliases.get(kw, [])
                        if not aliases:
                            aliases = table_aliases.get(kw, [])
                        for alias in aliases:
                            for table in tables:
                                if alias == table:
                                    rows = _query_db(db_path, f"SELECT * FROM [{table}] LIMIT 30")
                                    if rows:
                                        return _format_db_result(rows, table)

                    # If no table match, search all tables
                    return _search_all_tables(db_path, keyword)

        # ── 通用数值筛选 ──
        # 自动发现所有表中的数值列，然后逐个尝试匹配
        db3 = _get_db(db_path)
        if db3:
            cur = db3.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('sqlite_sequence','game_meta')")
            tables = [r['name'] for r in cur.fetchall()]
            db3.close()

            # 提取查询中的数值
            num_match = __import__('re').search(r"(\d+)", q)
            val = num_match.group(1) if num_match else None

            if val:
                # 判断查询是 cost/价格筛选还是 HP/血量筛选
                is_cost_query = bool(__import__('re').search(r"(?:cost|价格|价|费用|等级|级|格|槽|品质)", q))
                is_hp_query = bool(__import__('re').search(r"(?:HP|hp|血量|生命|health|强度|战力)", q))

                # 收集所有表的所有数值列名
                num_cols = ['hp', 'health', 'damage', 'cost', 'buy_price', 'sell_price',
                            'power', 'defense', 'capacity', 'ram_cost', 'reward_eb',
                            'reward_xp', 'buy_price', 'top_speed', 'horse_power',
                            'ammo_capacity', 'armor_penetration', 'weight',
                            'attack_speed', 'upload_time', 'effective_range']

                if is_hp_query:
                    priority_cols = ['hp', 'health', 'reward_xp', 'ram_cost']
                elif is_cost_query:
                    priority_cols = ['cost', 'buy_price', 'sell_price', 'reward_eb']
                else:
                    priority_cols = ['cost', 'hp', 'health', 'damage']

                results = []
                for table in tables:
                    for col in priority_cols:
                        try:
                            rows = _query_db(db_path, f"SELECT name, {col} FROM [{table}] WHERE {col} IS NOT NULL AND CAST({col} AS REAL) >= CAST(? AS REAL) LIMIT 5", (val,))
                            if rows:
                                results.append(f"  [{table}] " + ", ".join(f"{r.get('name','?')} ({col}={r.get(col,'?')})" for r in rows))
                                break
                        except Exception:
                            pass

                # 如果没找到，尝试所有可能数值列
                if not results:
                    for table in tables:
                        for col in num_cols:
                            try:
                                rows = _query_db(db_path, f"SELECT name, {col} FROM [{table}] WHERE {col} IS NOT NULL AND CAST({col} AS REAL) >= CAST(? AS REAL) LIMIT 5", (val,))
                                if rows:
                                    results.append(f"  [{table}] " + ", ".join(f"{r.get('name','?')} ({col}={r.get(col,'?')})" for r in rows))
                                    break
                            except Exception:
                                pass

                if results:
                    return f"筛选 ≥{val} 的结果：\n" + "\n".join(results[:20])
                return f"（数据库中没有匹配 ≥{val} 的数值条目）"

        # Fallback: search all tables
        return _search_all_tables(db_path, q) + f"\n\n💡 可用的表: {tbl_list}"

    except Exception as e:
        return f"[结构化查询出错] {e}"


@tool
def show_database_schema(game: str) -> str:
    """查看游戏数据库的所有表结构、列名和行数。

    当你不确定目标游戏的数据库包含哪些表时使用此工具。
    它会列出每一张表的列名/类型和行数，方便你构造精确的 query_structured_data 调用。

    Args:
        game: 目标游戏键名（如 hollow_knight、oni、terraria、silksong、cyberpunk2077、va11halla、mhw）
    """
    try:
        _game_key, cfg = _tool_game_cfg(game)
    except ValueError as e:
        return f"[参数错误] {e}"

    db_path = cfg["db_path"]
    game_name = cfg["name"]

    import sqlite3
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        cur = db.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('sqlite_sequence', 'game_meta')")
        tables = [r['name'] for r in cur.fetchall()]

        result_parts = []
        for table in tables:
            cur.execute(f"SELECT COUNT(*) FROM [{table}]")
            count = cur.fetchone()[0]

            cur.execute(f"PRAGMA table_info([{table}])")
            columns = cur.fetchall()
            col_desc = ", ".join(f"{c['name']} ({c['type']})" for c in columns)
            result_parts.append(f"📋 {table} ({count} rows)\n   Columns: {col_desc}")

        db.close()

        if not result_parts:
            return f"[{game_name}] 数据库中没有数据表。"

        return f"**{game_name}** 数据库结构：\n\n" + "\n\n".join(result_parts)
    except Exception as e:
        return f"[获取数据库结构出错] {e}"


# 全局共享的工具列表（所有游戏共用同一套工具）
GAME_TOOLS = [search_knowledge_base, query_structured_data, show_database_schema]





# ══════════════════════════════════════════
#  共享：游戏检测 + 准备
# ══════════════════════════════════════════

_MENU_NEW = """请问你想问哪款游戏的攻略？请选择：

1. 🐈 **空洞骑士** (Hollow Knight)
2. 🪱 **丝之歌** (Hollow Knight Silksong)
3. 💨 **缺氧** (Oxygen Not Included)
4. 🪨 **泰拉瑞亚** (Terraria)
5. 🐉 **怪物猎人荒野** (Monster Hunter Wilds)
6. 🤖 **赛博朋克2077** (Cyberpunk 2077)
7. 🍸 **赛博朋克酒保行动** (VA-11 Hall-A)

直接告诉我游戏名称就可以开始啦！"""

_MENU_SWITCH = """你想切换到哪个游戏？请选择：

1. 🐈 **空洞骑士** (Hollow Knight)
2. 🪱 **丝之歌** (Hollow Knight Silksong)
3. 💨 **缺氧** (Oxygen Not Included)
4. 🪨 **泰拉瑞亚** (Terraria)
5. 🐉 **怪物猎人荒野** (Monster Hunter Wilds)
6. 🤖 **赛博朋克2077** (Cyberpunk 2077)
7. 🍸 **赛博朋克酒保行动** (VA-11 Hall-A)

直接告诉我游戏名称就可以啦！"""


# ── 外部游戏列表（不在我们的知识库中，由 LLM 自身知识回答）──
_KNOWN_EXTERNAL_GAMES = [
    "原神", "genshin", "星穹铁道", "崩坏", "honkai",
    "星露谷", "stardew valley", "我的世界", "minecraft",
    "艾尔登法环", "elden ring", "黑魂", "dark souls", "只狼", "sekiro",
    "巫师", "witcher", "gta", "荒野大镖客", "red dead",
    "博德之门", "baldur", "最终幻想", "final fantasy",
    "塞尔达", "zelda", "宝可梦", "pokemon",
    "怪物猎人", "monster hunter", "文明", "civilization",
    "英雄联盟", "league of legends", "lol", "dota",
    "战神", "god of war", "神秘海域", "uncharted",
    "古墓丽影", "tomb raider", "生化危机", "resident evil",
    "死亡搁浅", "死亡搁浅", "death stranding",
    "上古卷轴", "skyrim", "辐射", "fallout",
    "双人成行", "it takes two", "胡闹厨房", "overcooked",
]


def _is_unknown_game_query(q: str) -> bool:
    """检查是否在问一个不在我们知识库里的游戏。

    先排除已知游戏的关键词命中（避免误判），然后检查
    是否提到外部游戏名或游戏相关术语。
    """
    ql = q.lower().strip()

    # 排除已知游戏（避免误判）
    known_keywords = ["空洞", "丝之歌", "silksong", "缺氧", "oni",
                      "泰拉瑞亚", "terraria", "赛博朋克", "cyberpunk",
                      "酒保", "va11", "hall-a", "va-11",
                      "怪物猎人荒野", "monster hunter wilds", "mh wilds"]
    for kw in known_keywords:
        if kw in ql:
            return False

    # 检查外部游戏名
    for game in _KNOWN_EXTERNAL_GAMES:
        if game in ql:
            return True

    # 检查泛游戏用语
    game_terms = ["攻略", "boss", "怎么打", "如何获得", "在哪里",
                  "装备", "技能", "职业", "等级", "通关"]
    for term in game_terms:
        if term in ql:
            return True

    return False


def _resolve_game(question: str, history: Optional[list] = None):
    """
    游戏检测 + 连续性判断。

    Returns:
        (game_key, full_prompt, vs_ok, db_ok, cfg, switched)
        switched=True 表示检测到游戏切换
    """
    global _LAST_GAME, _LAST_GAME_CONFIRMED

    q = question.strip()
    if not q:
        return None, "请问你想了解哪款游戏的攻略？", None, None, None, False

    # 检测游戏
    game_key, confidence = detect_game(q)
    logger.info(f"检测游戏: key={game_key}, confidence={confidence:.2f}, query={q[:50]}")

    # 检测游戏切换（在更新 _LAST_GAME 之前检查）
    switched = False

    # 连续性判断
    if confidence >= 0.4:
        if _LAST_GAME is not None and _LAST_GAME_CONFIRMED and game_key != _LAST_GAME:
            switched = True
            logger.info(f"检测到游戏切换: {_LAST_GAME} → {game_key}")
        _LAST_GAME = game_key
        _LAST_GAME_CONFIRMED = True
        logger.info(f"检测到游戏: {game_key}")
    elif is_switch_query(q):
        logger.info(f"检测到切换意图: {q[:50]}")
        _reset_game_state()
        return None, _MENU_SWITCH, None, None, None, False
    elif history and _LAST_GAME is not None and _LAST_GAME_CONFIRMED:
        game_key = _LAST_GAME
        logger.info(f"延续上轮游戏: {game_key}")
    else:
        # 未匹配已知游戏 → 检查是否仍是游戏相关提问
        if _is_unknown_game_query(q):
            _reset_game_state()
            fallback_prompt = (
                "用户问了一个游戏问题，但这个游戏不在你的知识库中（支持的游戏："
                "空洞骑士、丝之歌、缺氧、泰拉瑞亚、怪物猎人荒野、赛博朋克2077、赛博朋克酒保行动）。\n\n"
                "请按以下原则回答：\n"
                "1. 先告知用户「这个游戏不在我的专业知识库中，以下信息基于我的训练数据，"
                "可能不完全准确」。\n"
                "2. 然后尽力回答用户的问题。\n"
                "3. 如果确实不知道答案，诚实承认不知道。\n"
                "4. 使用中文回答。"
            )
            return "__llm_fallback__", fallback_prompt, None, None, None, False
        _reset_game_state()
        return None, _MENU_NEW, None, None, None, False

    # 检查知识库
    cfg = AVAILABLE_GAMES[game_key]
    vs_ok = os.path.isdir(cfg["vectorstore_dir"])
    db_ok = os.path.isfile(cfg["db_path"])

    if not vs_ok and not db_ok:
        _reset_game_state()
        return None, f"抱歉，{cfg['name']} 的知识库尚未准备好。请联系管理员初始化数据。", None, None, None, False

    # 构建 prompt（身份 + 通用规则）
    full_prompt = build_game_prompt(game_key) + "\n\n" + build_common_rules()

    if switched:
        full_prompt += f"\n\n**系统提示：** 用户切换了话题开始聊{cfg['name']}，除非用户主动提及和之前游戏的对比，否则上述的历史请忽略。"

    return game_key, full_prompt, vs_ok, db_ok, cfg, switched


# ══════════════════════════════════════════
#  构建消息
# ══════════════════════════════════════════

def build_messages(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    game_prompt: str = "",
) -> List[BaseMessage]:
    """构建消息列表。"""
    messages: List[BaseMessage] = []

    if history:
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

    if game_prompt and (not history or not any(isinstance(m, SystemMessage) for m in messages)):
        messages.insert(0, SystemMessage(content=game_prompt))

    messages.append(HumanMessage(content=question))
    return messages


# ══════════════════════════════════════════
#  ask() — 非流式（兼容旧接口）
# ══════════════════════════════════════════

def ask(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    model_name: Optional[str] = None,
    verbose: bool = False,
) -> str:
    """多游戏 Agent 入口（非流式）。

    自动检测游戏、加载对应工具、路由问题。

    Args:
        question: 用户问题
        history: 可选历史消息 [{"role": "user"/"assistant", "content": "..."}]
        model_name: 可选模型覆盖
        verbose: 打印详细信息

    Returns:
        Agent 回复文本
    """
    game_key, prompt, vs_ok, db_ok, cfg, switched = _resolve_game(question, history)

    # 需要弹菜单
    if game_key is None:
        return prompt

    # LLM 兜底：不在知识库的游戏，用 LLM 自身知识回答
    if game_key == "__llm_fallback__":
        config = dict(LLM_CONFIG)
        if model_name:
            config["model"] = model_name
        llm = ChatOpenAI(**config)
        messages = build_messages(question.strip(), history, prompt)
        if verbose:
            logger.info(f"🕹️ LLM兜底（不在知识库中）")
            logger.info(f"  🤖 模型: {config.get('model', 'default')}")
        try:
            response = llm.invoke(messages)
            return response.content if response.content else "（无回复）"
        except Exception as e:
            logger.error(f"LLM兜底调用失败: {e}")
            return f"[查询出错] {e}"

    # 创建 LLM
    config = dict(LLM_CONFIG)
    if model_name:
        config["model"] = model_name
    llm = ChatOpenAI(**config)

    # Agent（使用全局工具，game 参数由 LLM 根据 system prompt 中的游戏名传递）
    agent = create_agent(llm, GAME_TOOLS, prompt=SystemMessage(content=prompt))

    # 构建消息
    messages = build_messages(question.strip(), history, prompt)

    if verbose:
        logger.info(f"🕹️ 游戏: {game_key}")
        logger.info(f"  🤖 模型: {config.get('model', 'default')}")
        logger.info(f"  📊 向量库: {'✅' if vs_ok else '❌'} | 数据库: {'✅' if db_ok else '❌'}")
        logger.info(f"  💬 消息: {len(messages)} 条")

    try:
        result = agent.invoke({"messages": messages}, {"recursion_limit": 50})
        answer = result.get("messages", [])[-1].content if result.get("messages") else ""
        return answer or "（Agent 没有返回有效回答）"
    except Exception as e:
        logger.error(f"Agent 调用失败: {e}")
        return f"[查询出错] {e}"


# ══════════════════════════════════════════
#  ask_stream() — 流式输出
# ══════════════════════════════════════════

from langchain_core.messages import AIMessageChunk


async def ask_stream(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    model_name: Optional[str] = None,
    verbose: bool = False,
):
    """
    流式 Agent 入口。多游戏检测+路由，逐 token 产出。

    Yields:
        ("token", str)  — LLM 生成文本片段
        ("error", str)  — 出错信息
        ("meta", dict)  — 元信息（游戏、模型等）
    """
    game_key, prompt, vs_ok, db_ok, cfg, switched = _resolve_game(question, history)

    # 需要弹菜单或错误 → 当 token 一次性吐出
    if game_key is None:
        yield "token", prompt
        return

    # LLM 兜底：不在知识库的游戏，用 LLM 自身知识回答
    if game_key == "__llm_fallback__":
        config = dict(LLM_CONFIG)
        if model_name:
            config["model"] = model_name
        config["streaming"] = True
        llm = ChatOpenAI(**config)
        messages = build_messages(question.strip(), history, prompt)
        if verbose:
            logger.info(f"🕹️ LLM兜底（不在知识库中）")
            logger.info(f"  🤖 模型: {config.get('model', 'default')}")
        try:
            async for chunk in llm.astream(messages):
                if chunk.content:
                    yield "token", chunk.content
        except Exception as e:
            logger.error(f"LLM兜底流式调用失败: {e}")
            yield "error", str(e)
        return

    # 创建 LLM（streaming 开启）
    config = dict(LLM_CONFIG)
    if model_name:
        config["model"] = model_name
    config["streaming"] = True
    llm = ChatOpenAI(**config)

    # Agent（使用全局工具，game 参数由 LLM 根据 system prompt 中的游戏名传递）
    agent = create_agent(llm, GAME_TOOLS, prompt=SystemMessage(content=prompt))

    # 构建消息
    messages = build_messages(question.strip(), history, prompt)

    if verbose:
        logger.info(f"🕹️ 流式 游戏: {game_key}")
        logger.info(f"  🤖 模型: {config.get('model', 'default')}")
        logger.info(f"  📊 向量库: {'✅' if vs_ok else '❌'} | 数据库: {'✅' if db_ok else '❌'}")
        logger.info(f"  💬 消息: {len(messages)} 条")

    # 先发元信息，方便前端显示
    yield "meta", {
        "game": game_key,
        "game_name": cfg["name"],
        "model": config.get("model", "default"),
        "sources": {"vectorstore": vs_ok, "database": db_ok},
    }

    try:
        async for event in agent.astream_events({"messages": messages}, {"recursion_limit": 50}, version="v2"):
            if event["event"] == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if isinstance(chunk, AIMessageChunk) and chunk.content:
                    yield "token", chunk.content
    except Exception as e:
        logger.error(f"Agent 流式调用失败: {e}")
        yield "error", str(e)
