#!/usr/bin/env python3
"""
轻量版向量库构建器 — 只生成分块后的文档 JSON，不做 FAISS。

用于内存有限的服务器。生成的 JSON 可在 Mac 上转成 FAISS。

用法：
  python scripts/ingest_light.py --game oni
  python scripts/ingest_light.py --game terraria
  python scripts/ingest_light.py --game silksong
  python scripts/ingest_light.py --game all
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Dict

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAMES_DIR = PROJECT_ROOT / "games"

GAME_DATA: Dict[str, Dict[str, str]] = {
    "oni": {
        "name": "Oxygen Not Included",
        "data_path": str(GAMES_DIR / "oni" / "data" / "wiki_data.md"),
        "chunks_dir": str(GAMES_DIR / "oni" / "chunks"),
    },
    "terraria": {
        "name": "Terraria",
        "data_path": str(GAMES_DIR / "terraria" / "data" / "wiki_data.md"),
        "chunks_dir": str(GAMES_DIR / "terraria" / "chunks"),
    },
    "silksong": {
        "name": "Hollow Knight Silksong",
        "data_path": str(GAMES_DIR / "silksong" / "data" / "wiki_data.md"),
        "chunks_dir": str(GAMES_DIR / "silksong" / "chunks"),
    },
    "hollow_knight": {
        "name": "Hollow Knight",
        "data_path": str(PROJECT_ROOT / "data" / "wiki_data.md"),
        "chunks_dir": str(PROJECT_ROOT / "chunks"),
    },
}


def load_wiki_documents(filepath: str) -> List[Dict]:
    path = Path(filepath)
    if not path.exists():
        print(f"  ❌ 找不到数据文件：{filepath}")
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
            cat_match = re.search(r"- 类别[：:]\s*(.*)", line)
            if cat_match:
                category = cat_match.group(1).strip()
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


def build_chunks(game_key: str):
    cfg = GAME_DATA[game_key]
    print(f"\n{'='*50}")
    print(f"📦 {cfg['name']} — 生成分块 JSON")
    print(f"{'='*50}")

    docs = load_wiki_documents(cfg["data_path"])
    if not docs:
        print(f"  ❌ 没有文档可处理")
        return
    print(f"  📄 加载了 {len(docs)} 篇文档")

    total_chars = sum(len(d.page_content) for d in docs)
    print(f"  📏 总字符数: ~{total_chars:,}")

    # 分块
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800, chunk_overlap=160,
        separators=["\n## ", "\n# ", "\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(docs)
    print(f"  🧩 分块后: {len(chunks)} 个片段")

    # 保存为 JSON
    chunks_dir = Path(cfg["chunks_dir"])
    chunks_dir.mkdir(parents=True, exist_ok=True)

    output = []
    for i, c in enumerate(chunks):
        output.append({
            "index": i,
            "content": c.page_content,
            "metadata": c.metadata,
        })

    json_path = chunks_dir / "chunks.json"
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  ✅ 分块已保存: {json_path}")
    print(f"  📦 文件大小: {len(json.dumps(output, ensure_ascii=False)) / 1024:.0f} KB")

    # 也保存纯文本用于向量化
    texts = [c.page_content for c in chunks]
    metas = [c.metadata for c in chunks]
    text_path = chunks_dir / "texts.json"
    meta_path = chunks_dir / "metadatas.json"
    (chunks_dir / "texts.json").write_text(
        json.dumps(texts, ensure_ascii=False), encoding="utf-8")
    (chunks_dir / "metadatas.json").write_text(
        json.dumps(metas, ensure_ascii=False), encoding="utf-8")
    print(f"  ✅ 文本/元数据已分别保存")


def main():
    parser = argparse.ArgumentParser(description="生成游戏文档分块（轻量版，不构建 FAISS）")
    parser.add_argument("--game", "-g", required=True,
                        choices=list(GAME_DATA.keys()) + ["all"],
                        help="要处理的游戏")
    args = parser.parse_args()

    if args.game == "all":
        for key in GAME_DATA:
            build_chunks(key)
    else:
        build_chunks(args.game)


if __name__ == "__main__":
    main()
