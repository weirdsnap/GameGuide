# GameGuide · AI 应用工程深化计划

> 创建: 2026-09-04
> 基线版本: beta5 (2026-07-17)
> 目标: 把 GameGuide 从"功能可用的 RAG Agent"升级为"可评测、可观测、可讲清楚的生产级 AI 应用工程样板"
> 双重用途: ① 项目本身质量升级 ② 社招求职的代表作 + 博客 AI Agent 系列素材

---

## 背景与动机

GameGuide 已具备: 7 款游戏、LangGraph ReAct Agent、双通道检索（FAISS 向量 + SQLite 结构化）、
Bilingual Embedding、剧透分级管理、API Server。

但对照 AI 应用工程岗位的 JD 高频词（评测意识 Recall@K/MRR、重排、Agent 工具设计、trace、成本优化），
还有四块硬伤:

1. **没有评测基线** — `scripts/eval/ragas.py` 写好了没跑过，检索质量没有数字
2. **双通道未融合** — FAISS 与 SQLite 结果各自喂 LLM，没有 RRF 融合 + 重排，多阶段 RAG 只做了半程
3. **没有查询理解层** — 问题不分类、不改写，剧透过滤只停留在 Prompt 层
4. **工程化外壳不完整** — 无流式、无 trace、无缓存、无超时兜底

本计划按"先评测、再优化、每步可量化"的顺序推进，每阶段交付代码 + 数据 + 一篇博客。

---

## 执行原则

1. **评测先行**: 任何改动前先有 golden set 和基线数字，改完必须能对比
2. **服务器资源红线**: 服务器 2 核 / 3.6GB 内存，embedding 构建、重排模型跑批一律在 Mac M3 本地完成，
   服务器只承载查询与 API（沿用现有惯例）
3. **聚焦单点突破再横向推广**: 深化阶段聚焦 1 款数据最全的游戏（空洞骑士）做透，
   方法论验证后再看是否推广到其它游戏
4. **每阶段必须产出博客文章**，格式遵循博客仓库 AI Agent 系列（ch10 起），数据公开

---

## Phase 1 — 评测基线（先有数字）

现状: `scripts/eval/ragas.py`（Tier 2 RAGAS，标注在 Mac M3 上跑）已存在，从未运行出报告。

任务:
- [ ] 在 Mac M3 本地跑通 `python scripts/eval/ragas.py --game hollow_knight`，修掉运行期报错
- [ ] 建立 golden QA 集（聚焦空洞骑士，30–50 条，覆盖: 位置查询 / 护符属性 / Boss 打法 / 配方 /
      带剧透等级的问题），存为 `scripts/eval/golden/hollow_knight.jsonl`
- [ ] 跑出基线报告: context precision / context recall / faithfulness / answer relevancy，
      落盘 `evaluation/reports/baseline_hk_{date}.json`
- [ ] 把 baseline 数字写进 README 或 evaluation 目录索引，作为后续所有改动的对照锚点

验收: hollow_knight 有一份完整可复现的基线报告，golden set 进 git。

博客产出: ch10 · 为什么 RAG 项目要先建评测集（golden set 设计 + 指标怎么读）

---

## Phase 2 — 检索融合升级（多阶段 RAG 补完）

现状: 双通道各自喂 LLM（记忆中的升级路径: FAISS + SQLite → 需 RRF 融合 + cross-encoder 重排后统一进 prompt）。
仓库当前没有任何 EnsembleRetriever / RRF / cross-encoder 实现。

任务:
- [ ] 设计融合检索层: 向量召回 top-k1 + SQLite 结构化命中 → **RRF 融合** → **cross-encoder 重排** → 统一 context
- [ ] RRF 实现（手写或 LangChain EnsembleRetriever，注意服务器资源约束）:
      验证哪条路更适合本项目的数据结构（结构化命中是精确匹配，向量是语义召回，权重策略不同）
- [ ] cross-encoder 重排: 选小型 reranker（如 bge-reranker-base 类）在 Mac 本地跑批/评估；
      若服务器内存不允许在线重排，明确降级方案（如只对 top-n 重排 / 量化模型 / 缓存重排结果）
- [ ] 改动落在 `src/rag_agent/` 检索相关模块，保持 LangGraph 图结构不变（只换 retriever 内部实现）
- [ ] 同一 golden set 复跑评测，输出新旧对比（如 context recall 提升 X%）

验收: 融合检索在 golden set 上指标不低于旧版，且对比数字有记录。

博客产出: ch11 · 从双通道到多阶段 RAG——RRF 与重排在本项目的落地（含对比数据）

---

## Phase 3 — 查询理解层

现状: 剧透过滤在 Prompt 层，无查询分类/改写，中文与英文查询混走同一条检索。

任务:
- [ ] 查询分类器: 判定问题类型（位置/物品属性/Boss 攻略/配方合成/剧情进度/闲聊），路由到不同检索策略
- [ ] 查询改写: 同义扩展 + 中英混合查询归一（复用已有 Bilingual 能力），对 recall 差的问题做改写再检索
- [ ] 剧透过滤升级: 从 Prompt 层升级为"检索策略路由"——根据用户已报进度决定检索范围（沿用 4 级剧透分级数据）
- [ ] 评估: 分类准确率（golden set 标注类型）+ 改写前后 recall 对比

验收: golden set 上查询类型分类准确率达标（目标 ≥90%），改写对低 recall 问题的提升可量化。

博客产出: ch12 · 查询理解层——分类、改写与剧透路由（检索前的最后一公里）

---

## Phase 4 — Agent 工程化深度

现状: LangGraph ReAct 一期已图化（refactor/graph.py + tools.py），工具与回退仍粗糙。

任务:
- [ ] 工具 schema 审计: 逐个检查工具的描述/参数设计（工具描述写得好不好直接决定调用准确率），
      用 golden 多轮对话验证
- [ ] 失败回退: 工具报错 → 重试 → 换策略 → 诚实告知，禁止幻觉编造；超时兜底（对齐 ROADMAP P2 条目）
- [ ] 记忆分层: 短期上下文 / 会话摘要压缩 / 可选长期记忆（向量 + 结构化混合），对齐"对话记忆维护"现有实现
- [ ] 多轮复杂任务验证: 造一组"需要跨工具/多步"的任务集（如"泰拉瑞亚怎么做 xx + 需要哪些前置材料"），
      记录成功率
- [ ] （可选探索）MCP: 把 1 个工具包成 MCP server，评估对工具管理的好处

验收: 多步任务成功率有基线数字；工具调用失败不再裸奔（有回退路径记录）。

博客产出: ch13 · Agent 不是调框架——工具 schema、回退与记忆的工程细节

---

## Phase 5 — 生产化外壳

现状: ROADMAP P2 已列"LangGraph 超时兜底 / 服务器模型缓存 / API 流式响应"，均未做。

任务:
- [ ] SSE 流式响应（FastAPI StreamingResponse）
- [ ] 语义缓存 / 结果缓存: 相同或近似查询命中缓存，降低 LLM 调用成本
- [ ] 可观测性: 每轮对话输出结构化 trace（路由选择 → 检索了什么 → 召回了什么 → 最终答案），
      能回放"为什么答成这样"（自写轻量方案或 langfuse）
- [ ] 模型路由意识: 简单问题走小/快模型、复杂走强模型（可作为实验，不强制上线）
- [ ] 超时与重试兜底（对齐 ROADMAP P2）

验收: 一次完整对话能从 trace 还原每一跳决策；流式可用；缓存命中率有记录。

博客产出: ch14 · 生产级 AI 应用——流式、缓存与可观测性

---

## Phase 6 — 收尾沉淀

任务:
- [ ] 版本收口: 全部改动回归通过 `tests/test_light.py`，更新 README 架构说明，
      按三层版本体系（VERSION / ROADMAP.md / data 指标）升 beta6，git tag
- [ ] 最终 benchmark 总表: 各阶段指标对比汇总成一份可对外展示的成绩单
- [ ] 简历叙事整合: 把项目故事线压缩成"从检索到 Agent 到评测的完整闭环 + 全部公开可验证"
- [ ] 博客系列收尾: ch10–ch14 汇总一篇"GameGuide 升级全记录"或直接依靠系列文章本身

验收: beta6 发布，git tag，系列文章全部上线且互相"相关阅读"互链。

---

## 阶段依赖与顺序

```
Phase 1 评测基线 ──→ Phase 2 检索融合 ──→ Phase 3 查询理解
                                              │
Phase 4 Agent 深度的前提: 1 的评测集 + 3 的任务集
Phase 5 生产化外壳: 可在 2/3 之间并行起步，但 trace 设计最好在 3 之后一次到位
Phase 6 收尾: 全部完成后
```

建议节奏: Phase 1 先跑起来（RAGAS 依赖在 Mac M3，需安装 ragas/datasets）；
Phase 2–5 每阶段独立可交付，博客随阶段发，不强求一口气做完。

---

## 与 ROADMAP.md 的关系

本计划是 ROADMAP.md "下步计划"的深化执行文档:
- P0 的 "RAGAS 评估" = 本计划 Phase 1
- P0 的 "Bilingual 检索优化" 与 P2 工程条目被吸收进 Phase 2/5
- 待 Phase 6 收口时，把 ROADMAP.md 的勾选项与版本号统一更新到 beta6
