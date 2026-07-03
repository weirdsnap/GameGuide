#!/usr/bin/env python3
"""将 data/ 目录下的文档入库到向量库。"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from rag_agent.vectorstore import build_vectorstore

if __name__ == "__main__":
    build_vectorstore()
