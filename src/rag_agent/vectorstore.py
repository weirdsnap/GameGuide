"""向量数据库管理：文档切分、embedding、存储、检索。"""
from pathlib import Path
from typing import List, Optional

from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from .config import OPENAI_API_KEY, OPENAI_BASE_URL, EMBEDDING_MODEL, VECTORSTORE_DIR, DATA_DIR
from .data_converter import json_to_documents


def get_embeddings():
    """获取 embedding 模型实例。"""
    try:
        # 优先使用本地 fastembed（轻量，无需 API Key）
        from langchain_community.embeddings import FastEmbedEmbeddings
        print("使用本地 embedding（fastembed）")
        return FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    except ImportError:
        if not OPENAI_API_KEY:
            raise ValueError(
                "未安装 fastembed 且未配置 OPENAI_API_KEY。\n"
                "建议：pip install fastembed\n"
                "或设置 .env 中的 OPENAI_API_KEY"
            )
        print("使用远程 embedding（OpenAI API）")
        return OpenAIEmbeddings(
            model=EMBEDDING_MODEL,
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
        )


def load_documents(data_dir: str = DATA_DIR) -> List[Document]:
    """加载 data 目录下的所有文档（JSON 数据 + 已有 .md/.txt）。"""
    docs: List[Document] = []
    path = Path(data_dir)
    if not path.exists():
        raise FileNotFoundError(f"数据目录不存在：{data_dir}")

    # 1. 加载 JSON 数据（空洞骑士）
    print("加载 JSON 数据（空洞骑士）...")
    json_docs = json_to_documents(data_dir)
    docs.extend([
        Document(page_content=d["text"], metadata=d["metadata"])
        for d in json_docs
    ])
    print(f"  → 加载了 {len(json_docs)} 个 JSON 文档")

    # 2. 加载已有的 .md / .txt 文档（兼容旧格式）
    for file_path in path.rglob("*"):
        if file_path.suffix.lower() in {".md", ".txt"} and "hallownest_knowledge" not in file_path.name:
            text = file_path.read_text(encoding="utf-8")
            docs.append(Document(
                page_content=text,
                metadata={"source": str(file_path.relative_to(path))}
            ))
            print(f"  → 加载了 {file_path.relative_to(path)}")

    return docs


def split_documents(docs: List[Document], chunk_size: int = 500, chunk_overlap: int = 100) -> List[Document]:
    """将文档切分为片段。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_documents(docs)


def build_vectorstore(docs: Optional[List[Document]] = None, save_dir: str = VECTORSTORE_DIR) -> FAISS:
    """在服务器本地构建并保存向量库。"""
    docs = docs or load_documents()
    chunks = split_documents(docs)
    print(f"共 {len(chunks)} 个文本片段，开始生成 embedding...")
    embeddings = get_embeddings()
    vectorstore = FAISS.from_documents(chunks, embeddings)
    Path(save_dir).mkdir(parents=True, exist_ok=True)
    vectorstore.save_local(save_dir)
    print(f"✅ 向量库已保存到：{save_dir}（{len(chunks)} 个片段）")
    return vectorstore


def load_vectorstore(save_dir: str = VECTORSTORE_DIR) -> FAISS:
    """加载已有向量库（封装缓存版本）。"""
    return _load_vectorstore(save_dir)


def get_retriever(save_dir: str = VECTORSTORE_DIR, k: int = 4):
    """获取检索器。"""
    vectorstore = load_vectorstore(save_dir)
    return vectorstore.as_retriever(search_kwargs={"k": k})
from functools import lru_cache
@lru_cache(maxsize=1)
def _load_vectorstore(save_dir: str = VECTORSTORE_DIR) -> FAISS:
    """加载已有向量库（支持离线生成的索引）。\n    使用 @lru_cache 避免多次加载。
    """
    index_path = Path(save_dir) / "index.faiss"
    if not index_path.exists():
        raise FileNotFoundError(f"向量库不存在：{save_dir}")

    print(f"📥 加载向量库：{save_dir}")
    embeddings = get_embeddings()
    vectorstore = FAISS.load_local(save_dir, embeddings, allow_dangerous_deserialization=True)
    print(f"  → {vectorstore.index.ntotal} 个向量")
    return vectorstore


