#!/usr/bin/env python3
"""
便携版空洞骑士知识库 Embedding 生成器
======================================
在你本地机器上运行（Mac 或 2080Ti），生成 FAISS 向量索引后复制回服务器。

使用方法：
  1. 将本脚本和 hallownest_knowledge.md 放在同一目录
  2. 安装依赖（只需一次）：
     pip install faiss-cpu sentence-transformers numpy langchain-community langchain-core
  3. 运行：
     python3 embed_offline.py
  4. 将生成的 vectorstore/ 目录复制回服务器

输出： vectorstore/index.faiss + vectorstore/index.pkl（标准 langchain 兼容格式）

说明：
  - 模型使用 BAAI/bge-small-en-v1.5（约300MB，首次运行自动下载）
  - 如果网络不好，第一次请耐心等待模型下载
  - 有 GPU（如 2080Ti）会自动使用 CUDA 加速
"""

import re
import os
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("❌ 需要 numpy：pip install numpy")
    sys.exit(1)

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    print("❌ 需要 sentence-transformers：pip install sentence-transformers")
    sys.exit(1)

try:
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    print("❌ 需要 langchain 相关包：")
    print("   pip install langchain-community langchain-core langchain-text-splitters")
    sys.exit(1)

# ============================================================
# 配置
# ============================================================
MODEL_NAME = "BAAI/bge-small-en-v1.5"
DATA_FILE = "data/hallownest_knowledge.md"    # 传回服务器后放在项目根目录下
OUTPUT_DIR = "vectorstore"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100


def parse_markdown_docs(filepath: str) -> list:
    """从 hallownest_knowledge.md 解析文档。"""
    text = Path(filepath).read_text(encoding="utf-8")
    docs = []

    raw_docs = re.split(r"\n---\n", text)
    for raw in raw_docs:
        raw = raw.strip()
        if not raw:
            continue
        lines = raw.split("\n")

        # 提取标题
        title_match = re.search(r"^#\s*文档\s*\[\d+\]\s*(.*)", lines[0])
        name = title_match.group(1).strip() if title_match else "未知"

        # 提取元数据
        metadata = {"name": name}
        for line in lines[1:]:
            if line.startswith("- 类别："):
                metadata["category"] = line[4:].strip()
            elif line.startswith("- 标识："):
                metadata["slug"] = line[4:].strip()
            elif line.startswith("- 路径："):
                metadata["source"] = line[4:].strip()

        # 提取正文
        body_lines = []
        in_body = False
        for line in lines:
            if line.startswith(("- 类别：", "- 标识：", "- 路径：", "# 文档")):
                continue
            if not in_body:
                if line.strip():
                    in_body = True
                    body_lines.append(line)
            else:
                body_lines.append(line)

        content = "\n".join(body_lines).strip()
        if content:
            docs.append({"content": content, "metadata": metadata})

    return docs


def build_local_embedding():
    """使用 sentence-transformers 构建本地 embedding 函数。"""
    print(f"🧠 加载模型：{MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    print(f"  模型维度：{model.get_sentence_embedding_dimension()}")
    print(f"  设备：{model.device}")

    class LocalEmbeddings:
        """包装成 langchain Embeddings 接口。"""
        def embed_documents(self, texts):
            return model.encode(texts, normalize_embeddings=True).tolist()

        def embed_query(self, text):
            return model.encode([text], normalize_embeddings=True)[0].tolist()

    return LocalEmbeddings()


def main():
    print("=" * 50)
    print("  空洞骑士知识库 — 离线 Embedding 生成")
    print("=" * 50)
    print()

    # 1. 解析文档
    print(f"📖 解析：{DATA_FILE}")
    docs = parse_markdown_docs(DATA_FILE)
    print(f"  共 {len(docs)} 个文档")
    print()

    # 2. 转为 langchain Document
    documents = [
        Document(page_content=d["content"], metadata=d["metadata"])
        for d in docs
    ]

    # 3. 切分
    print(f"✂️ 切分（chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}）...")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(documents)
    print(f"  共 {len(chunks)} 个文本块")
    print()

    # 4. 生成 embedding
    print(f"🔮 生成 embedding...")
    embeddings = build_local_embedding()
    vectorstore = FAISS.from_documents(chunks, embeddings)
    print()

    # 5. 保存
    output_path = Path(OUTPUT_DIR)
    output_path.mkdir(parents=True, exist_ok=True)
    vectorstore.save_local(str(output_path))

    faiss_path = output_path / "index.faiss"
    pkl_path = output_path / "index.pkl"
    print(f"✅ 完成！输出：")
    print(f"  {faiss_path}（{faiss_path.stat().st_size / 1024:.0f} KB）")
    print(f"  {pkl_path}（{pkl_path.stat().st_size / 1024:.0f} KB）")
    print(f"  共 {vectorstore.index.ntotal} 个向量")
    print()
    print(f"📋 复制回服务器：")
    print(f"  scp -r {OUTPUT_DIR}/ user@server:/data/learning/agent/vectorstore/")
    print()
    print(f"  （或在本地压缩后传输）")
    print(f"  tar czf vectorstore.tar.gz {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
