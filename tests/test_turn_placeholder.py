#!/usr/bin/env python3
"""「思考中刷新就丢这一轮」的回归契约。

场景（用户实测）：新建对话 → 提第一个问题 → 回答还没生成就刷新页面。
连接断掉后上游生成器被取消，一个 token 都没产出；修复前这一轮彻底消失，
历史面板里只剩一个空会话，问题本身都找不回来。

修法：请求进来先落占位行（answer=NULL），答完再补；菜单/未识别轮标 discarded。
"""

import sys
from pathlib import Path

import pytest

import api_server
from rag_agent import store

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_helpers  # noqa: E402


@pytest.fixture(autouse=True)
def _tables():
    """store 层用例直接读写 sqlite，得先建表（conftest 只把路径指到 tmp）。"""
    store.init_db(api_server.SESSION_DB)


def _meta(**kw):
    base = {"route": "agent", "game": "baldurs_gate3", "tokens_in": 100,
            "tokens_out": 20, "tool_calls": 1}
    base.update(kw)
    return base


def _fresh(monkeypatch, tmp_path, *, ask=None, ask_stream=None):
    api_helpers.isolate_app_state(monkeypatch, tmp_path)
    if ask is not None:
        monkeypatch.setattr(api_server, "ask", ask)
    if ask_stream is not None:
        monkeypatch.setattr(api_server, "ask_stream", ask_stream)
    return api_server.create_app


def _db():
    return api_server.SESSION_DB


# ====== store 层：begin → finish → discard ======

def test_begin_turn_keeps_question_before_any_answer(tmp_path):
    """占位行在回答之前就存在，且不算进会话元数据。"""
    sid = "s" * 32
    store.ensure_session(session_id=sid, client_id="c" * 32, db_path=_db())
    store.begin_turn(session_id=sid, turn_id="t1", question="明斯克在哪", db_path=_db())

    rows = store.session_rows(sid, db_path=_db())
    assert [r["question"] for r in rows] == ["明斯克在哪"]
    assert rows[0]["answer"] is None
    assert rows[0]["answer_missing"] is True      # 与「过期」要能区分

    sess = store.get_session(sid, db_path=_db())
    assert sess["n_turns"] == 0                   # 还没答完，不算一轮
    assert sess["title"] is None


def test_unanswered_turn_stays_out_of_model_context(tmp_path):
    """占位行可以给用户看，但不能喂回模型（没有答案的一问接不上）。"""
    sid = "s" * 32
    store.ensure_session(session_id=sid, client_id="c" * 32, db_path=_db())
    store.begin_turn(session_id=sid, turn_id="t1", question="问了一句就断了", db_path=_db())

    assert store.session_turns(sid, db_path=_db()) == []


def test_finish_turn_completes_placeholder(tmp_path):
    sid = "s" * 32
    store.ensure_session(session_id=sid, client_id="c" * 32, db_path=_db())
    seq = store.begin_turn(session_id=sid, turn_id="t1", question="螳螂爪怎么拿",
                           db_path=_db())
    store.finish_turn(turn_id="t1", answer="在螳螂村", route="agent", game="hollow_knight",
                      tokens_in=10, tokens_out=5, tool_calls=1, elapsed_ms=800,
                      db_path=_db())

    row = store.session_rows(sid, db_path=_db())[0]
    assert seq == 1 and row["seq"] == 1
    assert row["answer"] == "在螳螂村"
    assert row["answer_missing"] is False and row["answer_expired"] is False

    sess = store.get_session(sid, db_path=_db())
    assert sess["n_turns"] == 1
    assert sess["title"] == "螳螂爪怎么拿"
    assert sess["game"] == "hollow_knight"
    # 上下文里现在有完整一轮
    assert store.session_turns(sid, db_path=_db())[1]["content"] == "在螳螂村"


def test_discarded_turn_invisible_but_kept(tmp_path):
    """菜单轮：对用户不可见，但行还在（数据只在「可见性」这一层处理）。"""
    sid = "s" * 32
    store.ensure_session(session_id=sid, client_id="c" * 32, db_path=_db())
    store.begin_turn(session_id=sid, turn_id="t1", question="你好", db_path=_db())
    store.discard_turn(turn_id="t1", db_path=_db())

    assert store.session_rows(sid, db_path=_db()) == []
    assert store.session_turns(sid, db_path=_db()) == []
    raw = store._read_all("SELECT question, discarded FROM turns WHERE turn_id=?",
                          ("t1",), db_path=_db())
    assert raw and raw[0]["discarded"] == 1


def test_discard_after_finish_freezes_turn(tmp_path):
    """已被 discard 的 turn 再 finish 不会写进去（顺序错也不会污染历史）。"""
    sid = "s" * 32
    store.ensure_session(session_id=sid, client_id="c" * 32, db_path=_db())
    store.begin_turn(session_id=sid, turn_id="t1", question="你好", db_path=_db())
    store.discard_turn(turn_id="t1", db_path=_db())
    store.finish_turn(turn_id="t1", answer="菜单", route="menu", db_path=_db())

    assert store.session_rows(sid, db_path=_db()) == []
    assert store.get_session(sid, db_path=_db())["n_turns"] == 0


# ====== 处理器层：断连 / 菜单 ======

def test_stream_disconnect_without_tokens_still_saves_question(monkeypatch, tmp_path):
    """核心回归：上游一个 token 都没出就断了，问题仍要留在历史里。"""
    async def bomb_stream(question, history=None, verbose=False, meta=None):
        meta.update({"route": "agent", "game": "baldurs_gate3"})
        raise ConnectionResetError("客户端断开")
        yield "token", "永远不会到"      # noqa: unreachable — 让它保持生成器语义

    factory = _fresh(monkeypatch, tmp_path, ask_stream=bomb_stream)
    status, text = api_helpers.call_raw(factory, "POST", "/api/ask/stream",
                                        {"question": "明斯克在哪"})
    assert status == 200

    events = api_helpers.sse_events(text)
    sid = next(e["session_id"] for e in events if e["type"] == "session")
    assert sid

    rows = store.session_rows(sid, db_path=_db())
    assert [r["question"] for r in rows] == ["明斯克在哪"]
    assert rows[0]["answer"] is None and rows[0]["answer_missing"] is True
    # 历史列表里也能看到（不是空会话）
    listed = store.list_sessions(store.get_session(sid, db_path=_db())["client_id"],
                                 db_path=_db())
    assert [s["session_id"] for s in listed] == [sid]


def test_stream_menu_turn_leaves_nothing(monkeypatch, tmp_path):
    async def menu_stream(question, history=None, verbose=False, meta=None):
        meta.update({"route": "menu"})
        yield "token", "请问你想问哪款游戏？"

    factory = _fresh(monkeypatch, tmp_path, ask_stream=menu_stream)
    status, text = api_helpers.call_raw(factory, "POST", "/api/ask/stream",
                                        {"question": "你好"})
    assert status == 200
    sid = next(e["session_id"] for e in api_helpers.sse_events(text)
               if e["type"] == "session")
    assert store.session_rows(sid, db_path=_db()) == []


def test_stream_success_writes_one_complete_turn(monkeypatch, tmp_path):
    async def ok_stream(question, history=None, verbose=False, meta=None):
        yield "token", "在石王藏身处"
        meta.update(_meta())

    factory = _fresh(monkeypatch, tmp_path, ask_stream=ok_stream)
    _, text = api_helpers.call_raw(factory, "POST", "/api/ask/stream",
                                   {"question": "明斯克在哪"})
    sid = next(e["session_id"] for e in api_helpers.sse_events(text)
               if e["type"] == "session")
    rows = store.session_rows(sid, db_path=_db())
    assert len(rows) == 1
    assert rows[0]["answer"] == "在石王藏身处"
    assert rows[0]["answer_partial"] is False
    assert store.get_session(sid, db_path=_db())["n_turns"] == 1


def test_ask_placeholder_exists_while_thinking(monkeypatch, tmp_path):
    """非流式：LLM 还在跑的时候库里就已经有这一行了。"""
    seen = {}

    def slow_ask(question, history=None, verbose=False, meta=None):
        seen["rows"] = store._read_all(
            "SELECT question, answer FROM turns WHERE question=?", (question,), db_path=_db())
        meta.update(_meta())
        return "答案"

    factory = _fresh(monkeypatch, tmp_path, ask=slow_ask)
    status, body = api_helpers.call_json(factory, "POST", "/api/ask",
                                         {"question": "铁手套在哪"})
    assert status == 200
    assert seen["rows"] == [{"question": "铁手套在哪", "answer": None}]
    row = store.session_rows(body["session_id"], db_path=_db())[0]
    assert row["answer"] == "答案" and row["answer_missing"] is False
