# 💡 项目配置记忆 — 改配置前先看这里

## ⚠️ Embedding 模型：必须用多语言！

**当前默认**：`paraphrase-multilingual-MiniLM-L12-v2`

**为什么**：bge-small-en-v1.5 是纯英文模型，中文 query 几乎检索不到任何内容。

**血泪史**（别再犯了）：
1. ✅ `1245c6e` — 第一次切到多语言模型，功臣
2. ❌ `99213ca` — beta4 重构重写了 config.py，**覆盖回了** bge-small-en-v1.5
3. ✅ 现在 — 又切回来了，别再丢了

**改默认值**：改 `src/rag_agent/config.py` 的 `FASTEMBED_MODEL`
**换模型**：改完要在 Mac 上重跑 `python3 scripts/ingest_game.py --game all`
**部署**：scp vectorstore 目录到服务器

## 快速参考

| 操作 | 命令 |
|------|------|
| 全量重构建库 | `python3 scripts/ingest_game.py --game all` |
| 部署到服务器 | `bash scripts/deploy_vectorstores.sh` |
| 启动 API | `bash scripts/start_api.sh` |
