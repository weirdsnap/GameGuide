"""RAG Agent 主体。"""
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from .config import OPENAI_API_KEY, OPENAI_BASE_URL, CHAT_MODEL
from .tools import KnowledgeSearchTool


def create_agent():
    if not OPENAI_API_KEY:
        raise ValueError("请先设置 OPENAI_API_KEY")

    tools = [KnowledgeSearchTool()]

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "你是一个《空洞骑士》（Hollow Knight）知识专家，熟悉圣巢（Hallownest）的一切——"
            "包括各个区域的地形与连接关系、Boss 的攻击方式与阶段、NPC 的故事与位置、"
            "护符的效果与联动、以及技能/法术的获取与用途。\n\n"
            "回答用户问题时：\n"
            "1. 先调用 search_knowledge_base 工具检索相关信息\n"
            "2. 基于检索结果给出准确、生动的回答\n"
            "3. 如果知识库里没有，明确告知用户\n"
            "4. 可以用游戏术语（Geo、SOUL、Nail 等）增加沉浸感\n\n"
            "保持专业但有趣的语气，就像一位熟悉圣巢的向导。"
        ),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    llm = ChatOpenAI(
        model=CHAT_MODEL,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        temperature=0.2,
    )

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)


def ask(question: str) -> str:
    executor = create_agent()
    result = executor.invoke({"input": question})
    return result["output"]
