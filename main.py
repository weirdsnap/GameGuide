#!/usr/bin/env python3
"""《空洞骑士》游戏助手——交互式 CLI（带对话记忆）。"""
import sys
from pathlib import Path

# 确保能找到 src
sys.path.insert(0, str(Path(__file__).parent / "src"))

from rag_agent.agent import ask


def main():
    print("🐞《空洞骑士》游戏助手（输入 /quit 退出）")
    print("━" * 40)

    history: list[dict] = []  # ← 对话记忆

    while True:
        try:
            q = input("\n❓ ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 拜拜！")
            break

        if not q:
            continue
        if q.lower() in ("/quit", "/exit", "/q"):
            print("👋 拜拜！")
            break

        # 传入历史，得到回答
        answer = ask(q, history=history)
        print(f"\n💬 {answer}")

        # 记录到历史
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
