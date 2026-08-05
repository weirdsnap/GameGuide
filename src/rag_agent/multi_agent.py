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


def ask(question, history=None, model_name=None, verbose=False):
    """多游戏 Agent 入口（非流式）。签名与旧版完全一致。"""
    try:
        result = get_graph().invoke(
            _build_initial_state(question, history, model_name, verbose))
    except Exception as e:
        logger.error(f"Agent 调用失败: {e}")
        return f"[查询出错] {e}"
    _writeback_game_state(result)
    return result.get("answer") or "（无回复）"


async def ask_stream(question, history=None, model_name=None, verbose=False):
    """流式入口。产出协议不变：("token"|"meta"|"error", data)。"""
    final_state = {}
    emitted_token = False
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
        logger.error(f"Agent 流式调用失败: {e}")
        yield "error", str(e)
    finally:
        if final_state:
            _writeback_game_state(final_state)
    # 菜单/报错路径不经过 LLM，没有任何 token——把终态 answer 补发为一个
    # token，与旧版"菜单文本当单 token 吐出"的行为对齐
    if not emitted_token and final_state.get("answer"):
        yield "token", final_state["answer"]
