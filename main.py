#!/usr/bin/env python3
"""RAG Agent 交互入口。"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root / "src"))

from rag_agent.agent import ask
from rag_agent.config import OPENAI_API_KEY


def main():
    if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("your_"):
        print("⚠️ 请先配置 OPENAI_API_KEY：复制 .env.example 为 .env 并填入真实 Key")
        return

    print("🤖 RAG Agent 已启动，输入问题开始对话，输入 exit 退出。")
    while True:
        try:
            question = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit", "q"}:
            print("再见！")
            break
        answer = ask(question)
        print(f"\nAgent：{answer}")


if __name__ == "__main__":
    main()
