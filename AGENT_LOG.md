# 🐈 项目工作日志

> **跨通道会话记录** — 无论 CLI / 飞书，项目决策和进展统一记在这里。
> 每次会话前先读本文件，完成后更新。

---

## 2026-07-01

### 中午（飞书通道）

- **数据导出完成**：从 HallownestAPI 克隆，Python 脚本导出 253 条结构化 JSON 数据
- **项目架子搭建**：创建 src/rag_agent/、main.py、scripts/ 骨架，包括：
  - `config.py` — 配置（读取 .env）
  - `agent.py` — Agent 主体（提示词：空洞骑士向导）
  - `tools.py` — 知识库检索工具
  - `vectorstore.py` — FAISS 向量库管理（支持远程 OpenAI + 本地 fastembed）
  - `data_converter.py` — JSON → 自然语言转换
  - `embed_offline.py` — 便携版离线 embedding 脚本（用户本地跑 + 复制回服务器）
- **配置 .env**：填入 DeepSeek API Key，指向 `https://api.deepseek.com/v1`
- **决策**：项目工作留档到 `AGENT_LOG.md`，方便跨通道同步

### 下午（CLI 通道）

- **补充能力锁元数据**：为 53 个区域的 `connectsTo` 标注 12 种能力/道具门槛（54 条有门槛连接）
  - `main` → `deepseek-v4-flash`
- **更新 `data_converter.py`**：`area_to_text()` 支持新的 dict 格式 `connectsTo`（含 `需要：...`）
- **同步到单个文件**：将 `_all.json` 的能力锁同步到 53 个单独的区域 JSON 文件
- **重新生成 `hallownest_knowledge.md`**：93KB，253 条文档，含能力锁信息
- **创建本日志文件**

### 傍晚（CLI 通道）

- **清理**：删除了过时的 `data/sample_knowledge.md`
- **更新**：`PROJECT_STATUS.md` 任务状态同步更新

---

## 日志格式

```
## YYYY-MM-DD

### 时段（通道）

- **操作记录**：做了什么
- **决策**：达成的共识
```
