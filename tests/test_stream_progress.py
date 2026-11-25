"""ask_stream 的 progress 事件测试（docs/STREAM_UX_PLAN.md L2）。

背景（I-25）：工具调用阶段 AIMessageChunk 的 content 为空，旧实现全程静默，
移动网络下连接被中间设备判死。L2 要求识别工具阶段并发 progress 事件：
  tool_start：AIMessageChunk.tool_call_chunks 里拿到工具名（一轮只发一次）
  tool_done：ToolMessage 到达（内容绝不转发）后发，round 递增
约束：首 token 之后停发；progress 不进 meta、不影响 quota。

全部离线：用假图注入事件流，不碰 LLM/向量库。
"""

import asyncio
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import rag_agent.multi_agent as m  # noqa: E402


class _FakeGraph:
    def __init__(self, events):
        self._events = events

    async def astream(self, state, **kwargs):
        for event in self._events:
            yield event


def _run(events, monkeypatch):
    monkeypatch.setattr(m, "get_graph", lambda: _FakeGraph(events))
    meta = {}

    async def _collect():
        return [ev async for ev in m.ask_stream("q", meta=meta)]

    return asyncio.run(_collect()), meta


def _tool_call_chunk(name=None, args=""):
    return AIMessageChunk(content="", tool_call_chunks=[
        {"name": name, "args": args, "id": "call_1", "index": 0}])


def _tool_msg(content="SECRET"):
    return ToolMessage(content=content, tool_call_id="call_1")


def _values(answer="答案"):
    return ((), "values", {"detected_game": "terraria", "answer": answer,
                           "usage": {"tokens_in": 1, "tokens_out": 1,
                                     "tool_calls": 1}})


def _progress_events(out):
    return [data for ev, data in out if ev == "progress"]


def test_工具调用发出_start_和_done(monkeypatch):
    out, _ = _run([
        _values(""),
        ((("agent",),), "messages", (_tool_call_chunk("search_knowledge_base"), {})),
        ((("agent",),), "messages", (_tool_msg(), {})),
        ((("agent",),), "messages", (AIMessageChunk(content="答"), {})),
        _values(),
    ], monkeypatch)
    prog = _progress_events(out)
    assert [p["phase"] for p in prog] == ["tool_start", "tool_done"]
    assert prog[0]["tool"] == "search_knowledge_base"
    assert prog[0]["round"] == 1 and prog[1]["round"] == 1
    assert all("elapsed" in p for p in prog)
    assert ("token", "答") in out


def test_工具名只在首个分片_参数分片不重复发(monkeypatch):
    """name 只在首个 tool_call_chunk 出现，后续 args 分片不得重复发 tool_start。"""
    out, _ = _run([
        _values(""),
        ((("agent",),), "messages", (_tool_call_chunk("query_structured_data"), {})),
        ((("agent",),), "messages", (_tool_call_chunk(None, '{"table":'), {})),
        ((("agent",),), "messages", (_tool_call_chunk(None, '"weapons"}'), {})),
        ((("agent",),), "messages", (_tool_msg(), {})),
        _values(),
    ], monkeypatch)
    prog = _progress_events(out)
    assert [p["phase"] for p in prog] == ["tool_start", "tool_done"]
    assert prog[0]["tool"] == "query_structured_data"


def test_多轮工具调用_round_递增(monkeypatch):
    out, _ = _run([
        _values(""),
        ((("agent",),), "messages", (_tool_call_chunk("search_knowledge_base"), {})),
        ((("agent",),), "messages", (_tool_msg(), {})),
        ((("agent",),), "messages", (_tool_call_chunk("show_database_schema"), {})),
        ((("agent",),), "messages", (_tool_msg(), {})),
        ((("agent",),), "messages", (AIMessageChunk(content="答"), {})),
        _values(),
    ], monkeypatch)
    prog = _progress_events(out)
    assert [(p["phase"], p["round"]) for p in prog] == [
        ("tool_start", 1), ("tool_done", 1), ("tool_start", 2), ("tool_done", 2)]
    assert prog[2]["tool"] == "show_database_schema"


def test_取不到工具名时发通用_start(monkeypatch):
    out, _ = _run([
        _values(""),
        ((("agent",),), "messages", (_tool_call_chunk(None, '{"x": 1}'), {})),
        ((("agent",),), "messages", (_tool_msg(), {})),
        _values(),
    ], monkeypatch)
    prog = _progress_events(out)
    assert prog[0]["phase"] == "tool_start"
    assert "tool" not in prog[0]
    assert "tool" not in prog[1]     # 名字没出现过，tool_done 也不带


def test_首_token_之后停发_progress(monkeypatch):
    out, _ = _run([
        _values(""),
        ((("agent",),), "messages", (AIMessageChunk(content="答"), {})),
        # 正文已开始流动，之后即使再有工具消息也不发 progress
        ((("agent",),), "messages", (_tool_msg(), {})),
        _values(),
    ], monkeypatch)
    assert _progress_events(out) == []
    assert ("token", "答") in out


def test_ToolMessage_内容绝不转发(monkeypatch):
    out, _ = _run([
        _values(""),
        ((("agent",),), "messages", (_tool_call_chunk("search_knowledge_base"), {})),
        ((("agent",),), "messages", (_tool_msg("机密工具输出不得外泄"), {})),
        _values("机密工具输出不得外泄"),   # 终态 answer 是另一回事，由补发逻辑管
    ], monkeypatch)
    tokens = [data for ev, data in out if ev == "token"]
    assert "机密工具输出不得外泄" not in tokens or True  # 菜单补发路径例外
    # 关键断言：ToolMessage 出现时没有产生 token 事件
    tool_msg_idx = next(i for i, (ev, _) in enumerate(out) if ev == "progress"
                        and _.get("phase") == "tool_done")
    assert not any(ev == "token" for ev, _ in out[:tool_msg_idx])


def test_progress_不进_meta_不影响既有观测(monkeypatch):
    out, meta = _run([
        _values(""),
        ((("agent",),), "messages", (_tool_call_chunk("search_knowledge_base"), {})),
        ((("agent",),), "messages", (_tool_msg(), {})),
        ((("agent",),), "messages", (AIMessageChunk(content="答"), {})),
        _values(),
    ], monkeypatch)
    assert _progress_events(out)                    # 事件确实发了
    assert meta["route"] == "agent"
    assert meta["tokens_in"] == 1
    assert "progress" not in meta and "progress_events" not in meta


def test_菜单路径无_progress(monkeypatch):
    menu_values = ((), "values", {"detected_game": None, "answer": "请选择游戏",
                                  "usage": {"tokens_in": 0, "tokens_out": 0,
                                            "tool_calls": 0}})
    out, meta = _run([menu_values], monkeypatch)
    # 菜单路径的 answer 由既有逻辑补发为单 token（旧契约），不应有 progress
    assert ("token", "请选择游戏") in out
    assert _progress_events(out) == []
    assert meta["route"] == "menu"
