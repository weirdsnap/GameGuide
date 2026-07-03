# 基于 LLM API 的 RAG Agent

一个最小可运行的 RAG（检索增强生成）Agent，支持从本地文档构建知识库并回答用户问题。

## 环境准备

```bash
# 激活虚拟环境（已创建）
source .venv/bin/activate

# 配置 API Key
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY
```

## 快速开始

1. **把文档放入知识库**

   将 `.md` 或 `.txt` 文件放入 `data/` 目录。

2. **构建向量库**

   ```bash
   python scripts/ingest.py
   ```

   这会调用 OpenAI Embedding API 对文档切片并生成向量索引，保存到 `vectorstore/`。

3. **运行 Agent**

   ```bash
   python main.py
   ```

   示例问题：
   - "智能客服助手多少钱？"
   - "文档分析助手支持哪些格式？"
   - "售后技术支持时间是什么？"

## 项目结构

```
.
├── data/                 # 知识库原始文档
├── scripts/
│   └── ingest.py         # 文档入库脚本
├── src/rag_agent/
│   ├── config.py         # 配置
│   ├── vectorstore.py    # 向量库管理
│   ├── tools.py          # Agent 工具
│   └── agent.py          # Agent 主逻辑
├── main.py               # 交互入口
├── requirements.txt
└── .env.example
```

## 自定义扩展

- 接入 PDF：使用 `langchain_community.document_loaders.PyPDFLoader`
- 接入网页：使用 `WebBaseLoader`
- 更换 Embedding：在 `config.py` 中修改 `EMBEDDING_MODEL`
- 更换向量库：将 `FAISS` 替换为 `Chroma`
- 接入国产模型：修改 `OPENAI_BASE_URL` 和 `CHAT_MODEL`（兼容 OpenAI API 即可）
