"""
游戏工具 — 向量检索 + 结构化数据库查询。

从 multi_agent.py 搬入，供 graph.py 的 agent 节点使用。
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from langchain.tools import tool

from rag_agent.game_router import AVAILABLE_GAMES
from rag_agent.vectorstore import load_vectorstore

logger = logging.getLogger(__name__)


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
    """在所有表中搜索关键词，返回命中条目的关键字段（供 Agent 直接引用）。"""
    db = _get_db(db_path)
    if not db:
        return "(数据库不可用)"
    cur = db.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r['name'] for r in cur.fetchall()]

    INFO_COLS = {"description", "effect", "where_to_find", "special_effect",
                 "loot", "objectives", "subtitle", "properties"}
    results = []
    for table in tables:
        # Try name column first (common across all structured tables)
        try:
            sql = f"SELECT * FROM [{table}] WHERE name LIKE ? LIMIT 5"
            cur.execute(sql, (f"%{keyword}%",))
            rows = cur.fetchall()
        except Exception:
            rows = []
        if not rows:
            # Some tables might not have a 'name' column; try title
            try:
                sql = f"SELECT * FROM [{table}] WHERE title LIKE ? LIMIT 5"
                cur.execute(sql, (f"%{keyword}%",))
                rows = cur.fetchall()
            except Exception:
                rows = []
        for r in rows:
            r = dict(r)
            name = r.get('name') or r.get('title') or '?'
            extras = []
            for c in INFO_COLS:
                v = r.get(c)
                if v:
                    extras.append(str(v)[:140])
            line = f"  [{table}] {name}"
            if extras:
                line += " | " + " | ".join(extras[:2])
            results.append(line)

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
        game: 目标游戏键名（如 hollow_knight、oni、terraria、silksong、cyberpunk2077、va11halla、mhw、baldurs_gate3）
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
                f"请在 Mac 本地运行 `python3 scripts/tool/mac_build.py --game {_game_key}` 来构建。")
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

    # 中英专名映射：把中文译名替换为英文原名，便于 LIKE 匹配英文数据库
    # （BG3 角色/关键名词常用译名，覆盖 Agent 直接传入中文名的情况）
    _ZH_TO_EN = {
        "阿斯代伦": "Astarion", "影心": "Shadowheart", "盖尔": "Gale",
        "威尔": "Wyll", "卡拉克": "Karlach", "莱埃泽尔": "Lae'zel",
        "养鸡妹": "Lae'zel", "哈尔辛": "Halsin", "明萨拉": "Minthara",
        "贾希拉": "Jaheira", "明斯克": "Minsc", "塔夫": "Tav",
        "邪念": "Dark Urge", "戈塔什": "Gortash", "奥林": "Orin",
        "凯瑟里克": "Ketheric", "夺心魔": "Illithid", "至上真神": "Absolute",
        "蒸煮罐": "Bubbling Cauldron", "沸腾蒸煮罐": "Bubbling Cauldron",
        "咕噜粥": "Gruel", "营地补给": "Camp Supplies",
    }
    q = query.lower().strip()
    if game == "baldurs_gate3":
        # 剥离游戏名残留（Agent 可能把完整问题传给搜索工具）
        q = re.sub(r"(博德之门3|博德之门 3|博得之门3|博得之门 3|博德之门|博得之门|bg3|baldur'?s? gate 3)", " ", q, flags=re.I).strip()
        for zh, en in _ZH_TO_EN.items():
            if zh in q:
                q = q.replace(zh, en)

    try:
        tbl_list = _fmt_table_names(db_path)

        # Try entity query: "查询 X Y" / "查 X"
        for prefix in ["查询", "查", "搜索", "搜"]:
            if q.startswith(prefix) and len(q) > 3:
                keyword = q[len(prefix):].strip()
                if keyword:
                    # Agent 可能画蛇添足拼接多个词（如「查询 蒸煮罐 stewpot 烹饪锅」），
                    # 逐个 token 搜索，取第一个能命中的即可
                    for token in keyword.split():
                        hit = _search_all_tables(db_path, token)
                        if "未找到" not in hit:
                            return hit
                    return _search_all_tables(db_path, keyword)

        # Try "所有 X" / "全部 X" / "列出所有 X"
        for cmd in ["所有", "全部", "列出所有", "列出全部"]:
            if cmd in q:
                keyword = q.split(cmd)[-1].strip()
                if keyword:
                    # Find matching table (only query tables that actually exist)
                    db1 = _get_db(db_path)
                    existing = set()
                    if db1:
                        cur = db1.cursor()
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        existing = {r['name'] for r in cur.fetchall()}
                        db1.close()
                    for table_match in keyword.split():
                        if table_match in existing:
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
                        "地点": ["locations", "areas"],
                        "位置": ["locations"],
                        "area": ["areas"],
                        "location": ["locations"],
                        "places": ["locations"],
                        "角色": ["characters", "character"],
                        "character": ["characters", "character"],
                        "NPC": ["characters", "character"],
                        "同伴": ["characters"],
                        "伙伴": ["characters"],
                        "companion": ["characters"],
                        "法术": ["spells"],
                        "魔法": ["spells"],
                        "spell": ["spells"],
                        "spells": ["spells"],
                        "书": ["books"],
                        "书籍": ["books"],
                        "book": ["books"],
                        "books": ["books"],
                        "任务": ["quests"],
                        "quest": ["quests"],
                        "quests": ["quests"],
                        "首领": ["bosses"],
                        "boss": ["bosses", "monsters"],
                        "装备": ["items", "weapons", "armor"],
                        "护甲": ["armor"],
                        "盔甲": ["armor"],
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
        # 去掉常见疑问词/语气词，提高英文 LIKE 命中率（如「Bubbling Cauldron 有什么用」）
        search_q = re.sub(
            r"(有什么用处|有什么用|是什么|有哪些|在哪里|在哪|怎么获得|如何获得|怎么得到|"
            r"多少钱|价格|介绍|资料|攻略|用处|用途|作用|效果|干嘛|物品|道具|装备|武器|角色|"
            r"what is |吗|呢|的|，|。|？)",
            "", q,
        ).strip()
        if not search_q:
            search_q = q
        return _search_all_tables(db_path, search_q) + f"\n\n💡 可用的表: {tbl_list}"

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

