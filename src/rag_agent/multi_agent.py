"""
多游戏 Agent — 支持空洞骑士、缺氧、泰拉瑞亚、丝之歌。

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
from rag_agent.game_router import AVAILABLE_GAMES, build_game_description, build_game_prompt, detect_game, is_switch_query
from rag_agent.vectorstore import get_retriever

logger = logging.getLogger(__name__)

# ── 游戏切换状态 ──
_LAST_GAME: Optional[str] = None
_LAST_GAME_CONFIRMED: bool = False


def _reset_game_state():
    """重置游戏状态（新对话时）。"""
    global _LAST_GAME, _LAST_GAME_CONFIRMED
    _LAST_GAME = None
    _LAST_GAME_CONFIRMED = False


def _game_slug(name: str) -> str:
    return name.lower().replace(' ', '_')


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
        try:
            sql = f"SELECT name, slug FROM [{table}] WHERE name LIKE ? LIMIT 5"
            cur.execute(sql, (f"%{keyword}%",))
            for r in cur.fetchall():
                results.append(f"  [{table}] {r['name']}")
        except Exception:
            pass

    db.close()
    if results:
        return "数据库中找到以下相关条目：\n" + "\n".join(results[:15]) + \
               "\n\n💡 试试更具体的查询，如「查询护符 Grubsong」或「列出 3 格 Cost 的护符」"
    return "(数据库中未找到相关内容)"


def _fmt_table_name(db_path: str) -> str:
    """获取数据库的表名列表（用于提示）。"""
    db = _get_db(db_path)
    if not db:
        return ""
    cur = db.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r['name'] for r in cur.fetchall()]
    db.close()
    return ", ".join(tables)


# ══════════════════════════════════════════
#  按游戏创建工具
# ══════════════════════════════════════════

def create_game_tools(game_key: str) -> List:
    """根据游戏创建对应的知识库和数据库查询工具。"""
    cfg = AVAILABLE_GAMES.get(game_key)
    if not cfg:
        return []

    vs_dir = cfg["vectorstore_dir"]
    db_path = cfg["db_path"]
    game_name = cfg["name"]

    # 向量库是否可用的标记（检查 index.faiss 是否存在）
    _vs_ok = os.path.isdir(vs_dir) and os.path.isfile(os.path.join(vs_dir, 'index.faiss'))

    @tool
    def search_knowledge_base(query: str, k: int = 8) -> str:
        """Search the game wiki knowledge base using vector retrieval.

        Use this for: lore, story backgrounds, area descriptions, boss strategies,
        game mechanics, NPC dialogues, crafting recipes, and any descriptive content.

        Args:
            query: Search query in English keywords for best results
            k: Number of relevant results (default 8)
        """
        if not _vs_ok:
            missing = os.path.basename(vs_dir)
            return (f"[知识库暂时不可用] {game_name} 的向量库尚未构建。\n"
                    f"请在 Mac 本地运行 `python3 scripts/run_on_mac.py --game {game_key}` 来构建。\n")
        try:
            retriever = get_retriever(k=k, vectorstore_dir=vs_dir)
            docs = retriever.invoke(query)
            if not docs:
                return f"(知识库未找到关于「{query}」的内容)"
            parts = []
            for i, doc in enumerate(docs, 1):
                content = doc.page_content.strip()
                meta = doc.metadata or {}
                source = meta.get("source", meta.get("title", ""))
                parts.append(f"【参考 {i}】{source}\n{content[:500]}")
            return "\n\n".join(parts)
        except Exception as e:
            return f"[知识库检索出错] {e}"

    @tool
    def query_structured_data(query: str) -> str:
        """Query the game structured database for precise data.

        Use this for: boss HP, item stats, damage values, costs, prices,
        and any numerical/quantifiable game data.

        Input format: natural language query like:
        - "查询护符 Grubsong" — get details about a specific item/boss/enemy
        - "3格Cost的护符" — filter by cost/level/stat
        - "HP>500" — filter by HP threshold
        - "所有敌人" — list all entries in a category

        Args:
            query: Natural language query
        """
        try:
            q = query.lower().strip()
            tbl = _fmt_table_name(db_path)

            # Try entity query: "查询 X Y"
            for prefix in ["查询", "查", "搜索", "搜"]:
                if q.startswith(prefix) and len(q) > 3:
                    keyword = q[len(prefix):].strip()
                    if keyword:
                        return _search_all_tables(db_path, keyword)

            # Try "所有 X" / "全部 X"
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
                        db = _get_db(db_path)
                        if db:
                            cur = db.cursor()
                            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                            tables = [r['name'] for r in cur.fetchall()]
                            db.close()
                            for table in tables:
                                if any(k in table for k in keyword.split()):
                                    rows = _query_db(db_path, f"SELECT * FROM [{table}] LIMIT 30")
                                    if rows:
                                        return _format_db_result(rows, table)
                        # If no table match, search all tables
                        return _search_all_tables(db_path, keyword)

            # Try cost/number filter
            cost_match = __import__('re').search(r"(?:cost|等级|级|格|槽)\s*[:：=]?\s*(\d+)", q)
            if cost_match:
                val = cost_match.group(1)
                rows = _query_db(db_path, f"SELECT * FROM ['enemies'] WHERE hp >= {val} LIMIT 30")
                # Alternative: search all tables for numeric columns
                db = _get_db(db_path)
                if db:
                    cur = db.cursor()
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [r['name'] for r in cur.fetchall()]
                    db.close()
                    results = []
                    for table in tables:
                        for col in ['hp', 'health', 'damage', 'cost', 'power', 'defense', 'calories']:
                            try:
                                rows = _query_db(db_path, f"SELECT name, {col} FROM [{table}] WHERE {col} IS NOT NULL AND {col} >= {val} LIMIT 5")
                                if rows:
                                    results.append(f"  [{table}] " + ", ".join(f"{r.get('name','?')} ({col}={r.get(col,'?')})" for r in rows))
                            except Exception:
                                pass
                    if results:
                        return f"筛选 {val} 以上的结果：\n" + "\n".join(results[:20])
                return f"（未找到匹配 {val} 的结果）"

            # HP filter
            hp_match = __import__('re').search(r"(?:HP|hp|血量|生命)\s*[>≥:：=]?\s*(\d+)", q)
            if hp_match:
                val = hp_match.group(1)
                results = []
                for table in ['bosses', 'enemies', 'npcs', 'critters']:
                    rows = _query_db(db_path, f"SELECT name, hp, health, damage FROM [{table}] WHERE hp >= {val} LIMIT 10")
                    if not rows:
                        rows = _query_db(db_path, f"SELECT name, health FROM [{table}] WHERE health >= {val} LIMIT 10")
                    if rows:
                        results.append(f"[{table}]")
                        results.extend(f"  {r.get('name','?')} (HP={r.get('hp') or r.get('health','?')})" for r in rows)
                if results:
                    return "\n".join(results)
                return f"(未找到 HP ≥ {val} 的结果)"

            # Fallback: search all tables
            return _search_all_tables(db_path, q)

        except Exception as e:
            return f"[结构化查询出错] {e}"

    return [search_knowledge_base, query_structured_data]


# ══════════════════════════════════════════
#  游戏检测 + 对话路由
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

    # Insert system prompt at start if not already there
    if game_prompt and (not history or not any(isinstance(m, SystemMessage) for m in messages)):
        messages.insert(0, SystemMessage(content=game_prompt))

    messages.append(HumanMessage(content=question))
    return messages


def ask(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    model_name: Optional[str] = None,
    verbose: bool = False,
) -> str:
    """多游戏 Agent 入口。

    自动检测游戏、加载对应工具、路由问题。

    Args:
        question: 用户问题
        history: 可选历史消息 [{"role": "user"/"assistant", "content": "..."}]
        model_name: 可选模型覆盖
        verbose: 打印详细信息

    Returns:
        Agent 回复文本
    """
    global _LAST_GAME, _LAST_GAME_CONFIRMED

    q = question.strip()
    if not q:
        return "请问你想了解哪款游戏的攻略？"

    # 检测游戏
    game_key, confidence = detect_game(q)

    logger.info(f"检测游戏: key={game_key}, confidence={confidence:.2f}, query={q[:50]}")

    # 连续性判断
    if confidence >= 0.4:
        # 检测到明确的游戏，使用检测结果
        _LAST_GAME = game_key
        _LAST_GAME_CONFIRMED = True
        logger.info(f"检测到游戏: {game_key}")
    elif is_switch_query(q):
        # 用户想切换游戏但没指名 → 弹菜单
        logger.info(f"检测到切换意图: {q[:50]}")
        _reset_game_state()
        return (
            "你想切换到哪个游戏？请选择：\n\n"
            "1. 🐈 **空洞骑士** (Hollow Knight)\n"
            "2. 🪱 **丝之歌** (Hollow Knight Silksong)\n"
            "3. 💨 **缺氧** (Oxygen Not Included)\n"
            "4. 🪨 **泰拉瑞亚** (Terraria)\n\n"
            "直接告诉我游戏名称就可以啦！"
        )
    elif history and _LAST_GAME is not None and _LAST_GAME_CONFIRMED:
        # 置信度低但有历史对话 + 上次已确认的游戏 → 延续上轮
        game_key = _LAST_GAME
        logger.info(f"延续上轮游戏: {game_key}")
    else:
        # 啥都没有 → 弹选择菜单
        _reset_game_state()
        return (
            "请问你想问哪款游戏的攻略？请选择：\n\n"
            "1. 🐈 **空洞骑士** (Hollow Knight)\n"
            "2. 🪱 **丝之歌** (Hollow Knight Silksong)\n"
            "3. 💨 **缺氧** (Oxygen Not Included)\n"
            "4. 🪨 **泰拉瑞亚** (Terraria)\n\n"
            "直接告诉我游戏名称就可以开始啦！"
        )

    # 检查向量库和数据库是否存在
    cfg = AVAILABLE_GAMES[game_key]
    vs_ok = os.path.isdir(cfg["vectorstore_dir"])
    db_ok = os.path.isfile(cfg["db_path"])

    if not vs_ok and not db_ok:
        _reset_game_state()
        return f"抱歉，{cfg['name']} 的知识库尚未准备好。请联系管理员初始化数据。"

    game_prompt = build_game_prompt(game_key)

    # 修正 system prompt：如果 game_prompt 包含工具说明但不够 HK 时丢失了引用
    # 补充通用规则
    base_rules = """
**Rules:**
- Answer in Chinese (中文) keeping English game terms in parentheses.
- Always cite your sources: mention whether info came from knowledge base or database.
- If both sources are needed, use both tools.
- Never fabricate game information.
- Be concise, informative, max 3-4 paragraphs.
- If the user asks about another game or topic, politely decline.
"""
    full_prompt = game_prompt + base_rules

    # 创建 LLM
    config = dict(LLM_CONFIG)
    if model_name:
        config["model"] = model_name
    llm = ChatOpenAI(**config)

    # 创建游戏工具
    tools = create_game_tools(game_key)

    # 创建 Agent
    agent = create_agent(llm, tools, prompt=SystemMessage(content=full_prompt))

    # 构建消息
    messages = build_messages(q, history)

    if verbose:
        logger.info(f"🕹️ 游戏: {game_key}")
        logger.info(f"  🤖 模型: {config.get('model', 'default')}")
        logger.info(f"  📊 向量库: {'✅' if vs_ok else '❌'} | 数据库: {'✅' if db_ok else '❌'}")
        logger.info(f"  💬 消息: {len(messages)} 条")

    try:
        result = agent.invoke({"messages": messages})
        answer = result.get("messages", [])[-1].content if result.get("messages") else ""
        return answer or "（Agent 没有返回有效回答）"
    except Exception as e:
        logger.error(f"Agent 调用失败: {e}")
        return f"[查询出错] {e}"
