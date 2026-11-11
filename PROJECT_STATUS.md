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

- 离线测试覆盖游戏路由、LangGraph 契约、额度、用量观测和 API 处理器。
- 真实 LLM 测试默认跳过，需显式传入 `--run-e2e`。
- 路由、检索和轻量端到端评估通过 `tests/test_light.py` 单独运行。

## 待办优先级

1. 建立 golden QA 与 RAGAS 基线，用数字指导检索优化。
2. 为流式请求和底层模型调用补全统一超时与取消策略。
3. 限制历史长度和单请求 Token 成本，明确失败请求的额度策略。
4. 持续补全各游戏数据与中英术语映射。

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
