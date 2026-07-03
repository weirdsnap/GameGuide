#!/usr/bin/env python3
"""《空洞骑士》游戏助手——交互式 CLI。"""
import sys
from pathlib import Path

# 确保能找到 src
sys.path.insert(0, str(Path(__file__).parent / "src"))

from rag_agent.agent import ask


def main():
    print("🐞《空洞骑士》游戏助手（输入 /quit 退出）")
    print("━" * 40)
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

        answer = ask(q)
        print(f"\n💬 {answer}")


if __name__ == "__main__":
    main()
