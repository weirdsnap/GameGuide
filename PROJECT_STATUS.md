# 🏗️ 空洞骑士 RAG Agent — 项目状态档案

> 最后更新：2026-07-01 15:22 (CST)
> 维护者：nanobot 🐈
> **无论从哪个通道（CLI/飞书）操作，先读这个文件了解最新进展！**

---

## 📋 项目概览

基于 LangChain + FAISS 的 RAG 框架，用《空洞骑士》数据搭建问答 Agent。

- **项目路径**：`/data/learning/agent/`
- **虚拟环境**：`/data/learning/agent/.venv/` ✅ 已创建
- **Git 仓库**：❌ 未初始化
- **API Key**：✅ 已配置 DeepSeek（sk-67ee...cc5a）

---

## 🗂️ 数据状态

来自 [HallownestAPI](https://github.com/yassenshopov/HallownestAPI) 的结构化 JSON 数据（CC BY-NC-SA 4.0 许可）：

```
data/
├── areas/             — 53 个文件（区域信息，connectsTo 已嵌入能力锁 ✅）
├── bosses/            — 47 个文件
├── characters/        — 90 个文件
├── charms/            — 45 个文件
├── skills/            — 18 个文件
├── hallownest_knowledge.md — 93KB，253条文档含能力锁的 Markdown 导出 ✅
├── manifest.json            — 数据清单（253条全部导出成功 ✅）
├── requires.json            — ❌ 已删除（能力锁已嵌入 connectsTo）
└── ~~sample_knowledge.md~~      — 旧的样例数据 → 已删除 ✅
```

**总计：253 条结构化数据**，已成功转化为自然语言文档 ✅

---

## 🧩 代码结构

```
src/rag_agent/
├── __init__.py         — 空
├── agent.py            — 33行，Agent 主体（提示词已改为空洞骑士向导风格）
├── config.py           — 22行，配置文件（读取 .env）
├── tools.py            — 35行，知识库检索工具（描述已改为圣巢知识库）
├── vectorstore.py      — 87行，向量库构建/加载/检索（支持 fastembed 本地 / OpenAI 远程）
└── data_converter.py   — 180行，JSON → 自然语言文档转换器

embed_offline.py        — 便携版离线 embedding 脚本（用户本地跑）
main.py                 — 35行，交互式 CLI 入口
```

**代码总量**：392 行

---

## 🚧 待办清单

### P0 — 必须完成
- [ ] **在用户本地机器上运行 `embed_offline.py`**，生成的 `vectorstore/` 复制回服务器
- [ ] 服务器安装 `fastembed`：`source .venv/bin/activate && pip install fastembed`
- [ ] 测试加载向量库并跑一次问答验证
- [x] 删除过时的 `sample_knowledge.md`

### P1 — 建议完成
- [ ] 初始化 Git 仓库并做第一次提交
- [ ] 补充区域连接的能力锁条件（`connectsTo` 目前不含能力锁，能力锁在 `requires.json` 中）
- [ ] 在飞书通道中集成 Agent（替代现在直接调 DS 的方式）

### P2 — 锦上添花
- [ ] 增加跨区域/跨Boss的关联问答能力
- [ ] 数据可视化（区域地图、Boss关系图等）

---

## 📝 操作日志

| 时间 | 操作 | 执行者 |
|:---:|:----|:------:|
| 2026-07-01 11:39 | HallownestAPI 数据导出完成（253条） | nanobot |
| 2026-07-01 11:43 | 项目架子搭建完成（main.py + src/ + scripts/） | nanobot |
| 2026-07-01 12:09 | 创建 PROJECT_STATUS.md | nanobot |
| 2026-07-01 12:20 | 代码改造完成：JSON转换器、空洞骑士提示词、离线embedding脚本 | nanobot |

---

*修改此文件请更新「最后更新」时间和「操作日志」表。*
