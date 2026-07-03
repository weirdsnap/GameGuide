"""Agent 可调用的工具。"""
from typing import Type
from pydantic import BaseModel, Field
from langchain.tools import BaseTool

from .vectorstore import get_retriever


class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="搜索关键词，描述你想了解的圣巢信息（如区域、Boss、护符、NPC、技能等）")


class KnowledgeSearchTool(BaseTool):
    name: str = "search_knowledge_base"
    description: str = (
        "从《空洞骑士》知识库中检索相关信息，包括：区域位置与连接、"
        "Boss 攻击方式与阶段、NPC 故事与对话、护符效果与联动、"
        "技能获取与用途等。回答圣巢相关问题前请先调用此工具。"
    )
    args_schema: Type[BaseModel] = KnowledgeSearchInput

    def _run(self, query: str) -> str:
        retriever = get_retriever()
        results = retriever.invoke(query)
        if not results:
            return "知识库中未找到相关信息。"

        output_parts = []
        for i, doc in enumerate(results, 1):
            meta = doc.metadata
            source = meta.get("name", meta.get("source", "未知"))
            category = meta.get("category", "")
            label = f"[{source}]" + (f"（{category}）" if category else "")
            output_parts.append(f"--- 结果 {i}：{label} ---\n{doc.page_content}")

        return "\n\n".join(output_parts)
