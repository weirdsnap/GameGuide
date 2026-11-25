# 线上体检 2026-11-25（数据侧）

> 采集范围：`data/turns/*.jsonl`（11-12～11-25，非本机轮次 275）、`api_server.log`
> （11-22 22:54 之后 42 轮带 httpx 明细）、`data/sessions.db`（48 轮）。
> 结论都可复算，脚本片段见各节。

## 0. 摘要

1. 11-25 那次断连不是孤立故障：它连带毁掉后面两轮，用户实际花了 45 秒 + 1 次额度才拿到答案。
2. 「首字之前」的等待有实测分布：p50 9s / p90 20s / max 50s。心跳 10s 够用，但没有进度语义仍然是硬伤。
3. **四个观测字段一直是 0/NULL**（`history_raw_msgs`/`history_used_msgs`/`history_trim_chars`/`progress_source`），根因是 LangGraph 按 TypedDict schema 裁掉了未声明的 key。上下文裁剪的效果此前只能靠 tokens_in 间接看。
4. 会话存储双写从 11-22 22:00 起 48 轮**零字段差异**，已达「先把开关打开」的条件。
5. 前端每轮平均 493 次全量 markdown 重渲染（最多 1266 次），是移动端卡顿的直接来源之一。

## 1. 断连的完整伤害链（11-25）

| 时刻 | 事件 | 数据 |
|---|---|---|
| 11:56:15 | 用户问「拿到法杖了，怎么救奥斯卡」 | 7 次 LLM，跨度 22s |
| 11:56:40 | 客户端断开，响应体 **0 字节** | `200 0`，turn 落 `route=null` `tokens=0` |
| 12:01:02 | 用户改问「第三章的」（4 个字） | 21.1s，答案却在讲**奥斯卡** |
| 12:10 | 用户重问完整问题 | 22.4s，才拿到正确答案 |

中间那轮答非所问的原因：前端断连时把空的 assistant 消息删了，但 user 消息留着
（`catch` 分支只 filter 掉 assistantId）。于是下一轮的历史里出现一条"用户问了、没人答"的
提问，模型顺手把它补答了，用户看到的是"我明明问第三章，怎么又讲奥斯卡"。

净损失：1 次额度、45 秒、两次困惑。**这就是断连比"慢一点"更贵的原因**——
它污染了后续上下文，而不只是少显示一段文字。

顺带说明：断连轮 `answer` 为空，`save_turn` 不落库（`if route not in (...) and answer_text`），
所以 SQLite 里查不到这一轮，只有 jsonl 有。会话恢复时它也不出现，用户无从知道自己漏了一轮。

## 2. 「首字之前」到底等多久（实测）

日志里每轮有完整的 httpx 时间戳序列，最后一次 LLM 调用必然早于首个答案 token，
所以「首次 LLM → 末次 LLM」的跨度是静默期的可靠上界估计。

| 指标 | p50 | p90 | max |
|---|---|---|---|
| 每轮 LLM 调用次数 | 4 | 6 | 9 |
| 工具循环跨度（首→末 LLM） | 9.0s | 20.0s | 50.0s |

最长的三轮：11-24 15:26（9 次 LLM / 50s / 65.4s 总耗时）、11-24 15:29（8 次 / 29s）、
11-25 00:00（5 次 / 22s）。

两个推论：
- `STREAM_UX_PLAN` 里定的 10s 心跳间隔是合适的：p90 能发到 2 次，最坏样本能发到 5 次。
- 但**p50 就有 9 秒没有任何反馈**，p90 是 20 秒。光有心跳（用户看不见）解决不了观感，
  `progress` 事件才是主菜。

## 3. 【新】四个观测字段从未生效

`turns` 里这几列统计出来是：

```
history_raw_msgs / history_used_msgs / history_trim_chars  全为 0（48/48 轮）
progress_source                                            全为 NULL
```

根因：`multi_agent._build_initial_state()` 把四个值放进了初始 state，但
`graph.AgentState`（TypedDict）里**没有声明这四个字段**，LangGraph 按 schema 建 channel，
未声明的 key 在图跑完时被丢弃，`_meta_from_state()` 的 `state.get(...)` 只能拿到默认 0。

实证（menu 路径，不调 LLM）：

```
初始 state: {'history_raw_msgs': 2, 'history_used_msgs': 2, 'history_trim_chars': 0, ...}
图终态 keys: ['answer','detected_game','history','history_full','last_game',
              'last_game_confirmed','model_name','prompt','question','usage','verbose']
```

影响：
- 上下文裁剪（I-09）的效果从来没有被这三个字段记录过，此前只能靠 `tokens_in` 间接判断
  （p90 从 210k 降到 37k 是真的，但「这一轮到底裁掉多少」没数）。
- `progress_source` 恒 NULL，排查"模型为什么认为用户在第二章"时少一只眼睛——
  看不出进度是历史锚点推的、信号词推的，还是默认值。
- `sessions.progress_src` 因此全为 NULL（跟着一起空）。

修法（Mac 侧，`src/rag_agent/graph.py` 一行级改动）：`AgentState` 补四个字段声明
`history_raw_msgs: int`、`history_used_msgs: int`、`history_trim_chars: int`、`progress_source: Optional[str]`。
服务器侧补一条回归测试：跑 menu 路径断言终态仍带这四个 key（防再被裁掉）。

## 4. 会话存储：已达切换条件

对账口径：jsonl（11-22 22:00 之后）vs `sessions.db`。

| 项 | 结果 |
|---|---|
| 双写起始 | 2026-11-22 22:00:43（部署时刻） |
| 双写轮次 | 48（11-22 12 / 11-23 3 / 11-24 18 / 11-25 15） |
| 字段差异 | **0**（route / game / tokens_in / tool_calls 全一致） |
| 只在 jsonl | 1 轮（11-25 11:56 断连，answer 为空故不落库，属预期） |
| 只在 SQLite | 0 |

按 I-23 定的上线条件（"SQL 对账 1–2 天无差后置 `GAMEGUIDE_SERVER_HISTORY=1`"），
条件在 11-24 就该满足了，现在跑了 3 天。

切之前要知道的三件事：
1. 切换后 `assemble_history` 用 `store.session_turns(limit_turns=6, max_chars=6000)`，
   与 `trim_history` 的默认一致，**上下文规模不会变**，前端发的 history 被忽略
   （前端分批渲染仍是有效的显示层优化，但不再影响上下文）。
2. `session_turns` 只取 `answer IS NOT NULL` 的轮次，3 天保留期一过 answer 变 NULL，
   那一轮**问题也不会进上下文**（比前端方案更激进，会更快"失忆"）。
3. 断连轮因为不落库，服务端装配时也看不到——这一条和现状一致，不是新增问题。

建议：先开 `SERVER_HISTORY=1` 观察 2 天，对比 `tokens_in` 分布与"答非所问"反馈，
再决定要不要把 `SESSION_MAX_TURNS` 提到 10。

## 5. 上下文规模（trim 生效后的现状）

| 窗口 | tokens_in 中位 | p90 | max |
|---|---|---|---|
| 11-15（trim 前） | 28,917 | — | 128,916 |
| 11-16（trim 前） | 78,920 | — | 249,784 |
| 11-22（trim 后，含旧进程污染） | 19,318 | — | 290,355 |
| **11-24～11-25（当前进程）** | 27,659 / 29,201 | 45,000 | 85,810 |

单轮基线稳定在 30k 上下，超出预算线（40k）的轮次集中在「工具调用 > 10 次」的长尾，
不是历史长度导致的。也就是说**再压历史收益很小，短轮次的成本在系统提示词 + 工具返回**。

`history_msgs` p50 29 / p90 135 / max 179，说明前端仍会发超长历史（135 条 = 67 轮），
只是被服务端裁掉了。开启 `SERVER_HISTORY=1` 后这个字段会掉到 ~12，可用来验证切换成功。

## 6. 前端渲染开销

| 指标 | p50 | p90 | max |
|---|---|---|---|
| 每轮 SSE chunk 数 | 482 | 710 | 1266 |
| 累计 chunk（275 轮） | — | — | 132,142 |

前端每个 `token` 事件都走一遍 `marked.parse(全文) + DOMPurify.sanitize(全文) + v-html 重设`，
全文平均 1.6k 字符。单轮 482 次 × 1.6k ≈ 77 万字符的解析量，最坏 300 万。
在 iPhone 上这是肉眼可见的掉帧和输入延迟，尤其长回答（11-24 那轮 1203 chunks / 2206 字符答案）。

改法（前端，改动很小）：token 先进缓冲字符串，`requestAnimationFrame` 或 60ms 定时器
批量 flush 到 `updateAssistantMessage`。chunk 数会从 1200 次渲染降到 ~250 次（15 秒 ÷ 60ms），
视觉上仍是逐字流。

## 7. 额度与限速

- 今日额度：16/100（`data/quota.json`）；历史最高单日 63 轮（11-16）。
- 全期日志中 **429 出现 0 次**：100/天和 20/分钟两条线都没被碰过。
  `20/分钟` 对当前流量形同虚设，留着防刷可以，但别指望它做过载保护。

## 8. 11-24 15:25 那轮 65 秒的解剖

用户问「第二章能拿到什么道具用来做早操或者叠加附加伤害的」。

- 9 次 LLM 调用（15:25:49 → 15:26:39），跨度 50s；
- 35 次工具调用 = 9 轮里并行发起，平均每轮 3.9 个；
- 1203 chunks，答案 2206 字符；
- `tokens_in` 累计 133,326（多轮累加，非单次上下文）。

结论：慢不在单次 LLM 延迟（每次 2-12 秒都正常），而在**轮次多**。压这个的方向是
「一次 LLM 并行发更多精简的工具调用」以及提示词层约束"够用就停"，不是压历史。

## 9. 优先级

| # | 事项 | 归属 | 依据 |
|---|---|---|---|
| 1 | L0 SSE 心跳 + 断流记账 + `first_token_ms` | 服务器侧 | §1 §2 |
| 2 | `progress` 事件（工具 id + round） | Mac 侧 | §2 |
| 3 | AgentState 补 4 个字段 + 回归测试 | Mac 侧 + 服务器侧测试 | §3 |
| 4 | 开 `SERVER_HISTORY=1` 观察 2 天 | 服务器侧 | §4 |
| 5 | 前端 token 渲染节流（rAF/60ms） | 前端 | §6 |
| 6 | 断连轮在前端保留"未回答"标记 | 前端 | §1 |
