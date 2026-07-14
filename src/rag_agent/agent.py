"""
Hollow Knight RAG Agent — 聊天代理主模块。

支持双通道检索：
1. RAG 向量检索（自然语言描述、剧情、策略等）
2. SQLite 结构化查询（数值、属性、精确数据）

用户问题 → LLM 路由 → 选择合适工具 → 整合回答
"""

import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent as create_agent

from rag_agent.config import LLM_CONFIG
from rag_agent.tools import KnowledgeSearchTool, StructuredQueryTool

logger = logging.getLogger(__name__)

# ── 工具实例 ──
_knowledge_search = KnowledgeSearchTool()
_structured_query = StructuredQueryTool()


@tool
def search_knowledge_base(query: str, k: int = 8) -> str:
    """Search the Hollow Knight wiki knowledge base using RAG vector retrieval.
    
    Use this for: lore, story backgrounds, area descriptions, boss strategies,
    charm concepts, gameplay mechanics, NPC dialogues, quest walkthroughs,
    and any descriptive/narrative content.
    
    Input should be in English keywords or phrases for best results.
    
    Args:
        query: Search query in English keywords
        k: Number of relevant documents to return (default 8)
    """
    return _knowledge_search.run(query, k=k)


@tool
def query_structured_data(query: str) -> str:
    """Query the Hollow Knight structured database for precise numerical data.
    
    Use this for: charm notch costs, boss HP values, skill damage numbers,
    enemy Geo drops, item prices, area connections, character inventories,
    and any other quantifiable game data.
    
    Supports queries like:
    - "查护符 Grubsong" — get detailed info about a specific charm
    - "3格Cost的护符" — list all charms with 3 notch cost
    - "HP>300的Boss" — list bosses with more than 300 HP
    - "所有区域" — list all areas
    - "搜索护符 fury" — fuzzy search charms
    
    Args:
        query: Natural language query describing what data to look up
    """
    return _structured_query.run(query)


# ── 系统提示 ──

SYSTEM_PROMPT = """You are a Hollow Knight (《空洞骑士》) game expert assistant named nanobot 🐈.

Your knowledge comes from two sources — use them wisely:

1. **search_knowledge_base** — Vector knowledge base (Wiki text)
   Good for: lore, story, boss strategies, charm combos, area descriptions, NPC dialogue,
   gameplay walkthroughs, quests, and general game concepts.
   ALWAYS search this first for descriptive questions.

2. **query_structured_data** — Structured database with precise numerical data
   Good for: charm notch costs, boss HP, skill damage, enemy geo drops, item prices,
   area connections, and any exact numbers/attributes.
   ALWAYS use this when the user asks for numbers, costs, HP, stats, or comparing values.

**How to respond:**
- Answer in Chinese (中文), but keep English game terms in parentheses.
- If the user asks in Chinese, translate key concepts to English for searching.
- Always cite your sources: mention whether info came from the knowledge base or database.
- If both sources are needed, use both tools.
- Be concise but informative. At most 3-4 paragraphs for normal answers.

**CRITICAL RULE — Stay in scope:**
- You ONLY answer questions about Hollow Knight (《空洞骑士》).
- If the question is about ANY OTHER GAME (Zelda, Elden Ring, Silksong, etc.), ANY OTHER SUBJECT (math, politics, weather), or ANY OTHER TOPIC outside Hollow Knight:
  - Say: "抱歉，我专注于《空洞骑士》(Hollow Knight) 的游戏知识。无法回答关于 [其他话题] 的问题。" alone, no further detail.
  - Do NOT answer the question. Do NOT provide any information about other games.
- Exception: simple greetings, how-are-you, thanks, and friendly chat are fine.

**Rules:**
- Never fabricate game information.
- If unsure, search the knowledge base first.
- For numbers (cost, HP, damage, geo), always query the structured database first."""


def create_hk_agent(model_name: Optional[str] = None) -> Any:
    """创建 Hollow Knight Agent（LangGraph）。"""
    config = dict(LLM_CONFIG)
    if model_name:
        config["model"] = model_name

    llm = ChatOpenAI(**config)
    tools = [search_knowledge_base, query_structured_data]
    agent = create_agent(llm, tools, prompt=SystemMessage(content=SYSTEM_PROMPT))
    return agent


def ask(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    model_name: Optional[str] = None,
    verbose: bool = False,
) -> str:
    """Ask the Hollow Knight agent a question.

    Args:
        question: User's question in any language
        history: Optional list of previous messages [{"role": "user"/"assistant", "content": "..."}]
        model_name: Optional model override
        verbose: Print intermediate steps

    Returns:
        Agent's response text
    """
    agent = create_hk_agent(model_name)

    # Build message list from history + current question
    messages: List[BaseMessage] = []

    if history:
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

    messages.append(HumanMessage(content=question))

    if verbose:
        logger.info(f"🤖 询问 Agent (model={model_name or 'default'}): {question[:60]}...")
        logger.info(f"  消息列表: {len(messages)} 条")

    try:
        result = agent.invoke({"messages": messages})
        answer = result.get("messages", [])[-1].content if result.get("messages") else ""
        return answer or "（Agent 没有返回有效回答）"
    except Exception as e:
        logger.error(f"Agent 调用失败: {e}")
        return f"[查询出错] {e}"


# ── CLI 快速测试 ──

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    else:
        question = "Fury of the Fallen 在哪里？"

    print(f"\n问题：{question}\n")
    print(f"回答：{ask(question, verbose=True)}\n")
