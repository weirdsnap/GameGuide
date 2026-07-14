"""向量数据库管理：文档切分、embedding、存储、检索。"""
from pathlib import Path
from typing import List, Optional

from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from .config import OPENAI_API_KEY, OPENAI_BASE_URL, EMBEDDING_MODEL, FASTEMBED_MODEL, VECTORSTORE_DIR, DATA_DIR
from .data_converter import json_to_documents


def get_embeddings():
    """获取 embedding 模型实例。"""
    try:
        # 优先使用本地 fastembed（轻量，无需 API Key）
        from langchain_community.embeddings import FastEmbedEmbeddings
        print(f"使用本地 embedding（fastembed: {FASTEMBED_MODEL}）")
        return FastEmbedEmbeddings(model_name=FASTEMBED_MODEL)
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


def load_beta_documents(data_dir: str = DATA_DIR) -> List[Document]:
    """加载 phase2_beta.jsonl（428 实体结构化数据），转为可检索文档文本。

    保留供迁移或对照使用。默认方案已改为 load_wiki_documents()。
    """
    import json
    beta_path = Path(data_dir) / "phase2_beta.jsonl"
    if not beta_path.exists():
        raise FileNotFoundError(f"beta2 数据不存在：{beta_path}")

    docs: List[Document] = []
    with open(beta_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            ent = json.loads(line)
            meta = {
                "entity_key": ent.get("_entity_key", ""),
                "title_cn": ent.get("title", ""),
                "title_en": ent.get("title_en", ""),
                "category": ent.get("category", "unknown"),
                "spoiler_level": ent.get("spoiler_level", "early"),
                "location": ent.get("location", ""),
            }

            parts = [
                f"# {ent.get('title', '')} ({ent.get('title_en', '')})",
                f"分类: {meta['category']}",
                f"位置: {meta['location']}" if meta['location'] else "",
            ]

            if ent.get("summary"):
                parts.append(f"## 简介\n{ent['summary']}")
            if ent.get("description"):
                parts.append(f"## 详情\n{ent['description']}")

            rels = ent.get("related_entities", [])
            if rels:
                rel_lines = ["## 关联"]
                for r in rels:
                    if isinstance(r, dict):
                        rel_lines.append(f"- {r.get('entity', '')} ({r.get('relation', '')})")
                    else:
                        rel_lines.append(f"- {r}")
                parts.append("\n".join(rel_lines))

            text = "\n\n".join(p for p in parts if p)
            docs.append(Document(page_content=text, metadata=meta))

    print(f"  → 加载了 {len(docs)} 个 beta2 实体文档")
    return docs


def _clean_html(text: str) -> str:
    """清理 HTML 标签和实体，返回可读纯文本。"""
    import re
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", "\"")
    text = text.replace("&#39;", "'")
    text = text.replace("&#x27;", "'")
    text = re.sub(r"  +", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_wiki_documents(data_dir: str = DATA_DIR) -> List[Document]:
    """加载 wiki_data.md（417 篇 Fandom Wiki 文档），转为可检索文档。

    文件以 '# 文档：标题' 分隔，每篇含类别/标识/来源元数据。
    这是当前默认的知识源方案。
    """
    import re

    wiki_path = Path(data_dir) / "wiki_data.md"
    if not wiki_path.exists():
        raise FileNotFoundError(f"Wiki 数据不存在：{wiki_path}")

    raw = wiki_path.read_text(encoding="utf-8")

    # 跳过文件头（# 文档 之前的内容，如 '文档总数：' 等统计行）
    first_doc = raw.find("\n# 文档")
    if first_doc == -1:
        print("⚠️ 未找到文档条目")
        return []
    body_start = raw.find("# 文档", first_doc)
    if body_start == -1:
        body_start = first_doc + 1
    raw_body = raw[body_start:]

    chunks = re.split(r"(?=^#\s*文档)", raw_body, flags=re.MULTILINE)

    docs: List[Document] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        # 提取标题
        title_match = re.search(r"# 文档[：:]\s*(.+)", chunk)
        title = title_match.group(1).strip() if title_match else "unknown"

        # 提取元数据行
        cat_match = re.search(r"- 类别[：:]\s*(.+)", chunk)
        category = cat_match.group(1).strip() if cat_match else "unknown"

        slug_match = re.search(r"- 标识[：:]\s*(.+)", chunk)
        slug = slug_match.group(1).strip() if slug_match else ""

        source_match = re.search(r"- 来源[：:]\s*(.+)", chunk)
        source = source_match.group(1).strip() if source_match else ""

        # 去掉元数据行、标题行，保留正文内容
        body = chunk
        body = re.sub(r"^# 文档[：:].*?(?:\n|$)", "", body, count=1, flags=re.MULTILINE)
        body = re.sub(r"^- (类别|标识|来源)[：:].*?(?:\n|$)", "", body, count=3, flags=re.MULTILINE)
        body = body.strip()

        if not body:
            continue

        # 去掉跨文档导航标记（## CATEGORY\n共 N 篇文档）
        body = re.sub(r"## [A-Z]+\s*\n共 \d+ 篇文档", "", body)
        # 去掉孤立的 --- 分隔线
        body = re.sub(r"^---+[\s]*$", "", body, flags=re.MULTILINE)
        body = body.strip()

        # 清理 HTML 标签
        body = _clean_html(body)

        meta = {
            "title": title,
            "category": category,
            "slug": slug,
            "source": source,
        }

        docs.append(Document(page_content=body, metadata=meta))

    print(f"  → 加载了 {len(docs)} 篇 Wiki 文档")
    return docs


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


def split_documents(docs: List[Document], chunk_size: int = 800, chunk_overlap: int = 160) -> List[Document]:
    """将文档切分为片段。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_documents(docs)


def build_vectorstore(
    docs: Optional[List[Document]] = None,
    save_dir: str = VECTORSTORE_DIR,
    use_wiki: bool = True,
    use_beta: bool = False,
) -> FAISS:
    """在服务器本地构建并保存向量库。

    Args:
        docs: 文档列表，不传则自动加载
        save_dir: 保存路径
        use_wiki: True（默认）使用 wiki_data.md（Fandom Wiki 数据）
        use_beta: True 使用 phase2_beta.jsonl（结构化数据，旧方案）
    """
    if docs is None:
        if use_wiki:
            print("📖 加载 Wiki 文档...")
            docs = load_wiki_documents()
        elif use_beta:
            print("📖 加载 beta2 结构化数据...")
            docs = load_beta_documents()
        else:
            print("📖 加载旧 API JSON 数据...")
            docs = load_documents()
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


def get_retriever(save_dir: str = VECTORSTORE_DIR, k: int = 8):
    """获取检索器。"""
    vectorstore = load_vectorstore(save_dir)
    return vectorstore.as_retriever(search_kwargs={"k": k})
from functools import lru_cache
@lru_cache(maxsize=8)
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


