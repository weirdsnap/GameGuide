# GameGuide RAG Agent — 路线图

> 当前基线：beta5 之后的 `main` 分支
>
> 服务形态：aiohttp API + Caddy 反向代理
>
> 游戏注册数：8

## 当前能力

- 多游戏信号词路由，统一 LangGraph ReAct Agent。
- FAISS Wiki 检索与 SQLite 结构化查询双通道。
- 多语言 Embedding，支持中文查询检索英文文档。
- 中文回答、专名格式和 Prompt 层剧透控制。
- 调用方传入 `history` 的多轮对话，不共享跨请求游戏状态。
- 无密码公开访问、全局每日 100 问额度、按真实客户端 IP 限速。
- 可信代理后的 `X-Forwarded-For` 解析，避免把全站访客识别成 localhost。
- 普通问答与 SSE 流式问答。
- 每轮路由、游戏、Token、工具调用和耗时的结构化日志。
- 非流式请求在线程中执行，并提供默认 120 秒超时。

## 测试与评估现状

- 默认 `pytest tests` 覆盖路由、图契约、额度、观测和 API 处理器，不调用真实 LLM。
- `tests/test_qa.py --run-e2e` 提供真实模型回归。
- `tests/test_light.py` 独立运行路由、检索 Hit@K 和轻量端到端评估。
- RAGAS 基线已跑通（`scripts/eval/ragas_eval.py`，报告本地留存），golden QA 集建设中。

## 下一步

### P0 — 核心质量

- [ ] 建立空洞骑士 golden QA 集并运行 RAGAS 基线。（进行中：RAGAS 基线已跑通，报告本地留存于 evaluation/reports/；golden 集已起步，`scripts/eval/golden/hollow_knight.jsonl` 9 题）
- [ ] 为 Silksong 重建并验证最新向量库。（wiki_data.md 已修复：29MB / 196 页，需在 Mac 运行 ingest_game.py）
- [ ] 量化中英文检索的 Recall@K / Hit@K，再调整融合和排序策略。

### P1 — 数据与回答质量

- [ ] 补全泰拉瑞亚合成配方与 NPC 数据。
- [ ] 建立跨游戏中英术语映射，减少专名查询失败。
- [ ] 增量更新 Silksong 等仍在变化的游戏资料。
- [ ] 增加跨工具、多步骤问题的端到端评估。

### P2 — 工程可靠性

- [ ] 为 SSE 和底层模型调用补充统一 deadline 与取消策略。
- [ ] 明确菜单、模型失败和客户端断连时的额度记账策略。
- [ ] 限制历史消息长度与请求体复杂度，控制单请求 Token 成本。
- [ ] 完善模型缓存、缓存命中和失败回退观测。

### P3 — 产品与扩展

- [ ] 前端聊天 UI 与更多机器人集成。
- [ ] 新游戏接入。
- [ ] 用户积分系统集成。

## 版本历史

| 版本 | 日期 | 说明 |
|:----|:-----|:-----|
| beta1 | 2026-03 | 空洞骑士单体 RAG |
| beta2 | 2026-07-07 | 多源整合与 LLM 合并 |
| beta3 | 2026-07-10 | 纯 Fandom Wiki 单游戏 RAG |
| beta4 | 2026-07-16 | 多游戏架构与双通道检索 |
| beta5 | 2026-07-17 | 多语言 Embedding、API 与代码整理 |
| main 后续 | 2026-11 | 全局额度、逐轮观测、请求状态隔离与代理限流修正 |
