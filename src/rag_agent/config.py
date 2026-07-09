"""项目配置。"""
import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None

# 默认使用 OpenAI 的 embedding 模型（远程用）
# 本地 fastembed 模型由 FASTEMBED_MODEL 指定（默认 bge-small-en-v1.5）
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# 本地 fastembed 模型（支持多语言，如 paraphrase-multilingual-MiniLM-L12-v2）
FASTEMBED_MODEL = os.getenv("FASTEMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

# 默认对话模型
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")

# 向量库路径
VECTORSTORE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "vectorstore")
VECTORSTORE_DIR = os.path.abspath(VECTORSTORE_DIR)

# 文档目录
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DATA_DIR = os.path.abspath(DATA_DIR)
