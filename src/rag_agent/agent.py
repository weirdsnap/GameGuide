"""《空洞骑士》游戏助手 Agent。"""
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent

from rag_agent.tools import KnowledgeSearchTool

load_dotenv()

SYSTEM_PROMPT = """你是《空洞骑士：丝之歌》游戏知识的专家助手。
你使用专业的中文游戏术语回答玩家的问题，回答要简洁准确。

规则：
1. 每次回答问题前，先调用 search_knowledge_base 获取相关知识
2. 基于检索到的知识回答问题，不要编造信息
3. 如果知识库中没有相关信息，如实告知玩家
4. 回答时使用中文，但游戏中的专有名词保留英文原名"""


def _build_agent():
    """构建 Agent。"""
    model = ChatOpenAI(
        model=os.getenv("CHAT_MODEL", "deepseek-v4-flash"),
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        temperature=0.2,
        max_tokens=4096,
    )

    agent = create_agent(
        model=model,
        tools=[KnowledgeSearchTool()],
        system_prompt=SYSTEM_PROMPT,
        name="hollow_knight_agent",
    )
    return agent


def ask(question: str, verbose: bool = False) -> str:
    """向 Agent 提问，返回回答文本。"""
    agent = _build_agent()
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    # 提取最后的 AI 回复
    for msg in reversed(result["messages"]):
        if hasattr(msg, "content") and msg.content and getattr(msg, "type", "") not in ("tool", "tool_call"):
            return msg.content
    return "抱歉，我没能生成有效的回答。"


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "How do I get the Mantis Claw?"
    print(f"❓ {q}\n")
    print(ask(q))
