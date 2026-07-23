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
import os
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
        "data_path_zh": str(GAMES_DIR / "hollow_knight" / "data" / "wiki_data_zh.md"),
        "vectorstore_dir": str(GAMES_DIR / "hollow_knight" / "vectorstore"),
    },
    "oni": {
        "name": "Oxygen Not Included",
        "data_path": str(GAMES_DIR / "oni" / "data" / "wiki_data.md"),
        "data_path_zh": str(GAMES_DIR / "oni" / "data" / "wiki_data_zh.md"),
        "vectorstore_dir": str(GAMES_DIR / "oni" / "vectorstore"),
    },
    "terraria": {
        "name": "Terraria",
        "data_path": str(GAMES_DIR / "terraria" / "data" / "wiki_data.md"),
        "data_path_zh": str(GAMES_DIR / "terraria" / "data" / "wiki_data_zh.md"),
        "vectorstore_dir": str(GAMES_DIR / "terraria" / "vectorstore"),
    },
    "silksong": {
        "name": "Hollow Knight Silksong",
        "data_path": str(GAMES_DIR / "silksong" / "data" / "wiki_data.md"),
        "data_path_zh": "",
        "vectorstore_dir": str(GAMES_DIR / "silksong" / "vectorstore"),
    },
    "cyberpunk2077": {
        "name": "Cyberpunk 2077",
        "data_path": str(GAMES_DIR / "cyberpunk2077" / "data" / "wiki_data.md"),
        "data_path_zh": "",
        "vectorstore_dir": str(GAMES_DIR / "cyberpunk2077" / "vectorstore"),
    },
    "va11halla": {
        "name": "VA-11 Hall-A",
        "data_path": str(GAMES_DIR / "va11halla" / "data" / "wiki_data.md"),
        "data_path_zh": str(GAMES_DIR / "va11halla" / "data" / "wiki_data_zh.md"),
        "vectorstore_dir": str(GAMES_DIR / "va11halla" / "vectorstore"),
    },
    "mhw": {
        "name": "Monster Hunter Wilds",
        "data_path": str(GAMES_DIR / "mhw" / "data" / "wiki_data.md"),
        "data_path_zh": "",
        "vectorstore_dir": str(GAMES_DIR / "mhw" / "vectorstore"),
    },
}


def load_wiki_documents(filepath: str) -> List[Dict]:
    """从 wiki_data.md 加载文档列表，支持多种格式。"""
    path = Path(filepath)
    if not path.exists():
        print(f"  ❌ 找不到数据文件：{filepath}")
        return []

    text = path.read_text(encoding="utf-8")
    docs = []

    # === 格式1：# 文档： 作为分割标记（HK / ONI / Terraria / Silksong） ===
    if "# 文档" in text:
        chunks = re.split(r"(?=^# 文档[：:])", text, flags=re.MULTILINE)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            if not chunk.startswith("# 文档"):
                continue

            lines = chunk.split("\n")
            title_match = re.search(r"^#\s*文档[：:]\s*(.*)", lines[0]) if lines else None
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
                docs.append({
                    "content": content,
                    "metadata": {"title": title, "category": category},
                })

    # === 格式2：## Title 分割（cyberpunk2077 / va11halla） ===
    elif "\n## " in text or text.startswith("## "):
        # 以 ## Title 作为分割标记（忽略前面的全局题头）
        prefix = text.split("\n## ", 1)[0] if "\n## " in text else ""
        # 从第一个 ## 开始拆分
        body = text[len(prefix):]
        # 把每个 ## Title 作为一个块
        chunks = re.split(r"\n(?=## )", body)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            lines = chunk.split("\n")
            title_match = re.match(r"^##\s+(.+)", lines[0]) if lines else None
            title = title_match.group(1).strip() if title_match else "Unknown"
            if title == "Unknown":
                continue
            # 跳过 ## Title 行和 --- 分隔线
            content_lines = [l for l in lines if not l.startswith("## ") and not l.strip().startswith("---")]
            content = "\n".join(content_lines).strip()
            if content:
                docs.append({
                    "content": content,
                    "metadata": {"title": title, "category": ""},
                })

    # === 格式3：无法识别格式，整篇作为一个文档 ===
    if not docs:
        lines = text.split("\n")
        title = "Unknown"
        for line in lines:
            m = re.match(r"^#\s+(.+)", line)
            if m:
                title = m.group(1).strip()
                break
        content_lines = [l for l in lines if not l.startswith("# ")]
        content = "\n".join(content_lines).strip()
        if content:
            print(f"  ⚠ 无法识别格式，整篇作为单文档（标题: {title}）")
            docs.append({
                "content": content,
                "metadata": {"title": title, "category": ""},
            })

    return docs


def build_vectorstore(game_key: str):
    """为指定游戏构建向量库。"""
    cfg = GAME_DATA[game_key]
    print(f"\n{'='*50}")
    print(f"📦 {cfg['name']} — 构建向量库")
    print(f"{'='*50}")

    # 加载英文文档
    docs = load_wiki_documents(cfg["data_path"])
    for d in docs:
        d["metadata"]["language"] = "en"

    if not docs:
        print("  ❌ 英文 Wiki 没有文档可处理")
        return
    print(f"  📄 EN: {len(docs)} 篇文档")
    total_chars_en = sum(len(d["content"]) for d in docs)
    print(f"  📏 总字符数: ~{total_chars_en:,}")

    # 加载中文文档（如果有）
    zh_path = cfg.get("data_path_zh", "")
    zh_docs = load_wiki_documents(zh_path) if zh_path and Path(zh_path).exists() else []
    for d in zh_docs:
        d["metadata"]["language"] = "zh"
    if zh_docs:
        print(f"  📄 ZH: {len(zh_docs)} 篇文档")
        total_chars_zh = sum(len(d["content"]) for d in zh_docs)
        print(f"  📏 总字符数: ~{total_chars_zh:,}")
        docs.extend(zh_docs)
    else:
        print("  📄 ZH: 无中文 Wiki 数据")

    print(f"  📄 合计: {len(docs)} 篇文档")

    # 构建向量库
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document

    # 设置 embedding 模型（从 config.py 读取默认值）
    from rag_agent.config import FASTEMBED_MODEL as DEFAULT_MODEL
    model_name = os.environ.get("FASTEMBED_MODEL", DEFAULT_MODEL)
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
                        help="要构建的游戏，逗号分隔多个或 all")
    args = parser.parse_args()

    game_list = [g.strip() for g in args.game.split(",")]

    for game_key in game_list:
        if game_key == "all":
            for key in GAME_DATA:
                try:
                    build_vectorstore(key)
                except Exception as e:
                    print(f"  ❌ {key} 构建失败: {e}")
                    import traceback
                    traceback.print_exc()
        elif game_key in GAME_DATA:
            build_vectorstore(game_key)
        else:
            print(f"  ❌ 未知游戏: {game_key}")
            print(f"     可用选项: {', '.join(GAME_DATA.keys())}, all")
            sys.exit(1)


if __name__ == "__main__":
    main()
    # 设置 HuggingFace 镜像（模型下载加速）
    from rag_agent.config import HF_ENDPOINT as HF_MIRROR
    os.environ.setdefault("HF_ENDPOINT", HF_MIRROR)
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

