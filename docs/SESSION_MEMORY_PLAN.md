# 会话存储与记忆（方案 B）设计

状态：设计待评审，未实现
日期：2026-11-22
作者：Mac 端
前置：`docs/CONTEXT_BUDGET_PLAN.md`（方案 A 裁剪已落地，`f9eaabb`）、
`docs/SPOILER_CONTROL_PLAN.md` §3（进度对象）、`docs/ISSUES_2026-11-22.md`

> **实施看 `docs/SESSION_STORE_IMPL.md`**：DDL、store.py 函数签名、HTTP 契约、
> api_server 改造点、分步上线与测试清单都在那边。本文只回答「为什么这么设计」。

---

## 1. 方案 B 要解决什么

方案 A（trim_history）治的是单轮成本，已上线。剩下的问题：

| 问题 | 现状 | 方案 B 手段 |
|---|---|---|
| 刷新页面 / 换设备即失忆 | 历史完全由前端持有 | 会话收归服务端（session_id） |
| 长会话被窗口裁掉后「忘事」 | 6 轮窗口外的内容直接丢弃 | 旧轮存根（receipt）进上下文 |
| 进度锚点随旧轮被裁掉 | 靠 `history_full` 每轮重扫，历史一久仍可能漏 | 进度对象结构化存储，不依赖文本扫描 |
| 跨轮一致性靠人工抽检 | 同一实体两轮说法冲突无法自动发现（I-11 遗留） | turns 落库后可写会话级 checker |
| golden set 靠手动攒 | 点踩数据（feedback）与 turn 记录分两处 | feedback 按 turn_id 关联导出 |

明确**不做**的：用户账号体系、跨会话记忆（用户画像）、语义检索历史
（把旧会话向量化再召回——收益不明，复杂度高，本轮不碰）。

## 2. 存储：store.py（SQLite）

位置：`data/sessions.db`。约束沿用项目惯例：WAL、busy_timeout、每请求
一个连接（服务器是单进程 aiohttp，SQLite 足够，不上外部 DB）。

```sql
CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,      -- 前端生成的 uuid，localStorage 持久
    created_at   TEXT NOT NULL,
    last_active  TEXT NOT NULL,
    game         TEXT,                  -- 最近确认的游戏键（冗余，便于统计）
    progress     INTEGER,               -- 进度对象：单调不减的整数上界，NULL=未知
    progress_src TEXT,                  -- user | inferred | question
    summary      TEXT,                  -- 远期：会话摘要（§6），初版为 NULL
    summary_upto INTEGER                -- 摘要覆盖到第几轮
);

CREATE TABLE turns (
    turn_id      TEXT PRIMARY KEY,      -- 与现有 turn_record / feedback 的 turn_id 一致
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    seq          INTEGER NOT NULL,      -- 会话内序号
    ts           TEXT NOT NULL,
    question     TEXT NOT NULL,
    answer       TEXT NOT NULL,         -- 完整答案（留存策略见 §7）
    route        TEXT, game TEXT,
    progress_level INTEGER,
    tokens_in    INTEGER, tokens_out INTEGER, tool_calls INTEGER,
    UNIQUE(session_id, seq)
);

-- 存根：旧轮进上下文的压缩形态，确定性生成，不经过 LLM（§4）
CREATE TABLE receipts (
    turn_id      TEXT PRIMARY KEY REFERENCES turns(turn_id),
    conclusion   TEXT,                  -- 一句话结论（§4.2，初版可空）
    sources      TEXT,                  -- JSON 数组：工具返回的来源标题（G4 已规划）
    entry_acts   TEXT,                  -- JSON 数组：条目 act 集合（I-10 已规划）
    entities     TEXT                   -- JSON 数组：glossary 命中的实体（中英）
);

-- 点赞点踩已有 /api/feedback 端点（4b5f720），落库时按 turn_id 关联即可，
-- 表结构由服务器侧实现时定，这里不约束。
```

为什么 receipt 单独一张表而不是 turns 加列：存根是「给模型看的」，
turns 是「事实记录」——职责不同，将来存根格式迭代（加字段、改生成逻辑）
不动事实表。

## 3. 进度对象：存数，不存文

`SPOILER_CONTROL_PLAN` §3.6 已定「中期归服务端」，这里落实：

- `sessions.progress` 是唯一权威进度，**单调不减**（下调只认用户明说
  「重开/新周目」，前端给一个显式重置入口，不靠模型识别）。
- 每轮：`progress = max(sessions.progress, infer_progress(本轮 history 窗口+问题))`，
  写回 sessions。推断逻辑（`progress.py`）不动，但输入从「完整历史」
  变成「窗口内历史 + 存储进度」——**锚点文本被裁掉不再丢进度**，
  这是结构化存储相对 `history_full` 重扫的核心收益。
- `history_full` 这个兜底字段在方案 B 落地后可以删掉（现在留着是因为
  前端历史是唯一数据源，不能丢）。

## 4. 存根（receipt）：旧轮的压缩形态

### 4.1 为什么不是 LLM 摘要

方案讨论（2026-11-22）已定论：自由摘要的三个失败模式——关键信息丢失
无法单测、进度锚点被改写失真、错误结论被固化成事实。存根走**确定性
生成**：答完一轮当场从结构化数据里抽，不经过 LLM，测试可锁。

### 4.2 生成时机与内容

在 `ask()` 返回答案、落 turns 的同一处生成，字段来源全部是现成的：

| 字段 | 来源 | 现状 |
|---|---|---|
| `sources` | 工具返回的 `【参考 i】[EN] 标题` 抽标题 | G4 已规划，未实现 |
| `entry_acts` | 工具返回条目里的 `[act N]` 标注 | I-10 已实现在工具输出里，抽取未做 |
| `entities` | `glossary.expand_query(question)` 的命中列表 | 现成 |
| `conclusion` | 答案首句（句号截断，≤120 字） | 确定性近似，初版就用这个 |
| `progress_level` | 本轮进度对象 | 现成 |

`conclusion` 用「答案首句」是刻意保守：不追求语义完整，只给模型一个
「这轮聊过什么、结论落在哪」的锚。它是唯一可能带错误结论的字段，
缓解办法是 I-11 的 prompt 纪律（不允许先答后更正）+ 存根里同时保留
sources，模型对冲突时可以按来源重新查证而不是盲信存根。

### 4.3 上下文装配（替代现在的 history 直拼）

每轮送进模型的「历史」由三段拼成，总量仍受 CONTEXT_BUDGET_PLAN 的
预算约束（窗口参数可按线上数据再调）：

```
[进度对象]  用户进度 = 第 N 章（来源：sessions 表）
[存根列表]  第 3 轮：问「铁手套」→ 结论…（来源：House of Hope；act 3）
            第 4 轮：问「赫尔希克」→ 结论…
            …（一行一轮，超过预算裁最旧的）
[原文窗口]  最近 4-6 轮的 user/assistant 原文（trim_history 现有逻辑）
```

- 原文窗口可以从 6 轮缩到 3-4 轮：存根接管了长程上下文，原文只保
  「正在进行的讨论」。**等裁剪线上验收数据回来再定具体值**。
- 存根单行 ~60 字，50 轮存根 ≈ 3000 字，比任何原文窗口都便宜一个量级。

## 5. 迁移路径（沿用 CONTEXT_BUDGET_PLAN §4，细化验收）

1. **store.py 只写不读**：落 sessions/turns/receipts，上下文仍走前端
   history + trim_history。验收：线上每轮都有对应行，与 jsonl 对账一致。
2. **session_id 引入**：前端 localStorage 生成 uuid 随请求发上来；
   服务端仍以 history 为准，但开始按 session 累计进度对象。验收：
   `sessions.progress` 与 jsonl 里推断值一致率 > 95%。
3. **服务端权威**：前端停发历史，只发 session_id + 问题；上下文按 §4.3
   装配。验收：刷新页面后续聊，路由/进度/引用上一轮结论全部正确；
   tokens_in 的 p90 不超过方案 A 落地后的水平。

每步独立可回滚：步骤 2 退回是忽略 session_id，步骤 3 退回是前端恢复发历史。

## 6. 远期：会话摘要（明确推后）

`sessions.summary` 字段先留好，但**生成逻辑本轮不实现**。触发条件与形态
等存根上线后看数据再定——如果 50 行存根 + 4 轮原文已经够用，摘要这层
可以永远不做。真要做的那一天，约束也写死：摘要只覆盖存根列表，不覆盖
原文窗口；进度对象不参与摘要（它是结构化字段，本来就丢不了）。

## 7. 隐私与留存（现在就要定，落库即产生责任）

现有 jsonl 落盘只有 gitignore，没有策略；turns 表把答案结构化后责任更明确，
建议一并定：

- turns.answer 保留 **90 天**，过期置空（receipts 保留——它不含原文，
  且是评测/golden set 的骨架）；
- sessions 最后活跃 **180 天**后整行删除；
- turns 不存 IP（IP 只活在日志和限流层，不入库）；
- 不提供用户数据导出/删除接口（无账号体系，session_id 即匿名 id；
  用户清 localStorage 等于丢弃身份）。

**用户口径补充（2026-11-22，I-23）**：「也许保留 3 天」。建议改成三档分层，
数字全部走环境变量，改数不改代码：

| 数据 | 建议留存 | 理由 |
|---|---|---|
| `turns.answer`（答案原文） | **3 天**（用户口径，`RETENTION_ANSWER_DAYS`） | 原文是隐私负担最大的一块，3 天够复盘与抽检 |
| `receipts`（结论/来源/act/实体） | 90 天，不含原文 | 评测与 golden set 的骨架，不保留无隐私风险 |
| `sessions` 行 | 90 天（`RETENTION_SESSION_DAYS`） | 跨周续玩还能认出来，比原建议的 180 天更保守 |

另需补一层**匿名 `client_id`**（localStorage）：session_id 只管一次对话，
client_id 把同一浏览器的多个 session 串起来，刷新后才有「最近会话」可恢复。
cookie 只能做辅助（博客与 API 跨站，属第三方 cookie，Chrome 已默认限制）；
按 IP+UA 弱指纹兜底能救「换浏览器」，但同出口 IP 会串号，默认关闭。
详见 `docs/ISSUES_2026-11-22.md` I-23。

这是一份建议稿，**需要你拍板**——尤其 90/180 天两个数字。

## 8. golden set 导出（顺带的，但价值最高）

turns + feedback + receipts 三表关联后，评测样本可以 SQL 直接出：

```sql
-- 点踩样本：直接成为回归用例候选
SELECT t.question, t.answer, r.sources, r.entry_acts
FROM turns t JOIN feedback f USING (turn_id)
LEFT JOIN receipts r USING (turn_id)
WHERE f.score = -1;
```

EVAL_SYSTEM 的 golden set 扩充（30-50 条目标）从「人工出题」变成
「从真实点踩+抽检里筛」，这是方案 B 最便宜的副产品。

## 9. 验收标准（实现完成后）

1. 契约测试：store 读写、进度单调、存根生成、装配预算（纯函数，秒级）。
2. 迁移步骤 3 后，回放现有 jsonl 长会话（87 轮那条），对比方案 A 的
   tokens_in：p90 不升，且第 80 轮仍能正确引用第 5 轮定下的流派。
3. 线上一天：`sessions.progress` 与 `spoiler_hits` 无回归（进度结构化
   存储不能比文本扫描漏得更多）。
4. 点踩样本 SQL 能导出 ≥ 现有 3 条 feedback 的完整上下文。

## 10. 分工建议

| 部分 | 归属 | 理由 |
|---|---|---|
| store.py + 表结构 + 迁移 | Mac | `scripts/` 与核心链路归 Mac（AGENTS 分工） |
| api_server 接 session_id / 装配切换 | 服务器 | api_server.py 是服务器领地 |
| receipts 生成（sources/entry_acts 抽取） | Mac | 依赖 G4/I-10 的工具侧格式 |
| 前端 session_id + 进度下拉 | 用户/前端 | SPOILER_CONTROL_PLAN §3.3 已定前端字段 |
| 验收回放与线上观测 | 服务器 | 数据在服务器 |
