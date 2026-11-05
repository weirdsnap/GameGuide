"""
多游戏 Agent 入口（薄壳）。

路由、工具、Agent 逻辑均已迁入 rag_agent.graph（LangGraph 图），
本模块只保留对外的两个入口函数与过渡期的游戏连续性全局状态。

用法：
  from rag_agent.multi_agent import ask
  answer = ask("泰拉瑞亚克苏鲁之眼怎么打？")
"""

import logging
from typing import Optional

from langchain_core.messages import AIMessageChunk

from rag_agent.graph import get_graph

logger = logging.getLogger(__name__)

# ── 游戏切换状态（过渡期全局，后续由会话存储替代）──
_LAST_GAME: Optional[str] = None
_LAST_GAME_CONFIRMED: bool = False


def _build_initial_state(question, history, model_name, verbose) -> dict:
    """组装图的初始 state（两个入口共用）。"""
    return {
        "question": question.strip(),
        "history": history,
        "model_name": model_name,
        "verbose": verbose,
        "last_game": _LAST_GAME,
        "last_game_confirmed": _LAST_GAME_CONFIRMED,
    }


def _writeback_game_state(result: dict):
    """把图终态里的路由状态回写全局（过渡期机制）。"""
    global _LAST_GAME, _LAST_GAME_CONFIRMED
    _LAST_GAME = result.get("last_game")
    _LAST_GAME_CONFIRMED = result.get("last_game_confirmed", False)


def _meta_from_state(state: dict) -> dict:
    """把图终态翻译成一行的观测元数据（第 0 步：只记录，不参与决策）。

    route 与 detected_game 的对应关系沿用 route_after_detect 的契约：
      None → menu（弹菜单/报错，不过 LLM）
      "__llm_fallback__" → fallback（知识库外游戏，LLM 自身知识回答）
      其余 → agent（正常游戏主路径）
    """
    detected = state.get("detected_game")
    if detected is None:
        route = "menu"
    elif detected == "__llm_fallback__":
        route = "fallback"
    else:
        route = "agent"
    usage = state.get("usage") or {}
    return {
        "route": route,
        "game": detected if route == "agent" else None,
        "tokens_in": int(usage.get("tokens_in") or 0),
        "tokens_out": int(usage.get("tokens_out") or 0),
        "tool_calls": int(usage.get("tool_calls") or 0),
    }


def ask(question, history=None, model_name=None, verbose=False, meta=None):
    """多游戏 Agent 入口（非流式）。

    meta：可选的 dict，函数结束时被填入本轮观测数据
          （route / game / tokens_in / tokens_out / tool_calls）。
          传 None 时行为与旧版完全一致；不用模块全局，避免并发串数据。
    """
    try:
        result = get_graph().invoke(
            _build_initial_state(question, history, model_name, verbose))
    except Exception as e:
        logger.error(f"Agent 调用失败: {e}")
        if meta is not None:
            meta.update({"route": "error", "game": None, "tokens_in": 0,
                         "tokens_out": 0, "tool_calls": 0, "error": str(e)[:200]})
        return f"[查询出错] {e}"
    _writeback_game_state(result)
    if meta is not None:
        meta.update(_meta_from_state(result))
    return result.get("answer") or "（无回复）"


async def ask_stream(question, history=None, model_name=None, verbose=False, meta=None):
    """流式入口。产出协议不变：(token|meta|error, data)。

    meta 参数含义同 ask()：结束时被填入本轮的观测数据。
    """
    final_state = {}
    emitted_token = False
    error_msg = None
    try:
        # subgraphs=True：agent_node 内部的 create_react_agent 是子图，
        # 不开这个开关其 LLM token 不会冒泡到外层流。
        # 开启后每项变为 (namespace, mode, chunk) 三元组：
        #   namespace == ()          → 父图（meta custom / 终态 values 在这里）
        #   namespace 非空且 messages → 子图（agent）的 LLM token
        async for namespace, mode, chunk in get_graph().astream(
            _build_initial_state(question, history, model_name, verbose),
            stream_mode=["custom", "messages", "values"],
            subgraphs=True,
        ):
            if namespace == () and mode == "custom" and chunk.get("type") == "meta":
                yield "meta", {k: v for k, v in chunk.items() if k != "type"}
            elif mode == "messages":
                msg_chunk, _metadata = chunk
                # 只转发 LLM 生成的文本 chunk；工具结果（ToolMessage）也会
                # 出现在 messages 流里，必须过滤掉，否则会泄到前端
                if isinstance(msg_chunk, AIMessageChunk) and msg_chunk.content:
                    emitted_token = True
                    yield "token", msg_chunk.content
            elif namespace == () and mode == "values":
                final_state = chunk          # 父图每步全量 state，最后一份即终态
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Agent 流式调用失败: {e}")
        # 观测数据必须在 yield 之前落地：消费方收到 error 事件通常立即 break，
        # 生成器不再被恢复，yield 以后的收尾代码不会执行
        if meta is not None:
            meta.update({"route": "error", "error": error_msg[:200]})
        yield "error", str(e)
    finally:
        if final_state:
            _writeback_game_state(final_state)
    # 菜单/报错路径不经过 LLM，没有任何 token——把终态 answer 补发为一个
    # token，与旧版"菜单文本当单 token 吐出"的行为对齐
    if not emitted_token and final_state.get("answer"):
        yield "token", final_state["answer"]
    # 观测数据后置填：正常路径在此时才拿到完整终态（含 usage）
    if meta is not None and not error_msg:
        meta.update(_meta_from_state(final_state))
