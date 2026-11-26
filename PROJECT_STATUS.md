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
- 会话存储（I-23）：SQLite turns/sessions 表（WAL），刷新恢复历史、软删除、
  3 天答案/90 天会话读时过期（数据保留不删）。
- turn 占位机制：先落占位行答完再 finish，思考中刷新/断连不丢会话（944fa43）。
- SSE 流式：首字节立即落地 + 静默期 8s 心跳，修移动端 Safari 断连（b54278a）；
  工具阶段 `progress` 事件两端透传（f2fb79f + api_server 转发）。
- 历史裁剪（I-09）：`history_trim.py` 控制单请求 token 成本，观测字段
  （I-26 修复后）随 turn 落库。
- `api.sh` 优雅退出：SIGTERM 后轮询等待在途请求（默认 30s）再强杀（8967f49）。

### 测试

- 离线测试覆盖游戏路由、LangGraph 契约、额度、用量观测、API 处理器、工具契约、
  术语表匹配、建库元数据契约、防剧透提示词契约、会话存储、act 索引、
  双语分数合并等（373 passed / 22 skipped / 1 xfailed，2026-11-26）。
- 真实 LLM 测试默认跳过，需显式传入 `--run-e2e`。
- 脚本入口有 `--help` 冒烟测试，防止 `sys.path` 顺序回归。
- 路由、检索和轻量端到端评估通过 `tests/test_light.py` 单独运行。
- 每次重建向量库后跑 `scripts/eval/retrieval_smoke.py`（12 条真实问法打真实
  检索路径，当前基线 10/12，2 条 MISS 均为数据覆盖缺口）。

### 数据与评估

- 空洞骑士 golden QA 已起步（`scripts/eval/golden/hollow_knight.jsonl`，9 题，目标 30–50）。
- RAGAS 评估已迁移到 0.4，基线报告本地留存于 `evaluation/reports/`。
- BG3 中英术语表 4238 条（`games/baldurs_gate3/data/baldurs_gate3_glossary.jsonl`），
  已接入 `search_knowledge_base`（命中中文专名自动追加英文原名；A/B：影心 top-8 不中 → rank 1）。
- 建库契约：文档必须带 `language` 标签（读端严格模式，漏标即零命中）；
  标题/实体名前置进 chunk（`prepend_title_to_chunks`，防长块稀释实体信号）。
- 双语检索按相似度分数合并（I-28）：`similarity_search_with_score` 两路归并排序，
  不再「en 在前 zh 在后」顺序拼接（c4b290f）。
- refetch_wiki 重抓管线（质量闸门 + 断点续传）：silksong 29MB 垃圾 → 99KB
  干净正文（79 页）；terraria 中文 wiki.gg/zh 直连重抓 3,031 页。
- 建库分页器识别 refetch 的 `---` 页分隔（P0-4）：terraria/silksong/mhw 已重建，
  页内小节继承页面 title，碎片率从 48% 降到 0。
- 防剧透 P0 提示词层：每游戏标注 `spoiler_axis`（act/chapter/ability/none），
  进度即硬上界、别剧透先复述边界；方案全文见 `docs/SPOILER_CONTROL_PLAN.md`。
- 观测层（服务器侧）：turn 记录含 `answer_head`/`spoiler_hits`，按日 JSONL 落盘
  `data/turns/`，分析脚本 `scripts/analysis/turns_report.py`。

## 待办优先级

1. 扩充 golden QA 到 30–50 题并跑出可复现的 RAGAS 基线；基线数字落盘后再改检索。
2. I-29：silksong 库覆盖不足（重抓后 79 页，Silk Soar 等核心条目缺失），
   需确认 fandom 分类抓取范围后补抓。
3. I-28 数据面：给 HK / ONI / VA11 补中文 wiki（HK 中文块仅占 2.4%，无区域页）。
4. I-30：检索 top-k 同页去重或 MMR，提升来源多样性（P3）。
5. 进度轴重构 S1（`docs/PROGRESS_AXIS_DESIGN.md`）：拆配置，等用户拍板开工。
6. I-19：`max_turns=6` 观测 3 天后再决定是否调整（不动参数）。

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
