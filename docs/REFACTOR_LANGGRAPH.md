# LangGraph 重构指南

将手写的游戏路由 / 状态管理改造为 LangGraph 图结构。

## 当前架构（重构前）

```
用户输入
    ↓
[手写] detect_game()           — 信号词匹配
[手写] _resolve_game()         — 状态机（连续对话 / 切换检测）
[手写] _is_unknown_game_query() — 兜底判断
[手写] build_messages()        — 消息拼接
    ↓
[LangGraph 预构建] create_react_agent()
  → LLM 选择：调用工具 or 直接回答
    ↓
[手写工具函数] search_knowledge_base / query_structured_data / show_database_schema
```

手写部分集中在 `multi_agent.py`，依赖模块级全局变量 `_LAST_GAME` 和 `_LAST_GAME_CONFIRMED`。

## 目标架构（重构后）

```
用户输入
    │
    ▼
┌─────────────────────┐
│  detect_game_node   │  ← LLM 或规则判断游戏
└────────┬────────────┘
         │ game_key
         ▼
┌─────────────────────┐
│  route_data_node    │  ← 加载对应向量库 + DB
└────────┬────────────┘
         │ 并行（可选）
    ┌────┴────┐
    ▼         ▼
┌──────┐ ┌────────┐
│ RAG  │ │  SQL   │  ← 同时检索，不靠 LLM 选工具
└──┬───┘ └───┬────┘
    │         │
    ▼─────────▼
┌─────────────────────┐
│  synthesize_node    │  ← LLM 融合两路结果生成回答
└────────┬────────────┘
         │
    ┌────┴────┐
    ▼         ▼
  回答     兜底处理
```

## 改造步骤

### 第1步：定义图状态

```python
from typing import TypedDict, Optional, List, Any
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from typing_extensions import Annotated

class AgentState(TypedDict):
    question: str                                 # 当前问题
    history: Optional[List[dict]]                  # 对话历史
    detected_game: Optional[str]                   # 游戏检测结果
    last_game: str                                 # 上一轮的游戏
    game_switched: bool                            # 是否切换了游戏
    prompt: str                                    # 构建好的 system prompt
    messages: Annotated[list, add_messages]        # LangGraph 自动管理的消息列表
```

- `messages` 用 `Annotated[list, add_messages]`：消息会被自动追加，不需要手写 `build_messages()`
- 不要放 `ChatOpenAI` 实例、`sqlite3.Connection`等不可序列化的对象进 state

### 第2步：拆节点

```python
def detect_game_node(state: AgentState) -> dict:
    """游戏检测节点。可保留现有 detect_game()，也可升级为 LLM 判断。"""
    question = state["question"]
    history = state.get("history", [])
    last_game = state.get("last_game", "")
    game_key, confidence = detect_game(question)
    # ... 现有 _resolve_game 相似逻辑 ...
    return {"detected_game": game_key, "prompt": full_prompt, "game_switched": switched}


def agent_node(state: AgentState) -> dict:
    """现有 ReAct Agent 调用。"""
    llm = ChatOpenAI(**LLM_CONFIG)
    agent = create_agent(llm, GAME_TOOLS, prompt=SystemMessage(content=state["prompt"]))
    messages = build_messages(state["question"].strip(), state.get("history", []), state["prompt"])
    result = agent.invoke({"messages": messages}, {"recursion_limit": 50})
    return {"messages": [AIMessage(content=result["messages"][-1].content)]}


def fallback_node(state: AgentState) -> dict:
    """LLM 兜底（不认识的游戏）。"""
    llm = ChatOpenAI(**LLM_CONFIG)
    messages = build_messages(state["question"].strip(), state.get("history", []), state.get("prompt", ""))
    response = llm.invoke(messages)
    return {"messages": [AIMessage(content=response.content or "（无回复）")]}
```

### 第3步：写条件路由

```python
def route_after_detect(state: AgentState) -> str:
    """条件边：检测结果决定走哪条路。"""
    game = state["detected_game"]
    if game is None:
        return "menu"         # 弹菜单
    elif game == "__llm_fallback__":
        return "fallback"
    else:
        return "agent"
```

### 第4步：编译图

```python
workflow = StateGraph(AgentState)
workflow.add_node("detect_game", detect_game_node)
workflow.add_node("agent", agent_node)
workflow.add_node("fallback", fallback_node)
workflow.set_entry_point("detect_game")

workflow.add_conditional_edges(
    "detect_game", route_after_detect, {
        "agent": "agent",
        "fallback": "fallback",
        "menu": END,
    }
)

workflow.add_edge("agent", END)
workflow.add_edge("fallback", END)

graph = workflow.compile()
```

### 第5步：改写 ask() / ask_stream()

```python
def ask(question, history=None, model_name=None, verbose=False):
    result = graph.invoke({
        "question": question.strip(),
        "history": history or [],
        "last_game": _LAST_GAME,
    })
    # 更新 last_game
    dg = result.get("detected_game")
    if dg and dg not in (None, "__llm_fallback__"):
        _LAST_GAME = dg
        _LAST_GAME_CONFIRMED = True
    return result["messages"][-1].content
```

## 进阶改造

### 并行 RAG + SQL

加两个节点，用 `add_edge` 建并行路径：

```
route_data ──→ rag_retrieve ──┐
            └─→ sql_query   ──┤
                               ▼
                          synthesize
```

```python
workflow.add_edge("route_data", "rag_retrieve")
workflow.add_edge("route_data", "sql_query")
workflow.add_edge("rag_retrieve", "synthesize")
workflow.add_edge("sql_query", "synthesize")
```

适合对准确性要求高的场景，代价是每次请求都跑两个查询。

### 问题分类路由

加一个分类节点，判断问题类型后走不同路径：数值类 → SQL，剧情类 → RAG，混合类 → 并行。

```python
def classify_question(state: AgentState) -> str:
    q = state["question"]
    if any(w in q for w in ["多少", "数值", "伤害", "价格"]):
        return "sql_only"
    if any(w in q for w in ["背景", "故事", "怎么打"]):
        return "rag_only"
    return "both"
```

## Review Checklist

| 检查项 | 说明 |
|--------|------|
| 状态定义 | `add_messages` reducer 用对了吗？state 里有不可序列化对象吗？ |
| 条件边 | `route_after_detect` 的返回值匹配 `add_conditional_edges` 的字典键吗？ |
| 全局变量清理 | `_LAST_GAME` 和 `_LAST_GAME_CONFIRMED` 移除了吗？ |
| 现有功能保留 | 切换检测 / LLM 兜底 / 弹菜单 还在吗？ |
| 流式 | `ask_stream()` 的 `astream_events` 接上图了吗？ |
| 多轮对话 | history 在 `graph.invoke` 之间如何传递？ |
| 错误处理 | 图的异常被 `try/except` 兜住了吗？ |

## 注意事项

1. **State = 不可序列化**：`ChatOpenAI`、`sqlite3.Connection`、`FAISS` 对象不能放 state 里
2. **Reducer 只在顶层生效**：`Annotated[list, add_messages]` 只在 TypedDict 顶层有效
3. **条件边键名必须匹配**：字典键是字符串，route 函数返回值必须是其中之一
4. **全局变量可以过渡保留**：先让图跑通，再逐步清理全局变量
