# 💡 项目配置记忆 — 改配置前先看这里

## ⚠️ Embedding 模型：必须用多语言！

**当前默认**：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`（384维，fastembed 原生支持）

**选择原因**：
1. **多语言语义空间** — 维基内容是英文，用户用中文提问。这个模型支持 50+ 语言，中文 query 能和英文文档向量在同一个空间匹配。
2. **维度 384** — 和 bge-small-en-v1.5 一致，检索速度/存储开销不变。
3. **轻量快速** — MiniLM 架构，Mac 上 300MB 模型，推理速度和 bge 系列接近。
4. **fastembed 开箱即用** — `FastEmbedEmbeddings(model_name=...)` 直接支持。

**反面教训**：之前用 bge-small-en-v1.5（纯英文模型），中文问"螳螂爪在哪里"检索几乎不命中任何英文文档，RAG 等于没有。

**血泪史**（别再犯了）：
1. ✅ `1245c6e` — 第一次切到多语言模型，功臣
2. ❌ `99213ca` — beta4 重构重写了 config.py，**覆盖回了** bge-small-en-v1.5
3. ✅ 现在 — 又切回来了，别再丢了

**改默认值的地方（全部都要改！否则构建向量库时仍会用旧模型）：**
- `src/rag_agent/config.py` — `FASTEMBED_MODEL`（核心配置）
- `scripts/ingest_game.py:181` — `os.environ.get("FASTEMBED_MODEL", DEFAULT_MODEL)` ✅ 已修复
- `scripts/run_on_mac.py:329` — `os.environ.get("FASTEMBED_MODEL", DEFAULT_MODEL)` ✅ 已修复

**切模型后必须全量重构建库**，否则服务器端的查询 embedding 和存储的文档 embedding 空间不一致，检索结果是垃圾。

## ⚙️ 迁移步骤（换模型时）

1. **Mac 上** `git pull`
2. **Mac 上** `python3 scripts/ingest_game.py --game all`
3. **Mac 上** `bash scripts/deploy_vectorstores.sh`（scp 到服务器）
4. **服务器** `bash scripts/start_api.sh`（重启 API，加载新配置和新向量库）

## 🐛 已知 bug / 踩坑记录

- **切换 embedding 模型会静默产生垃圾结果**：FAISS 不校验 query embedding 与 document embedding 是否来自同一模型。两个 384d 向量只要维度相同就能检索，但语义空间不同，结果毫无意义。**千万别在向量库未重建的情况下切换模型！**
- **config.py 被覆盖过**：beta4 重构（99213ca）重写了 config.py，把多语言模型换回了英文。所有改配置的操作都要 git add 确保提交了。

## 快速参考

| 操作 | 命令 |
|------|------|
| 全量重构建库 | `python3 scripts/ingest_game.py --game all` |
| 部署到服务器 | `bash scripts/deploy_vectorstores.sh` |
| 启动 API | `bash scripts/start_api.sh` |
