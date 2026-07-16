# 💡 项目配置记忆 — 改配置前先看这里

## ⚠️ Embedding 模型：必须用多语言！

**当前默认**：`paraphrase-multilingual-MiniLM-L12-v2`

**为什么**：bge-small-en-v1.5 是纯英文模型，中文 query 几乎检索不到任何内容。

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
