# LangGraph 重构实施文档

> 第一期目标：**只做图化，行为完全不变**。把 `multi_agent.py` 里的手写路由状态机搬进 LangGraph 图，入口签名与流式协议保持不变。并行 RAG+SQL、去全局变量等留作后续迭代（见文末）。

## 当前架构（重构前）

```
用户输入
    ↓
[手写] detect_game()           — 信号词匹配（game_router.py，纯函数，不动）
[手写] _resolve_game()         — 状态机（连续对话 / 切换检测），依赖模块级全局
                                  _LAST_GAME / _LAST_GAME_CONFIRMED
[手写] _is_unknown_game_query() — 兜底判断
[手写] build_messages()        — 消息拼接
    ↓
[LangGraph 预构建] create_react_agent()
  → LLM 选择：调用工具 or 直接回答
    ↓
[手写工具函数] search_knowledge_base / query_structured_data / show_database_schema
```

痛点集中在 `src/rag_agent/multi_agent.py`（781 行），它混杂了四类职责：

1. 路由状态机 `_resolve_game()`（读写模块级全局变量，无法测试、无法并发）
2. 三个 `@tool` 工具函数 + DB 辅助函数（约 380 行）
3. `build_messages()` 消息拼接
4. 入口 `ask()` / `ask_stream()`

## 目标架构（第一期）

```
用户输入
    │
    ▼
 detect_game_node        ← 现有 _resolve_game 逻辑搬入（纯函数化，读写 state 而非全局）
    │
    ├─ detected_game = None               → menu_node      → END
    ├─ detected_game = "__llm_fallback__" → fallback_node  → END
    └─ 其它                                → agent_node     → END（内部仍是 create_react_agent）
```

约束（已核实的代码事实）：

- 调用方 `api_server.py:22`、`tests/test_qa.py:19`、`tests/test_light.py:215` 都以
  `ask(question, history=...)` / `ask_stream(question, history=...)` 方式调用，
  **入口签名和流式产出协议（`("token"|"meta"|"error", data)`）必须不变**。
- `src/rag_agent/game_router.py` 是纯函数（`detect_game` / prompt 构建 / `is_switch_query`），不动。
- 环境已装 langgraph 1.2.7：`StateGraph`、`get_stream_writer`（自定义事件）、
  `astream(stream_mode=[...])` 均可用。
  注意：langgraph 1.x 已**移除** `adispatch_custom_event` 和 `on_custom_event`，
  自定义事件改用 `get_stream_writer()` 发送、`stream_mode="custom"` 接收。

## 实施步骤

### 第 1 步：新建 `src/rag_agent/tools.py`（纯移动，不改逻辑）

把 `multi_agent.py` 中的以下符号**原样搬入**新文件：

- DB 辅助：`_get_db`、`_query_db`、`_format_db_result`、`_search_all_tables`、`_fmt_table_names`
- 游戏解析：`_resolve_game_key`、`_tool_game_cfg`
- 工具：`search_knowledge_base`、`query_structured_data`、`show_database_schema`、`GAME_TOOLS`

目的：让 `graph.py` 从 `tools.py` 导入工具，避免 `graph.py ↔ multi_agent.py` 循环导入。
没有其他文件 import 这些符号，不需要兼容层。

### 第 2 步：新建 `src/rag_agent/graph.py`（核心）

#### 2.1 图状态

```python
from typing import Optional, List
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    question: str
    history: Optional[List[dict]]
    model_name: Optional[str]
    verbose: bool
    # ── 路由状态（替代 _LAST_GAME 全局）──
    last_game: Optional[str]
    last_game_confirmed: bool
    detected_game: Optional[str]
    game_switched: bool
    # ── detect 节点产物 ──
    prompt: str
    menu_text: Optional[str]
    vs_ok: Optional[bool]
    db_ok: Optional[bool]
    # ── 最终回答 ──
    answer: str
```

注意：

- **不要**把 `ChatOpenAI`、`sqlite3.Connection`、FAISS 对象放进 state（不可序列化）。
- 本期**不用** `add_messages` reducer：history 由调用方每次整包传入，继续用
  `build_messages()` 拼接，避免同时改变消息流行为。

#### 2.2 节点

`detect_game_node`：把 `_resolve_game()` 的逻辑搬来并**纯函数化**——从 state 读
`last_game` / `last_game_confirmed`，返回更新后的值，不再碰全局变量。菜单常量
`_MENU_NEW` / `_MENU_SWITCH`、`_is_unknown_game_query()`、`_KNOWN_EXTERNAL_GAMES`
一并移入本文件。

```python
def detect_game_node(state: AgentState) -> dict:
    q = state["question"].strip()
    if not q:
        return {"detected_game": None, "menu_text": "请问你想了解哪款游戏的攻略？"}

    game_key, confidence = detect_game(q)
    last_game = state.get("last_game")
    confirmed = state.get("last_game_confirmed", False)
    switched = False

    if confidence >= 0.4:
        if last_game and confirmed and game_key != last_game:
            switched = True
        last_game, confirmed = game_key, True
    elif is_switch_query(q):
        return {"detected_game": None, "menu_text": _MENU_SWITCH,
                "last_game": None, "last_game_confirmed": False}
    elif state.get("history") and last_game and confirmed:
        game_key = last_game                       # 延续上轮游戏
    else:
        if _is_unknown_game_query(q):
            return {"detected_game": "__llm_fallback__", "prompt": _FALLBACK_PROMPT,
                    "last_game": None, "last_game_confirmed": False}
        return {"detected_game": None, "menu_text": _MENU_NEW,
                "last_game": None, "last_game_confirmed": False}

    cfg = AVAILABLE_GAMES[game_key]
    vs_ok = os.path.isdir(cfg["vectorstore_dir"])
    db_ok = os.path.isfile(cfg["db_path"])
    if not vs_ok and not db_ok:
        return {"detected_game": None,
                "menu_text": f"抱歉，{cfg['name']} 的知识库尚未准备好。请联系管理员初始化数据。",
                "last_game": None, "last_game_confirmed": False}

    prompt = build_game_prompt(game_key) + "\n\n" + build_common_rules()
    if switched:
        prompt += f"\n\n**系统提示：** 用户切换了话题开始聊{cfg['name']}，…（同现有逻辑）"

    return {"detected_game": game_key, "prompt": prompt,
            "vs_ok": vs_ok, "db_ok": db_ok, "game_switched": switched,
            "last_game": last_game, "last_game_confirmed": confirmed}
```

`menu_node` / `fallback_node` / `agent_node`：

```python
def menu_node(state: AgentState) -> dict:
    return {"answer": state["menu_text"]}


def _make_llm(state: AgentState, streaming: bool = False) -> ChatOpenAI:
    config = dict(LLM_CONFIG)
    if state.get("model_name"):
        config["model"] = state["model_name"]
    if streaming:
        config["streaming"] = True
    return ChatOpenAI(**config)


def fallback_node(state: AgentState) -> dict:
    llm = _make_llm(state)
    messages = build_messages(state["question"].strip(),
                              state.get("history"), state["prompt"])
    response = llm.invoke(messages)
    return {"answer": response.content or "（无回复）"}


def agent_node(state: AgentState) -> dict:
    # 顶部需 from langgraph.config import get_stream_writer（langgraph 1.x）
    llm = _make_llm(state, streaming=True)          # streaming=True 让 messages 流模式拿到 token chunk
    agent = create_agent(llm, GAME_TOOLS,
                         prompt=SystemMessage(content=state["prompt"]))
    # 流式 meta（stream_mode 不含 "custom" 时 writer 为 no-op，invoke 时静默忽略）
    get_stream_writer()({
        "type": "meta",
        "game": state["detected_game"],
        "game_name": AVAILABLE_GAMES[state["detected_game"]]["name"],
        "model": llm.model_name,
        "sources": {"vectorstore": state.get("vs_ok"), "database": state.get("db_ok")},
    })
    messages = build_messages(state["question"].strip(),
                              state.get("history"), state["prompt"])
    result = agent.invoke({"messages": messages}, {"recursion_limit": 50})
    answer = result["messages"][-1].content if result.get("messages") else ""
    return {"answer": answer or "（Agent 没有返回有效回答）"}
```

#### 2.3 条件路由与编译

```python
def route_after_detect(state: AgentState) -> str:
    game = state.get("detected_game")
    if game is None:
        return "menu"
    if game == "__llm_fallback__":
        return "fallback"
    return "agent"


def build_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("detect_game", detect_game_node)
    workflow.add_node("menu", menu_node)
    workflow.add_node("fallback", fallback_node)
    workflow.add_node("agent", agent_node)
    workflow.set_entry_point("detect_game")
    workflow.add_conditional_edges(
        "detect_game", route_after_detect,
        {"menu": "menu", "fallback": "fallback", "agent": "agent"},
    )
    workflow.add_edge("menu", END)
    workflow.add_edge("fallback", END)
    workflow.add_edge("agent", END)
    return workflow.compile()


_graph = None

def get_graph():
    """模块级懒加载单例，编译一次复用。"""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
```

### 第 3 步：瘦身 `multi_agent.py`

删除：已搬走的工具/DB 辅助、`_resolve_game`、菜单常量、`_is_unknown_game_query`、
`_KNOWN_EXTERNAL_GAMES`。

保留：`_LAST_GAME` / `_LAST_GAME_CONFIRMED` 全局变量（**过渡期**，调用方零改动）、
`_reset_game_state()`、`build_messages()`（graph.py 从这里 import）。

`ask()` 变为薄封装，**签名不变**：

```python
from rag_agent.graph import get_graph


def ask(question, history=None, model_name=None, verbose=False):
    try:
        result = get_graph().invoke({
            "question": question.strip(),
            "history": history,
            "model_name": model_name,
            "verbose": verbose,
            "last_game": _LAST_GAME,
            "last_game_confirmed": _LAST_GAME_CONFIRMED,
        })
    except Exception as e:
        logger.error(f"Agent 调用失败: {e}")
        return f"[查询出错] {e}"

    _set_last_game(result.get("last_game"), result.get("last_game_confirmed", False))
    return result.get("answer") or "（无回复）"
```

`ask_stream()` 同理，用 `astream(stream_mode=[...])` 接管整图：

```python
async def ask_stream(question, history=None, model_name=None, verbose=False):
    initial_state = {...}  # 同 ask()
    last_game_update = {}
    try:
        async for mode, chunk in get_graph().astream(
            initial_state,
            stream_mode=["custom", "messages", "values"],
        ):
            if mode == "custom" and chunk.get("type") == "meta":
                yield "meta", {k: v for k, v in chunk.items() if k != "type"}
            elif mode == "messages":
                msg_chunk, _metadata = chunk
                if msg_chunk.content:
                    yield "token", msg_chunk.content
            elif mode == "values":
                last_game_update = chunk          # 每步的全量 state，最后一份即终态
    except Exception as e:
        yield "error", str(e)
    finally:
        _set_last_game(last_game_update.get("last_game"),
                       last_game_update.get("last_game_confirmed", False))
```

> **注意**：回写 `last_game` 用 `stream_mode` 里的 `"values"`——图每跑完一个节点就
> emit 一份全量 state，订阅时保留最后一份即终态，零额外开销。
> （langgraph 1.x 的 `astream_events` 虽仍在，但自定义事件已不经过它，
> 统一用 `stream_mode` 更简洁，不要再混用两套。）

### 第 4 步：测试

1. 回归：`python -m pytest tests/test_light.py -v`（含 `detect_game`、`multi_agent` import，
   必须全绿）。
2. 新增 `tests/test_graph.py`（离线、不依赖 LLM，直接单测节点函数）：
   - `route_after_detect`：`None` / `"__llm_fallback__"` / 正常 game_key 三种映射；
   - `detect_game_node` 状态转移：
     - 新问题命中关键词 → 正常 game_key，`last_game` 更新；
     - 无关键词但有 `last_game` + history → 延续上轮游戏；
     - `is_switch_query` 命中 → `menu_text == _MENU_SWITCH`，状态清空；
     - 未知游戏（如"原神怎么玩"）→ `__llm_fallback__`；
     - 无关键词无历史 → `menu_text == _MENU_NEW`；
   - `menu_node` 直通 `menu_text → answer`。
3. 有 API key 时手动冒烟（可选）：`ask("空洞骑士辐光怎么打")` → 连续两轮验证游戏延续 →
   `ask("原神怎么玩")` 验证兜底。

## 自查清单

| 检查项 | 说明 |
|--------|------|
| 状态定义 | state 里没有 `ChatOpenAI` / sqlite 连接 / FAISS 等不可序列化对象 |
| 条件边 | `route_after_detect` 返回值与 `add_conditional_edges` 字典键一一对应 |
| 接口兼容 | `api_server.py`、`tests/test_qa.py`、`tests/test_light.py` 零改动可用 |
| 行为对齐 | 菜单弹出 / 切换检测 / 未知游戏兜底 / 知识库未就绪提示 均与重构前一致 |
| 流式协议 | `("token", ...)` / `("meta", ...)` / `("error", ...)` 产出顺序和字段不变 |
| 全局回写 | `invoke` / 流式结束后 `_LAST_GAME` 都从结果 state 回写 |
| 错误处理 | 图的异常被 `ask` / `ask_stream` 的 `try/except` 兜住，返回 `[查询出错]` / `("error", ...)` |

## 后续迭代（本期不做）

1. **去全局变量**：`_LAST_GAME` 目前仍是模块级全局（多用户共享一个值，有串话风险）。
   前提是 `api_server.py` 先支持按会话存取 `last_game`（现在 api_server 无会话存储，
   前端每次传全量 history）。改造方向：`ask()` 接受并返回 `last_game`，由服务端按
   token/会话 ID 维护。
2. **并行 RAG + SQL**：`detect_game` 之后分叉 `rag_retrieve` / `sql_query` 两个节点再汇合到
   `synthesize_node`，不靠 LLM 选工具。代价是每次请求固定跑两路查询。
3. **问题分类路由**：数值类 → SQL，剧情类 → RAG，混合类 → 并行。
4. **记忆体系演进**：分"会话级"和"长期"两级，见下方专节。

### 记忆体系演进（多轮对话 → 真正的记忆）

**现状**："多轮对话"是前端模拟出来的——服务端完全无状态，每轮请求由前端把
全量 history 整包重传，LLM 看到完整记录所以表现得像记得。图跑完 state 即丢弃，
唯一跨轮信息是 `_LAST_GAME` 全局变量（且所有用户共享一个值）。

**隐性坑**（随使用变长逐渐暴露）：

1. 历史无限增长：整包重传，对话越长 token 成本越高，无截断/摘要策略，终撞上下文上限；
2. `last_game` 是唯一"被记住"的东西，还是全局单值；
3. 重复追问：剧透管理要求"先问用户玩到第几章"，用户说过一次进度，隔天再来系统又不知道了。

**第一级：会话级记忆**——`add_messages` reducer + checkpointer（MemoryStore 起步，
生产换 SqliteSaver），按 thread_id 存档：

- 每轮只传新问题，旧消息由框架从存档合并，前端不再整包传；
- `last_game` 随 state 存档持久化，全局变量随之淘汰（与迭代 1 联动）；
- 刷新页面/重连后可接续对话。

**第二级：长期记忆**——跨会话的用户画像，基于 LangGraph Store（`BaseStore`，
与 checkpointer 是两样东西）：

- 图里加 memory 节点：进图时按 user_id 从 store 读画像拼进 prompt，
  出图时把新信息写回（如本轮提到的游戏进度）；
- 典型内容：用户各游戏的进度（服务剧透管理，不再每次追问）、
  回答偏好（中文带英文原名等）、常玩游戏。

两级的依赖顺序：先图化（本期）→ 会话级（迭代 4）→ 长期。state 结构设计时已为此
留好扩展位，届时加的是节点和存档，不是推翻重来。

### 路由计分改进（detect_game 计分机制，独立于图结构，图化完成后随时可做）

现状缺陷（2026-07 实测，8 个游戏）：

- **单个泛词命中 = 置信度 1.0**：置信度 = 最高分/总分，只有一个游戏得分时恒为 1.0。
  实测 `"温度太高怎么办"` → `(oni, 1.0)`、`"法术怎么学"` → `(baldurs_gate3, 1.0)`。
  置信度只衡量占比，不衡量区分度。
- **无加权计数**：signals 按命中个数计分，泛词（"温度""法术""电"）与特征词等权，
  词表长的游戏有结构性优势（bg3 三十余词 vs silksong 不足十词）。
- 跨游戏撞词目前仅 1 例（`hornet`: hollow_knight/silksong，同 IP 共享角色），
  但游戏增多后必然出现。

改进项（按性价比排序，改动集中在 `detect_game()` 一个函数，路由表结构不动）：

1. **信号词分两级**：`GAME_REGISTRY` 的 `signals` 拆为特征词（辐光、复制人、煌雷龙）
   与泛词（温度、法术、电）；命中规则改为"≥1 个特征词，或 ≥2 个泛词"才算有效命中。
2. **置信度看分差**：最高分 − 次高分 ≥ 2 才确信，接近则弹菜单让用户选
   （替代现在并列仅 ×0.5 的粗暴处理）。
3. **注册表 lint 测试**：遍历 `GAME_SIGNALS` 断言同一词不出现在两个游戏
   （`hornet` 等特例加白名单），新加游戏时 CI 自动拦截撞词。
4. **远期**（不值得为 8 个游戏做）：规则置信度低时降级到 LLM/embedding 路由。
