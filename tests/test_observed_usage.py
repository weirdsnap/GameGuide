"""第 0 步观测层的离线单测（不调 LLM、不读向量库）。

覆盖：
  usage_from_messages — token / 工具调用汇总（含回退与异常输入）
  menu_node / _agent_answer — 节点层面的 usage 字段
  _make_llm — stream_usage 开关（流式 token 统计为 0 的根因）
  _meta_from_state / ask / ask_stream — meta 出口与异常分支
  api_server.turn_record — 结构化记录字段
  api_server.logger — 回归：曾经 logger 未定义导致异常分支 NameError
"""

import asyncio
import logging

import pytest

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from rag_agent import graph as g
from rag_agent import multi_agent as m


# ── helpers ────────────────────────────────────────────────

def _ai(content="ok", *, tokens=None, tool_calls=None, response_metadata=None):
    """造一条 AIMessage，可带 usage_metadata / tool_calls / response_metadata。"""
    kwargs = {"content": content}
    if tokens is not None:
        kwargs["usage_metadata"] = {
            "input_tokens": tokens[0], "output_tokens": tokens[1], "total_tokens": tokens[0] + tokens[1],
        }
    if tool_calls is not None:
        kwargs["tool_calls"] = tool_calls
    if response_metadata is not None:
        kwargs["response_metadata"] = response_metadata
    return AIMessage(**kwargs)


def _tool_call(name="search_knowledge_base"):
    return {"name": name, "args": {"query": "x"}, "id": "call_1"}


# ── usage_from_messages ────────────────────────────────────

def test_usage_汇总多轮工具调用():
    messages = [
        HumanMessage(content="q"),
        _ai("", tokens=(100, 20), tool_calls=[_tool_call()]),
        ToolMessage(content="doc", tool_call_id="call_1"),
        _ai("答案", tokens=(200, 50), tool_calls=[_tool_call(), _tool_call()]),
    ]
    assert g.usage_from_messages(messages) == {
        "tokens_in": 300, "tokens_out": 70, "tool_calls": 3,
    }


def test_usage_无_metadata_时回退_response_metadata():
    msg = _ai("答案", response_metadata={"token_usage": {"prompt_tokens": 11, "completion_tokens": 7}})
    assert g.usage_from_messages([msg]) == {"tokens_in": 11, "tokens_out": 7, "tool_calls": 0}


def test_usage_两者都缺时记零():
    assert g.usage_from_messages([_ai("答案")]) == g.ZERO_USAGE


def test_usage_空轨迹与_None_都安全():
    assert g.usage_from_messages([]) == g.ZERO_USAGE
    assert g.usage_from_messages(None) == g.ZERO_USAGE


def test_usage_忽略非_AIMessage():
    # ToolMessage / HumanMessage 不应贡献 token 或 tool_calls
    assert g.usage_from_messages([HumanMessage(content="q"), ToolMessage(content="t", tool_call_id="c")]) == g.ZERO_USAGE


def test_usage_不修改_ZERO_USAGE_常量():
    g.menu_node({"menu_text": "菜单"})
    g.menu_node({"menu_text": "菜单"})["usage"]["tokens_in"] = 999
    assert g.ZERO_USAGE["tokens_in"] == 0, "menu_node 必须返回 ZERO_USAGE 的拷贝"


# ── 节点层：menu / agent ───────────────────────────────────

def test_menu_node_返回全零_usage():
    out = g.menu_node({"menu_text": "请选择游戏"})
    assert out["answer"] == "请选择游戏"
    assert out["usage"] == g.ZERO_USAGE


def test_agent_answer_带出_usage():
    trajectory = [
        HumanMessage(content="q"),
        _ai("", tokens=(80, 10), tool_calls=[_tool_call()]),
        ToolMessage(content="doc", tool_call_id="call_1"),
        _ai("最终答案", tokens=(150, 40)),
    ]
    out = g._agent_answer({"messages": trajectory})
    assert out["answer"] == "最终答案"
    assert out["usage"] == {"tokens_in": 230, "tokens_out": 50, "tool_calls": 1}


def test_agent_answer_空轨迹不崩():
    out = g._agent_answer({"messages": []})
    assert out["usage"] == g.ZERO_USAGE
    assert out["answer"]


# ── _make_llm：stream_usage 开关 ────────────────────────────

def test_make_llm_开启_stream_usage(monkeypatch):
    captured = {}

    class _FakeLLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.model_name = kwargs.get("model")

    monkeypatch.setattr(g, "ChatOpenAI", _FakeLLM)
    llm = g._make_llm({"model_name": "custom-model"}, streaming=True)

    assert captured["stream_usage"] is True, "流式路径不开 stream_usage，token 统计会恒为 0"
    assert captured["streaming"] is True
    assert captured["model"] == "custom-model"
    assert llm.model_name == "custom-model"


def test_make_llm_非流式也带_stream_usage_且不污染配置(monkeypatch):
    captured = {}

    class _FakeLLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(g, "ChatOpenAI", _FakeLLM)
    g._make_llm({}, streaming=False)

    assert captured["stream_usage"] is True
    assert "streaming" not in captured
    assert "stream_usage" not in g.LLM_CONFIG, "不能把观测开关写回全局配置"


# ── _meta_from_state：route 映射 ───────────────────────────

@pytest.mark.parametrize("detected,route,game", [
    ("hollow_knight", "agent", "hollow_knight"),
    ("__llm_fallback__", "fallback", None),
    (None, "menu", None),
])
def test_meta_route_映射(detected, route, game):
    meta = m._meta_from_state({"detected_game": detected})
    assert meta["route"] == route
    assert meta["game"] == game
    assert meta["tokens_in"] == 0 and meta["tokens_out"] == 0 and meta["tool_calls"] == 0


def test_meta_带出_usage():
    meta = m._meta_from_state({
        "detected_game": "terraria",
        "usage": {"tokens_in": 30, "tokens_out": 9, "tool_calls": 2},
    })
    assert (meta["tokens_in"], meta["tokens_out"], meta["tool_calls"]) == (30, 9, 2)


# ── 请求状态隔离 ───────────────────────────────────────────

def test_initial_state_只从当前请求历史推导游戏():
    hk_history = [
        {"role": "user", "content": "空洞骑士辐光怎么打"},
        {"role": "assistant", "content": "可以参考知识库"},
    ]
    oni_history = [{"role": "user", "content": "缺氧净水怎么造"}]

    hk_state = m._build_initial_state("它多少血", hk_history, None, False)
    oni_state = m._build_initial_state("它多少血", oni_history, None, False)
    empty_state = m._build_initial_state("它多少血", [], None, False)

    assert hk_state["last_game"] == "hollow_knight"
    assert hk_state["last_game_confirmed"] is True
    assert oni_state["last_game"] == "oni"
    assert empty_state["last_game"] is None
    assert empty_state["last_game_confirmed"] is False


def test_initial_state_忽略无效历史项():
    history = [None, {"role": "assistant", "content": "空洞骑士"},
               {"role": "user", "content": 42}]

    state = m._build_initial_state("它多少血", history, None, False)

    assert state["last_game"] is None
    assert not hasattr(m, "_LAST_GAME")


def test_initial_state_切换或未知游戏会中断旧上下文():
    hk = {"role": "user", "content": "空洞骑士辐光怎么打"}
    switched = [hk, {"role": "user", "content": "换个游戏"}]
    unknown = [hk, {"role": "user", "content": "原神怎么玩"}]

    assert m._game_from_history(switched) is None
    assert m._game_from_history(unknown) is None


# ── ask / ask_stream 的 meta 出口 ──────────────────────────

class _FakeGraph:
    def __init__(self, result=None, exc=None, events=None, stream_exc=None):
        self._result = result or {}
        self._exc = exc
        self._events = events or []
        self._stream_exc = stream_exc

    def invoke(self, state):
        if self._exc:
            raise self._exc
        return self._result

    async def astream(self, state, **kwargs):
        for event in self._events:
            yield event
        if self._stream_exc:
            raise self._stream_exc


def _patch_graph(monkeypatch, fake):
    monkeypatch.setattr(m, "get_graph", lambda: fake)


def test_ask_填充_meta(monkeypatch):
    _patch_graph(monkeypatch, _FakeGraph(result={
        "answer": "答案", "detected_game": "hollow_knight",
        "usage": {"tokens_in": 10, "tokens_out": 5, "tool_calls": 1},
        "last_game": "hollow_knight", "last_game_confirmed": True,
    }))
    meta = {}
    assert m.ask("螳螂爪在哪", meta=meta) == "答案"
    assert meta == {"route": "agent", "game": "hollow_knight",
                    "tokens_in": 10, "tokens_out": 5, "tool_calls": 1}


def test_ask_不传_meta_时行为不变(monkeypatch):
    _patch_graph(monkeypatch, _FakeGraph(result={"answer": "答案", "detected_game": "oni"}))
    assert m.ask("净水怎么造") == "答案"


def test_ask_异常路径填_route_error(monkeypatch):
    _patch_graph(monkeypatch, _FakeGraph(exc=RuntimeError("boom")))
    meta = {}
    out = m.ask("q", meta=meta)
    assert "查询出错" in out
    assert meta["route"] == "error"
    assert meta["tokens_in"] == 0
    assert "boom" in meta["error"]


def test_ask_stream_填充_meta(monkeypatch):
    events = [
        ((), "values", {"detected_game": "terraria", "answer": "",
                        "usage": {"tokens_in": 7, "tokens_out": 3, "tool_calls": 0}}),
        ((("agent",),), "messages", (AIMessageChunk(content="答"), {})),
        ((), "values", {"detected_game": "terraria", "answer": "答",
                        "usage": {"tokens_in": 7, "tokens_out": 3, "tool_calls": 0}}),
    ]
    _patch_graph(monkeypatch, _FakeGraph(events=events))
    meta = {}

    async def _run():
        return [ev async for ev in m.ask_stream("克苏鲁之眼", meta=meta)]

    out = asyncio.run(_run())
    assert ("token", "答") in out
    assert meta["route"] == "agent"
    assert meta["game"] == "terraria"
    assert (meta["tokens_in"], meta["tokens_out"]) == (7, 3)


def test_ask_stream_异常时_route_error(monkeypatch):
    events = [((), "values", {"detected_game": "oni", "answer": "",
                              "usage": {"tokens_in": 5, "tokens_out": 0, "tool_calls": 0}})]
    _patch_graph(monkeypatch, _FakeGraph(events=events, stream_exc=RuntimeError("上游超时")))
    meta = {}

    async def _run():
        return [ev async for ev in m.ask_stream("q", meta=meta)]

    out = asyncio.run(_run())
    assert out[-1][0] == "error"
    assert meta["route"] == "error"
    assert "上游超时" in meta["error"]


def test_ask_stream_消费方收到_error_就_break_时_meta_仍完整(monkeypatch):
    """真实调用方（api_server）收到 error 事件会立即 break，
    生成器不会再被恢复——meta 必须在 yield 之前写好。"""
    _patch_graph(monkeypatch, _FakeGraph(events=[], stream_exc=RuntimeError("上游超时")))
    meta = {}
    seen = []

    async def _run():
        async for ev, data in m.ask_stream("q", meta=meta):
            seen.append(ev)
            if ev == "error":
                break               # 模拟 api_server.handle_ask_stream

    asyncio.run(_run())
    assert seen == ["error"]
    assert meta["route"] == "error"
    assert "上游超时" in meta["error"]


def test_ask_stream_菜单路径补发_token(monkeypatch):
    # 菜单路径没有 LLM token，终态 answer 要作为一个 token 补发（旧行为契约）
    events = [((), "values", {"detected_game": None, "answer": "请选择游戏",
                              "usage": g.ZERO_USAGE})]
    _patch_graph(monkeypatch, _FakeGraph(events=events))
    meta = {}

    async def _run():
        return [ev async for ev in m.ask_stream("随便", meta=meta)]

    out = asyncio.run(_run())
    assert ("token", "请选择游戏") in out
    assert meta["route"] == "menu"
    assert meta["tool_calls"] == 0


# ── api_server：结构化记录 + logger 回归 ───────────────────

def test_turn_record_字段与截断():
    import api_server

    record = api_server.turn_record(
        ip="1.2.3.4", question="问" * 200, elapsed=1.2345,
        meta={"route": "agent", "game": "hollow_knight",
              "tokens_in": 100, "tokens_out": 20, "tool_calls": 2},
    )
    assert record["route"] == "agent"
    assert record["game"] == "hollow_knight"
    assert record["elapsed_ms"] == 1234
    assert len(record["question"]) == 120
    assert record["stream"] is False
    assert "stream_chunks" not in record
    assert "error" not in record


def test_turn_record_流式与错误字段():
    import api_server

    record = api_server.turn_record(
        ip="1.2.3.4", question="q", elapsed=0.5,
        meta={"route": "error", "error": "boom"}, streamed=True, chunks=42,
    )
    assert record["stream"] is True
    assert record["stream_chunks"] == 42
    assert record["error"] == "boom"
    assert record["tokens_in"] == 0


def test_turn_record_容忍空_meta():
    import api_server

    record = api_server.turn_record(ip="x", question="q", elapsed=0, meta=None)
    assert record["route"] is None
    assert record["tokens_out"] == 0


def test_api_server_logger_已定义():
    # 回归：曾用 logger.warning/logger.error 但未定义 logger，异常分支会 NameError
    import api_server

    assert isinstance(api_server.logger, logging.Logger)
