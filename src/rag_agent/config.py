"""项目配置。"""
import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None

# 默认使用 OpenAI 的 embedding 模型（远程用）
# 本地 fastembed 模型由 FASTEMBED_MODEL 指定（默认多语言模型，支持中英文检索）
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# 本地 fastembed 模型（使用多语言模型，支持中文查询检索英文文档）
# 如果更换模型，需要在 Mac 上重新构建向量库（重跑 ingest_game.py）
FASTEMBED_MODEL = os.getenv("FASTEMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

# HuggingFace 镜像（国内加速下载）
HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")

# 默认对话模型
CHAT_MODEL = os.getenv("CHAT_MODEL", "deepseek-v4-flash")

# 向量库路径
VECTORSTORE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "games", "hollow_knight", "vectorstore")
VECTORSTORE_DIR = os.path.abspath(VECTORSTORE_DIR)

# 文档目录
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DATA_DIR = os.path.abspath(DATA_DIR)

# LLM 配置（用于 Agent 对话模型）
LLM_CONFIG = {
    "model": CHAT_MODEL,
    "api_key": OPENAI_API_KEY,
    "base_url": OPENAI_BASE_URL,
    "temperature": 0.2,
    "max_tokens": 4096,
}
