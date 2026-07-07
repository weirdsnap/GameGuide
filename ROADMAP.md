# GameGuide RAG Agent — 路线图

> **当前版本**: beta3 (2026-07-07)
> **主分支**: `master`
> **知识源**: Fandom Wiki (`wiki_data.md`, 416篇, 1.04MB)
> **Git tag**: 待定

---

## 最终目标

让 AI 助手能准确回答《空洞骑士》的游戏知识问题，包括能
力获取、Boss 策略、护符搭配、路线规划等。

---

## 版本演进

| 版本 | 知识源 | 说明 |
|:----:|:------:|------|
| beta1 | HallownestAPI (结构化JSON) | 初期数据源，文档太短(234chars) |
| beta2 | HallownestAPI + Wiki 合并 | LLM多源整合尝试，有幻/丢关联问题 |
| **beta3** | **Fandom Wiki (纯Wiki)** | 直接以Wiki自然语言文档建RAG，当前方案 |

---

## 当前进度

| # | 事项 | 状态 | 说明 |
|---|:----|:----:|:------|
| 1 | **Wiki 数据全面采集** | ✅ 完成 | Fandom Wiki 12个分类 416篇(去重后), 1.04MB |
| 2 | **补抓 POI 页面** | ✅ 完成 | 6篇 Points of Interest (Geo, Hot Spring, Lore Tablets, Shade Gate, Whispering Root) |
| 3 | **去重清理** | ✅ 完成 | 43篇重复移除 |
| 4 | **`load_wiki_documents()`** | ✅ 完成 | 新的文档加载器，直接从 `wiki_data.md` 分块入库 |
| 5 | **FAISS 入库** | 🛠️ 用户本地运行 | `python scripts/ingest.py` 在 Mac 上跑一次 |
| 6 | **回归测试验证** | 📝 待FAISS就绪 | 跑 `tests/test_qa.py` 确保问答质量 |
| 7 | **Agent 调优** | 📝 待做 | 剧透过滤、检索参数调优 |

---

## 方案对比

### beta2（旧方案）— 已弃用
phase2_beta.jsonl → 结构化实体 → 增强提示 → FAISS

- 优点：有剧透等级、关联实体等结构化元数据
- 缺点：LLM 合并产生幻觉、内容碎片化（平均值 234 字符/篇）

### beta3（当前方案）
Fandom Wiki 原始文档 → 自然语言分段 → FAISS

- 优点：文档丰富（平均值 2600 字符/篇）、无LLM交融错误
- 待弥补：无结构化剧透等级（可在检索后加过滤层）

---

## 下步计划

1. 用户在 Mac 本地跑 `python scripts/ingest.py` 生成向量库
2. 结果同步回服务器 `vectorstore/` 目录
3. 跑回归测试验证问答质量
4. 评估是否需要追加 `hollowknight.wiki` 数据
