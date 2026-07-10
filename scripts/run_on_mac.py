#!/usr/bin/env python3
"""
🖥️ Mac 本地运行脚本 — 构建所有游戏的向量库（FAISS）

服务器 CPU/RAM 不足（3.6GB），无法跑 fastembed + FAISS。
请在你的 MacBook 上运行此脚本，然后将生成的 vectorstore 目录传回服务器。

用法：
    cd /data/learning/agent
    python3 scripts/run_on_mac.py --game all          ← 构建所有游戏
    python3 scripts/run_on_mac.py --game oni           ← 只构建某个游戏
    python3 scripts/run_on_mac.py --game terraria
    python3 scripts/run_on_mac.py --game silksong
    python3 scripts/run_on_mac.py --game hollow_knight

前置条件：
    pip install -r requirements.txt              # 安装 fastembed 等依赖
    # 如果在大陆，可能需要设置 huggingface 镜像：
    # export HF_ENDPOINT=https://hf-mirror.com

输出目录：
    games/{game}/vectorstore/                     ← 每个游戏独立的向量库
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAMES_DIR = PROJECT_ROOT / "games"

GAME_DATA: Dict[str, Dict[str, str]] = {
    "hollow_knight": {
        "name": "Hollow Knight (空洞骑士)",
        "data_path": str(PROJECT_ROOT / "data" / "wiki_data.md"),
        "vectorstore_dir": str(PROJECT_ROOT / "vectorstore"),
    },
    "oni": {
        "name": "Oxygen Not Included (缺氧)",
        "data_path": str(GAMES_DIR / "oni" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "oni" / "vectorstore"),
    },
    "terraria": {
        "name": "Terraria (泰拉瑞亚)",
        "data_path": str(GAMES_DIR / "terraria" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "terraria" / "vectorstore"),
    },
    "silksong": {
        "name": "Hollow Knight: Silksong (丝之歌)",
        "data_path": str(GAMES_DIR / "silksong" / "data" / "wiki_data.md"),
        "vectorstore_dir": str(GAMES_DIR / "silksong" / "vectorstore"),
    },
}


def load_wiki_documents(filepath: str) -> List[Dict]:
    """从 wiki_data.md 加载文档列表。"""
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
        if not chunk or not chunk.startswith("# 文档"):
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

    return docs


def build_vectorstore(game_key: str):
    import os
    cfg = GAME_DATA[game_key]
    print(f"\n{'='*50}")
    print(f"📦 {cfg['name']}")
    print(f"{'='*50}")

    # 1. 加载文档
    docs = load_wiki_documents(cfg["data_path"])
    if not docs:
        print("  ❌ 没有文档可处理")
        return
    total_chars = sum(len(d["content"]) for d in docs)
    print(f"  📄 {len(docs)} 篇文档, ~{total_chars:,} 字符")

    # 2. 导入
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document

    # 3. Embedding 模型
    model_name = os.environ.get("FASTEMBED_MODEL", "BAAI/bge-small-en-v1.5")
    print(f"  🧠 {model_name}")
    embed_model = FastEmbedEmbeddings(model_name=model_name)

    # 4. 分块
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=160,
        separators=["\n## ", "\n# ", "\n\n", "\n", ". ", " ", ""],
    )
    documents = [
        Document(page_content=d["content"], metadata=d["metadata"]) for d in docs
    ]
    chunks = splitter.split_documents(documents)
    print(f"  🧩 {len(chunks)} 个分块")

    if not chunks:
        print("  ❌ 无分块结果")
        return

    # 5. 构建 FAISS
    out_dir = Path(cfg["vectorstore_dir"])
    print(f"  🔨 构建 FAISS 索引...")
    vectorstore = FAISS.from_documents(chunks, embed_model)

    # 6. 保存
    out_dir.mkdir(parents=True, exist_ok=True)
    vectorstore.save_local(str(out_dir))
    print(f"  ✅ 已保存: {out_dir} ({vectorstore.index.ntotal} 向量)")

    # 7. 验证
    vs2 = FAISS.load_local(str(out_dir), embed_model, allow_dangerous_deserialization=True)
    print(f"  ✅ 验证通过: {vs2.index.ntotal} 向量")


def main():
    parser = argparse.ArgumentParser(description="Mac 本地向量库构建")
    parser.add_argument("--game", "-g", required=True,
                        choices=list(GAME_DATA.keys()) + ["all"],
                        help="要构建的游戏")
    args = parser.parse_args()

    if args.game == "all":
        for key in GAME_DATA:
            build_vectorstore(key)
    else:
        build_vectorstore(args.game)

    print(f"\n🎉 全部完成！")
    print("请将 vectorstore 目录传回服务器：")
    print("  scp -r games/oni/vectorstore/ snap@114.132.189.56:/data/learning/agent/games/oni/")
    print("  scp -r games/terraria/vectorstore/ snap@114.132.189.56:/data/learning/agent/games/terraria/")
    print("  scp -r games/silksong/vectorstore/ snap@114.132.189.56:/data/learning/agent/games/silksong/")
    print("  scp -r vectorstore/ snap@114.132.189.56:/data/learning/agent/vectorstore/   (如果重建 HK)")
    print()
    print("覆盖后重启服务器即可生效。")


if __name__ == "__main__":
    main()
