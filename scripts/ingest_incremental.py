#!/usr/bin/env python3
"""
增量向量库构建器 — 逐块构建 FAISS 索引，减少峰值内存。

用法：
  python scripts/ingest_incremental.py --game oni
  python scripts/ingest_incremental.py --game terraria
  python scripts/ingest_incremental.py --game silksong
  python scripts/ingest_incremental.py --game all
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import List

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import fastembed
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAMES_DIR = PROJECT_ROOT / "games"

GAME_DATA = {
    "oni": {
        "name": "Oxygen Not Included",
        "data_path": str(GAMES_DIR / "oni" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "oni" / "vectorstore"),
    },
    "terraria": {
        "name": "Terraria",
        "data_path": str(GAMES_DIR / "terraria" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "terraria" / "vectorstore"),
    },
    "silksong": {
        "name": "Hollow Knight Silksong",
        "data_path": str(GAMES_DIR / "silksong" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "silksong" / "vectorstore"),
    },
}


def load_wiki_documents(filepath: str) -> List[Document]:
    path = Path(filepath)
    if not path.exists():
        print(f"  ❌ 找不到: {filepath}")
        return []
    text = path.read_text(encoding="utf-8")
    chunks = re.split(r"(?=^# 文档[：:])", text, flags=re.MULTILINE)
    docs = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk or not chunk.startswith("# 文档"):
            continue
        lines = chunk.split("\n")
        title_match = re.search(r"^#\s*文档[：:]\s*(.*)", lines[0])
        title = title_match.group(1).strip() if title_match else "Unknown"
        category = ""
        for line in lines[:6]:
            m = re.search(r"- 类别[：:]\s*(.*)", line)
            if m:
                category = m.group(1).strip()
                break
        content_lines = []
        for line in lines:
            if any(line.startswith(p) for p in ("# 文档", "- 类别", "- 标识", "- 来源", "- 路径")):
                continue
            content_lines.append(line)
        content = "\n".join(content_lines).strip()
        if content:
            docs.append(Document(
                page_content=content,
                metadata={"title": title, "category": category, "source": filepath},
            ))
    return docs


def build_vectorstore_incremental(game_key: str):
    cfg = GAME_DATA[game_key]
    print(f"\n{'='*50}")
    print(f"📦 {cfg['name']} — 增量构建 FAISS")
    print(f"{'='*50}")

    # 加载文档
    docs = load_wiki_documents(cfg["data_path"])
    if not docs:
        print("  ❌ 无文档")
        return
    print(f"  📄 {len(docs)} 篇文档")

    # 分块
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800, chunk_overlap=160,
        separators=["\n## ", "\n# ", "\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(docs)
    total = len(chunks)
    print(f"  🧩 {total} 个片段")

    # Embedding 模型
    print(f"  🧠 加载 fastembed (bge-small-en-v1.5)...")
    embed_model = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")

    # 增量构建 FAISS
    print(f"  🔨 逐块构建 FAISS 索引...")
    vs = None
    batch_size = 50
    t0 = time.time()

    for i in range(0, total, batch_size):
        batch = chunks[i:i + batch_size]
        if vs is None:
            vs = FAISS.from_documents(batch, embed_model)
        else:
            vs.add_documents(batch)
        
        elapsed = time.time() - t0
        progress = min(i + batch_size, total)
        rate = progress / elapsed if elapsed > 0 else 0
        eta = (total - progress) / rate if rate > 0 else 0
        print(f"    ├─ {progress}/{total} ({rate:.1f}/s, ~{eta:.0f}s remaining)")

    # 保存
    vs_dir = Path(cfg["vectorstore_dir"])
    vs_dir.mkdir(parents=True, exist_ok=True)
    vs.save_local(str(vs_dir))
    elapsed = time.time() - t0
    print(f"  ✅ 保存到 {vs_dir}")
    print(f"  ⏱️  总耗时: {elapsed:.0f}s")

    # 验证
    vs2 = FAISS.load_local(str(vs_dir), embed_model, allow_dangerous_deserialization=True)
    print(f"  ✅ 验证: {vs2.index.ntotal} 个向量")


def main():
    parser = argparse.ArgumentParser(description="增量构建游戏向量库")
    parser.add_argument("--game", "-g", required=True,
                        choices=list(GAME_DATA.keys()) + ["all"],
                        help="要构建的游戏")
    args = parser.parse_args()
    if args.game == "all":
        for key in GAME_DATA:
            build_vectorstore_incremental(key)
    else:
        build_vectorstore_incremental(args.game)


if __name__ == "__main__":
    main()
