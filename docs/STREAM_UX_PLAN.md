# 流式响应体验方案（静默期 / 进度反馈 / 断流恢复）

> 2026-11-25。来源：线上 11-25 11:56:40 一轮 iPhone 移动网络提问，
> 服务端跑 25.6s 工具循环、7 次 DeepSeek 调用全 200，**一个字节都没写给前端**，
> 客户端先断开 → 前端 `catch` 分支弹「网络错误，无法连接到助手服务器」。
> 关联：`docs/ISSUES_2026-11-25.md` I-25（待建）。

## 1. 问题拆解

三个独立问题，别混在一起修：

| 层 | 症状 | 根因 | 归属 |
|---|---|---|---|
| T 传输层 | 移动网络下连接被掐，前端报网络错误 | SSE 首字节前静默 25–65s，无任何 keepalive | 服务器侧 `api_server.py` |
| P 协议层 | 前端只知道「思考中」，不知道在干什么 | `ask_stream` 只在 `AIMessageChunk.content` 非空时吐 token，工具调用阶段 content 为空，全程无事件 | Mac 侧 `src/rag_agent/multi_agent.py` |
| U 前端层 | 等待无反馈、断流只能看到「网络错误」 | loading 文案静态、断流覆盖掉已有内容、失败无重试入口 | Mac 侧博客仓库 `htmls/hollow_knight.html` |

实测数据（`data/turns/`，11-20～11-25 共 125 轮）：elapsed p50 15.4s、p90 31.9s、max 65.4s。
`stream_chunks=0` 的轮次 2 条（11-22 22:54、11-25 11:56），都是客户端先断。
注意 elapsed 不等于首 token 静默——目前没有 first_token 观测字段，这是第 4 个问题（观测）。

## 2. 分层方案

### L0 SSE keepalive（服务器侧，立即可做，风险最低）

目的：让连接在等待期一直有字节，避免中间设备/浏览器把长静默判死。

改动点 `api_server.py:handle_ask_stream`：

1. `await resp.prepare(request)` 之后**立刻**写一行 `: connected\n\n`。
   首字节 0 延迟到达，浏览器与中继立刻确认连接可用。
2. 消费循环由 `async for` 改为「生产者 task + 队列 + 超时取」：

```python
q: asyncio.Queue = asyncio.Queue()

async def _pump():
    try:
        async for ev in ask_stream(question, history=..., verbose=True, meta=meta):
            await q.put(ev)
    except Exception as e:                    # 生成器内部已吞异常，这里兜底
        await q.put(("error", str(e)))
    finally:
        await q.put(None)                     # 哨兵

pump = asyncio.create_task(_pump())
try:
    async with asyncio.timeout(float(config.get("stream_timeout", 240))):
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=10)
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")   # 注释行，前端天然忽略
                continue
            if ev is None:
                break
            ...原有 token/meta/error 分发...
finally:
    pump.cancel()
```

要点：
- 心跳用 SSE 注释行（`: xxx`），前端 `if (!line.startsWith('data: ')) continue` 直接跳过，
  **前端零改动**即可生效。
- 心跳不计入 `token_count`、不写日志（否则日志被噪音淹没）。
- `pump.cancel()` 必须在 `finally`，否则客户端断开后生成器仍在后台跑完整个工具循环（白烧 token）。
- 外层 240s `asyncio.timeout` 保留；`wait_for` 的超时只用于发心跳，不算失败。
- 体积代价：10s 一次 19 字节，60s 静默 ≈ 114 字节，可忽略。
- 不做 WebSocket：改动大、要动前端重连逻辑，收益与 SSE 心跳重叠，暂不考虑。

### L1（已否决）前端本地假计时

最初设计里有一档「前端自己 setInterval 计时，0–4s 思考中 / 4–15s 正在查资料 / ≥15s 还在查」。
**2026-11-25 用户否掉**：没有后端真数据就别假装在干具体事，假进度比没进度更伤信任。
等待期只允许两种状态：有真事件就展示真语义，没事件就老实显示「思考中…」。
想改善等待观感只能靠 L2 把真事件发出来，不能靠时间瞎猜。

### L2 真实工具进度事件（协议层，Mac 侧）

新增事件类型 `progress`，与现有 `token/meta/error/done` 并列：

```
data: {"type":"progress","phase":"tool_start","tool":"search_knowledge_base","round":1,"elapsed":3.2}
data: {"type":"progress","phase":"tool_done","tool":"search_knowledge_base","round":1,"elapsed":6.8}
```

`phase` 取值：`thinking`（进入 agent 节点，可选）、`tool_start`、`tool_done`。
后端只发工具 **id**（`search_knowledge_base` 等），中文文案放在前端映射表，
以后加工具不用改后端。

实现位置 `src/rag_agent/multi_agent.py:ask_stream` 的 `astream` 循环：

- `mode == "messages"` 分支已经是唯一入口，现有逻辑只处理 `AIMessageChunk` 且有 content 的情况。
- 增加：`AIMessageChunk` 的 `tool_call_chunks` 非空且能取到 `name` → `yield ("progress", {...phase:"tool_start"})`。
  注意 name 分片只在首个 chunk 出现，必须**累积拼接**（`accumulated.tool_calls`）后再判，
  取不到 name 就发不带 tool 的通用 `tool_start`。
- 增加：`ToolMessage` → `phase:"tool_done"`。**不要转发 ToolMessage 内容**（现状已过滤，保持）。
- 去重：同一 `(round, tool)` 只发一次 `tool_start`；`round` 按 tool_done 次数递增。
- 首 token 之后不再发 progress（正文已经在前端流动，进度无意义）。
- 断流/异常路径不受影响，`progress` 事件不进 `meta`、不影响 quota 判定。

前端消费（`htmls/hollow_knight.html` 的 `switch (data.type)`）：

| 事件 | 文案 |
|---|---|
| `tool_start` + `search_knowledge_base` | `正在检索攻略库…` |
| `tool_start` + `query_structured_data` | `正在查数据表…` |
| `tool_start` + `show_database_schema` | `正在看数据结构…` |
| `tool_start` 无 tool | `正在查资料…` |
| `tool_done` 且 round ≥ 2 | `已查 {round} 次，还在整理…` |
| 收到第一个 `token` | 切正文流式渲染（进度文案消失） |

### L3 断流恢复（前端侧）

现状：`catch (e)` 无差别覆盖成「网络错误，无法连接到助手服务器」，已渲染的半截内容被丢掉。

改为分两种：

1. **已有部分答案**（`answerText` 非空）→ 保留正文，末尾追加灰字
   `（连接中断，回答可能不完整）` + `重试` 按钮（复用原 question 重发）。
2. **一个 token 都没收到** → 整条替换成
   `连接中断了 · 点这里重试`，同样带重试按钮。
3. 断流时同步清 `loading`、清计时器。

移动端补充（可选，成本低）：
- 监听 `visibilitychange`，切回前台且 `loading` 为真、已等 >90s 时，直接按断流处理并提示重试
  （iOS 切后台会静默断掉 fetch，回来时界面会一直转圈）。
- 加「取消」按钮（`AbortController`）：用户主动 abort → 服务端走 `ConnectionResetError` 分支。

### L4 服务端断流记账（服务器侧，与 L0 一起做）

现有断连分支有两个缺口（`api_server.py`）：

```python
except (ConnectionResetError, ConnectionAbortedError):
    stream_failed = True
    logger.warning(f"客户端断开连接: {ip}")
```

- **没退额度**：`TimeoutError` 分支调了 `quota.refund()`，断连分支没有 → 网络原因没看到答案也扣 1 次。
  建议：断连且 `answer_parts` 为空时 `refund()`（已产出部分答案的不退，模型确实干了活）。
- **没标 route**：断连轮次 `route` 落库为 null、`tokens_in=0`，统计上和正常轮并存、看不出来。
  建议：`meta` 补 `route="client_abort"`，并加 `aborted=True` 字段；`NON_LLM_ROUTES` 不要包含它
  （否则会被算成不计费轮次，掩盖真实消耗）。

新增观测字段（`turn_record`）：

- `first_token_ms`：首个 token 相对请求开始的时间，直接量化「静默期」，心跳方案的效果靠它验收。
- `progress_events`：本轮 progress 事件数（L2 之后才有意义）。

## 3. 验收

服务器侧（`pytest`，全部用 aiohttp 测试客户端，工厂模式）：

1. `test_ask_stream_keepalive`：注入一个每 3s 才吐一个 token 的假生成器，断言
   - 首字节是 `: connected`；
   - token 到达前至少收到一次 `: keepalive`；
   - 心跳不改变 `token_count` 与落库内容。
2. `test_ask_stream_client_abort`：生成器慢吐，客户端读一半关闭 → 断言 `route="client_abort"`、
   未产出 token 时额度已退、`answer_partial` 标记正确。
3. `test_first_token_ms`：正常轮次 `first_token_ms` 被写入且大于 0。

线上验证（用户在自己终端跑，助手 curl 被 SSRF 守卫拦）：

```
curl -N -X POST https://api.weirdsnap.top:8443/api/ask/stream -H 'Content-Type: application/json' -d '{"question":"bg3 第三章怎么去铁王座"}'
```

观察点：`data:` 事件之间不得超过 10s 空档；开头有 `: connected`。
上线后 3 天看 `turns_report.py --days 3`：`stream_chunks=0` 轮次应为 0，`first_token_ms` p90 有数。

前端手测：iPhone + 移动网络提一个会触发 6+ 次工具调用的问题（如「第三章有哪些支线」「铁手套在哪拿」），
确认等待期文案在动、不弹「网络错误」、断网后能一键重试。

## 4. 分工与顺序

| 步骤 | 内容 | 归属 | 依赖 |
|---|---|---|---|
| 1 | L0 心跳 + L4 断流记账 + `first_token_ms` + 3 条测试 | 服务器侧 | 无 |
| 2 | L2 progress 协议（工具 id + round） | Mac 侧（`multi_agent.py`） | 需 1 先合入（否则事件到了前端也没心跳保护） |
| 3 | L3 断流恢复 + 取消按钮 + progress 文案映射 | 前端 | 依赖 2 |

前端「历史分批渲染与反向无限滚动」另见 `CHAT_HISTORY_RENDER_PLAN.md`，与本文不耦合。

前端计时器已否决（L1），所以第 2 步完成前前端只会显示「思考中…」——这是有意为之的诚实状态，
不是回归。先把 1 的连接稳定性做掉，再上 2 的语义进度。
