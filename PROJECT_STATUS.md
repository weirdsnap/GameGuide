# GameGuide — 当前项目状态

## 已完成

### 核心问答

- 8 款游戏统一注册与信号词路由。
- LangGraph 外层路由 + ReAct Agent 工具调用。
- FAISS 非结构化检索 + SQLite 精确数据查询。
- 多语言 Embedding 与 FAISS 加载缓存。
- 普通问答、SSE 流式问答和 LLM 未知游戏兜底。

### API 与可靠性

- 无密码公开访问，全局每日 100 问额度并持久化。
- 按客户端 IP 的分钟级和每日限速。
- Caddy 可信代理后的真实客户端 IP 解析。
- 每轮路由、Token、工具调用、耗时和错误日志。
- 非流式 Agent 调用移出 aiohttp 事件循环，默认 120 秒超时。
- 多轮游戏上下文只从当前请求的 `history` 推导，不再使用进程级全局状态。

### 测试

- 离线测试覆盖游戏路由、LangGraph 契约、额度、用量观测、API 处理器、工具契约、术语表匹配和建库元数据契约（119 passed / 20 skipped）。
- 真实 LLM 测试默认跳过，需显式传入 `--run-e2e`。
- 脚本入口有 `--help` 冒烟测试，防止 `sys.path` 顺序回归。
- 路由、检索和轻量端到端评估通过 `tests/test_light.py` 单独运行。

### 数据与评估

- 空洞骑士 golden QA 已起步（`scripts/eval/golden/hollow_knight.jsonl`，9 题，目标 30–50）。
- RAGAS 评估已迁移到 0.4，基线报告本地留存于 `evaluation/reports/`。
- BG3 中英术语表 4238 条（`games/baldurs_gate3/data/baldurs_gate3_glossary.jsonl`）+ 只读探针；尚未接入检索。

## 待办优先级

1. 扩充 golden QA 到 30–50 题并跑出可复现的 RAGAS 基线；基线数字落盘后再改检索。
2. 把 BG3 术语表接入 `search_knowledge_base`，用探针 + golden QA 做 A/B 对比。
3. 为流式请求和底层模型调用补全统一超时与取消策略。
4. 限制历史长度和单请求 Token 成本，明确失败请求的额度策略。
5. 持续补全各游戏数据（泰拉瑞亚配方/NPC、Silksong 向量库重建）。

详细计划见 `ROADMAP.md` 和 `docs/AI_APPLICATION_ENGINEERING_PLAN.md`。

## 技术栈

| 组件 | 方案 |
|:-----|:-----|
| HTTP | aiohttp + Caddy |
| Agent | LangChain + LangGraph ReAct |
| LLM | OpenAI 兼容接口，默认模型由 `CHAT_MODEL` 配置 |
| 向量库 | FAISS |
| Embedding | fastembed + paraphrase-multilingual-MiniLM-L12-v2 |
| 结构化数据 | SQLite |
