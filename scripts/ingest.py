#!/usr/bin/env python3
"""
将 Fandom Wiki 数据（wiki_data.md）入库到 FAISS 向量库。

用法：
    python scripts/ingest.py                    ← 用 Wiki 数据（默认）
    python scripts/ingest.py --beta             ← 用 beta2 结构化数据
    python scripts/ingest.py --legacy           ← 用旧 API JSON 数据
    python scripts/ingest.py --data-dir /path   ← 指定数据目录

前置条件：
    - pip install -r requirements.txt
    - 支持 fastembed（本地 embedding）或配置 OPENAI_API_KEY
"""
import sys, argparse
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from rag_agent.vectorstore import build_vectorstore

def main():
    parser = argparse.ArgumentParser(description="构建 FAISS 向量库")
    parser.add_argument("--beta", action="store_true", help="使用 beta2 结构化数据（旧方案）")
    parser.add_argument("--legacy", action="store_true", help="使用旧 API JSON 数据（更旧方案）")
    parser.add_argument("--data-dir", type=str, default=None, help="数据目录路径")
    args = parser.parse_args()

    print("🧹 开始构建向量库...\n")
    build_vectorstore(use_wiki=not (args.beta or args.legacy), use_beta=args.beta)
    print("\n🎉 完成！")

if __name__ == "__main__":
    main()
