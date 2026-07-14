#!/usr/bin/env python3
"""
向量库构建器 — 为指定游戏构建 FAISS 向量库。

用法：
  python scripts/ingest_game.py --game oni
  python scripts/ingest_game.py --game terraria
  python scripts/ingest_game.py --game silksong
  python scripts/ingest_game.py --game hollow_knight
  python scripts/ingest_game.py --game all
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAMES_DIR = PROJECT_ROOT / "games"
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from rag_agent.vectorstore import load_vectorstore


GAME_DATA: Dict[str, Dict[str, str]] = {
    "hollow_knight": {
        "name": "Hollow Knight",
        "data_path": str(GAMES_DIR / "hollow_knight" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "hollow_knight" / "vectorstore"),
    },
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


def load_wiki_documents(filepath: str) -> List[Dict]:
    """从 wiki_data.md 加载文档列表（以 # 文档： 作为分割标记）。"""
    path = Path(filepath)
    if not path.exists():
        print(f"  ❌ 找不到数据文件：{filepath}")
        return []

    text = path.read_text(encoding="utf-8")

    # 以 # 文档： 作为文档分割标记
    chunks = re.split(r"(?=^# 文档[：:])", text, flags=re.MULTILINE)
    docs = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        # 跳过非文档内容的题头行（游戏名分类索引等）
        if not chunk.startswith("# 文档"):
            continue

        lines = chunk.split("\n")
        title_match = re.search(r"^#\s*文档[：:]\s*(.*)", lines[0]) if lines else None
        title = title_match.group(1).strip() if title_match else "Unknown"

        # Extract category if available
        category = ""
        for line in lines[:6]:
            cat_match = re.search(r"- 类别[：:]\s*(.*)", line)
            if cat_match:
                category = cat_match.group(1).strip()
                break

        # Build content (skip metadata lines)
        content_lines = []
        for line in lines:
            if any(line.startswith(p) for p in ("# 文档", "- 类别", "- 标识", "- 来源", "- 路径")):
                continue
            content_lines.append(line)

        content = "\n".join(content_lines).strip()
        if content:
            docs.append({
                "content": content,
                "metadata": {"title": title, "category": category},
            })

    return docs


def build_vectorstore(game_key: str):
    """为指定游戏构建向量库。"""
    cfg = GAME_DATA[game_key]
    print(f"\n{'='*50}")
    print(f"📦 {cfg['name']} — 构建向量库")
    print(f"{'='*50}")

    # 加载文档
    docs = load_wiki_documents(cfg["data_path"])
    if not docs:
        print("  ❌ 没有文档可处理")
        return
    print(f"  📄 加载了 {len(docs)} 篇文档")
    total_chars = sum(len(d["content"]) for d in docs)
    print(f"  📏 总字符数: ~{total_chars:,}")

    # 构建向量库
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document
    import os

    # 设置 embedding 模型
    model_name = os.environ.get("FASTEMBED_MODEL", "BAAI/bge-small-en-v1.5")
    print(f"  🧠 Embedding 模型: {model_name}")
    embed_model = FastEmbedEmbeddings(model_name=model_name)

    # 分块
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=160,
        separators=["\n## ", "\n# ", "\n\n", "\n", ". ", " ", ""],
    )
    documents = [
        Document(page_content=d["content"], metadata=d["metadata"]) for d in docs
    ]
    chunks = splitter.split_documents(documents)
    print(f"  🧩 分块后: {len(chunks)} 个片段")

    if not chunks:
        print("  ❌ 无分块结果")
        return

    # 构建 FAISS
    vectorstore_dir = cfg["vectorstore_dir"]
    print(f"  🔨 构建 FAISS 索引...")
    vectorstore = FAISS.from_documents(chunks, embed_model)

    # 保存
    Path(vectorstore_dir).mkdir(parents=True, exist_ok=True)
    vectorstore.save_local(vectorstore_dir)
    print(f"  ✅ 向量库已保存到: {vectorstore_dir}")

    # 验证
    vs2 = FAISS.load_local(vectorstore_dir, embed_model, allow_dangerous_deserialization=True)
    print(f"  ✅ 验证通过: {vs2.index.ntotal} 个向量")


def main():
    parser = argparse.ArgumentParser(description="构建游戏向量库")
    parser.add_argument("--game", "-g", required=True,
                        choices=list(GAME_DATA.keys()) + ["all"],
                        help="要构建的游戏")
    args = parser.parse_args()

    if args.game == "all":
        for key in GAME_DATA:
            try:
                build_vectorstore(key)
            except Exception as e:
                print(f"  ❌ {key} 构建失败: {e}")
                import traceback
                traceback.print_exc()
    else:
        build_vectorstore(args.game)


if __name__ == "__main__":
    main()
