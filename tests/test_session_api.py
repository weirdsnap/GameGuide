#!/usr/bin/env python3
"""会话接口（I-23）的处理器层测试：身份解析、双写、装配、降级。

规格：docs/SESSION_STORE_IMPL.md §4–§5、§10。
全部离线：ask/ask_stream 用假实现，存储指向 tmp_path（conftest 的 autouse fixture）。
"""

import asyncio
import sys
from pathlib import Path

import api_server
from rag_agent import store

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_helpers  # noqa: E402


def _meta(**kw):
    base = {"route": "agent", "game": "baldurs_gate3", "tokens_in": 100,
            "tokens_out": 20, "tool_calls": 1}
    base.update(kw)
    return base


def _fresh(monkeypatch, tmp_path, *, ask=None, ask_stream=None):
    """替换额度/限速/Agent 入口，返回 (app_factory, seen) —— seen 收集传给 ask 的入参。"""
    seen = []

    def default_ask(question, history=None, verbose=False, meta=None):
        seen.append({"question": question, "history": history})
        if meta is not None:
            meta.update(_meta(progress_level=2, progress_source="question"))
        return "答案：" + question

    async def default_stream(question, history=None, verbose=False, meta=None):
        seen.append({"question": question, "history": history})
        yield "token", "答案："
        yield "token", question
        if meta is not None:
            meta.update(_meta())

    api_helpers.isolate_app_state(monkeypatch, tmp_path)
    monkeypatch.setattr(api_server, "ask", ask or default_ask)
    monkeypatch.setattr(api_server, "ask_stream", ask_stream or default_stream)
    return api_server.create_app, seen


def _call(factory, method, path, payload=None):
    return api_helpers.call_json(factory, method, path, payload)


# ====== 身份解析 ======

def test_ask_without_ids_creates_identity(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    status, body = _call(factory, "POST", "/api/ask", {"question": "螳螂爪怎么拿"})
    assert status == 200
    assert len(body["session_id"]) == 32 and len(body["client_id"]) == 32
    row = store.get_session(body["session_id"], db_path=api_server.SESSION_DB)
    assert row["client_id"] == body["client_id"] and row["n_turns"] == 1


def test_second_question_reuses_session(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    _, first = _call(factory, "POST", "/api/ask", {"question": "第一问"})
    _, second = _call(factory, "POST", "/api/ask",
                      {"question": "第二问", "session_id": first["session_id"],
                       "client_id": first["client_id"]})
    assert second["session_id"] == first["session_id"]
    rows = store.session_rows(first["session_id"], db_path=api_server.SESSION_DB)
    assert [r["seq"] for r in rows] == [1, 2]


def test_other_client_cannot_hijack_session(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    _, mine = _call(factory, "POST", "/api/ask", {"question": "我的问题"})
    _, other = _call(factory, "POST", "/api/ask",
                     {"question": "偷看", "session_id": mine["session_id"],
                      "client_id": "f" * 32})
    assert other["session_id"] != mine["session_id"]     # 拒绝复用，新建
    assert store.session_rows(mine["session_id"], db_path=api_server.SESSION_DB)[0]["question"] == "我的问题"


def test_expired_session_id_is_replaced_silently(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    _, body = _call(factory, "POST", "/api/ask",
                    {"question": "问问", "session_id": "a" * 32, "client_id": "c" * 32})
    assert body["session_id"] != "a" * 32
    assert store.get_session(body["session_id"], db_path=api_server.SESSION_DB) is not None


# ====== 装配：服务端历史 vs 请求体历史 ======

def test_server_history_overrides_request_body(monkeypatch, tmp_path):
    factory, seen = _fresh(monkeypatch, tmp_path)
    _, first = _call(factory, "POST", "/api/ask", {"question": "第一章的问"})
    monkeypatch.setattr(api_server, "SERVER_HISTORY", True)
    _call(factory, "POST", "/api/ask",
          {"question": "接着问", "session_id": first["session_id"],
           "client_id": first["client_id"],
           "history": [{"role": "user", "content": "前端伪造的历史"}]})
    hist = seen[-1]["history"]
    contents = [m["content"] for m in hist]
    assert "第一章的问" in contents and "前端伪造的历史" not in contents
    assert contents[0] == "第一章的问"          # user 开头，回答紧随
    assert contents[1].startswith("答案：")


def test_legacy_mode_still_uses_request_history(monkeypatch, tmp_path):
    factory, seen = _fresh(monkeypatch, tmp_path)
    monkeypatch.setattr(api_server, "SERVER_HISTORY", False)
    _call(factory, "POST", "/api/ask",
          {"question": "新问", "history": [{"role": "user", "content": "旧问"}]})
    assert [m["content"] for m in seen[-1]["history"]] == ["旧问"]


def test_menu_round_is_not_stored(monkeypatch, tmp_path):
    def menu_ask(question, history=None, verbose=False, meta=None):
        meta.update({"route": "menu", "game": None, "tokens_in": 0, "tokens_out": 0,
                     "tool_calls": 0})
        return "请先告诉我是哪款游戏"

    factory, _ = _fresh(monkeypatch, tmp_path, ask=menu_ask)
    _, body = _call(factory, "POST", "/api/ask", {"question": "随便问问"})
    assert store.session_rows(body["session_id"], db_path=api_server.SESSION_DB) == []
    assert store.get_session(body["session_id"], db_path=api_server.SESSION_DB)["n_turns"] == 0


# ====== 流式 ======

def test_stream_stores_answer_and_returns_ids(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    status, text = _call(factory, "POST", "/api/ask/stream", {"question": "螳螂爪"})
    assert status == 200
    done = [e for e in api_helpers.sse_events(text) if e["type"] == "done"][0]
    assert done["session_id"]
    rows = store.session_rows(done["session_id"], db_path=api_server.SESSION_DB)
    assert rows[0]["answer"] == "答案：螳螂爪"


def test_stream_interruption_stores_partial(monkeypatch, tmp_path):
    async def broken_stream(question, history=None, verbose=False, meta=None):
        yield "token", "半截答案"
        if meta is not None:
            meta.update({"route": "agent", "game": "baldurs_gate3"})
        raise RuntimeError("连接断了")

    factory, _ = _fresh(monkeypatch, tmp_path, ask_stream=broken_stream)
    client_id = "c" * 32
    _, text = _call(factory, "POST", "/api/ask/stream",
                    {"question": "问问", "client_id": client_id})
    events = api_helpers.sse_events(text)
    # 断流后没有 done 事件，但答案已经按 partial 落库
    assert not any(e["type"] == "done" for e in events)
    sessions = store.list_sessions(client_id, db_path=api_server.SESSION_DB)
    assert len(sessions) == 1
    row = store.session_rows(sessions[0]["session_id"],
                             db_path=api_server.SESSION_DB)[0]
    assert row["answer"] == "半截答案" and row["answer_partial"] is True


# ====== 会话查询路由 ======

def test_list_sessions_requires_client_id(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    status, body = _call(factory, "GET", "/api/sessions")
    assert status == 200 and body["sessions"] == []


def test_list_sessions_returns_recent(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    _, s1 = _call(factory, "POST", "/api/ask", {"question": "第一段"})
    _, s2 = _call(factory, "POST", "/api/ask",
                  {"question": "第二段", "client_id": s1["client_id"]})
    status, body = _call(factory, "GET", f"/api/sessions?client_id={s1['client_id']}")
    assert status == 200
    ids = [s["session_id"] for s in body["sessions"]]
    assert set(ids) == {s1["session_id"], s2["session_id"]}
    assert all(s["turns_available"] == 1 for s in body["sessions"])


def test_session_detail_and_404(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    _, body = _call(factory, "POST", "/api/ask", {"question": "问一句"})
    status, detail = _call(factory, "GET", f"/api/sessions/{body['session_id']}")
    assert status == 200
    assert detail["turns"][0]["question"] == "问一句"
    assert detail["turns"][0]["answer_expired"] is False
    status404, _ = _call(factory, "GET", "/api/sessions/" + "0" * 32)
    assert status404 == 404


def test_session_reset_and_delete(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    _, body = _call(factory, "POST", "/api/ask", {"question": "第三章"})
    sid, cid = body["session_id"], body["client_id"]
    assert store.get_session(sid, db_path=api_server.SESSION_DB)["progress"] == 2

    status, _ = _call(factory, "POST", f"/api/sessions/{sid}/reset")
    assert status == 200
    assert store.get_session(sid, db_path=api_server.SESSION_DB)["progress"] is None

    status, res = _call(factory, "DELETE", f"/api/sessions/{sid}?client_id={cid}")
    assert status == 200 and res["deleted"] is True
    row = store.get_session(sid, db_path=api_server.SESSION_DB)
    assert row is not None and row["hidden_at"] is not None   # 软删：行还在


# ====== 降级 ======

def test_store_failure_degrades_to_legacy(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)

    def boom(*a, **kw):
        raise store.StoreError("库坏了")

    monkeypatch.setattr(api_server.store, "get_session", boom)
    monkeypatch.setattr(api_server.store, "ensure_session", boom)
    status, body = _call(factory, "POST", "/api/ask", {"question": "还能答吗"})
    assert status == 200
    assert body["answer"] == "答案：还能答吗"
    assert body["session_id"] is None          # 存储不可用：不假装有会话


def test_init_failure_still_serves(monkeypatch, tmp_path):
    """建表失败（磁盘满/权限）不影响服务启动，问答照常。"""
    factory, _ = _fresh(monkeypatch, tmp_path)

    def boom(*a, **kw):
        raise store.StoreError("建表失败")

    monkeypatch.setattr(api_server.store, "init_db", boom)
    monkeypatch.setattr(api_server.store, "ensure_session", boom)
    monkeypatch.setattr(api_server.store, "get_session", boom)
    status, body = _call(factory, "POST", "/api/ask", {"question": "磁盘满了"})
    assert status == 200 and body["session_id"] is None


def test_session_routes_report_503_when_store_down(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    _, body = _call(factory, "POST", "/api/ask", {"question": "先建一个"})
    sid = body["session_id"]

    def boom(*a, **kw):
        raise store.StoreError("库坏了")

    monkeypatch.setattr(api_server.store, "get_session", boom)
    assert _call(factory, "GET", f"/api/sessions/{sid}")[0] == 503
    assert _call(factory, "POST", f"/api/sessions/{sid}/reset")[0] == 503
    monkeypatch.setattr(api_server.store, "hide_session", boom)
    assert _call(factory, "DELETE", f"/api/sessions/{sid}?client_id={'c'*32}")[0] == 503


def test_reset_missing_session_is_404(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    assert _call(factory, "POST", "/api/sessions/" + "9" * 32 + "/reset")[0] == 404
    status, body = _call(factory, "DELETE", "/api/sessions/" + "9" * 32 + "?client_id=" + "c" * 32)
    assert status == 200 and body["deleted"] is False


# ====== 进度与观测接线（不跑图，直接喂 state）======

def test_meta_from_state_carries_progress():
    """I-14 的通道：detect 节点算出的进度必须能到达 save_turn，否则 session 进度永远是空。"""
    from rag_agent.multi_agent import _meta_from_state

    meta = _meta_from_state({
        "detected_game": "baldurs_gate3",
        "progress_level": 3,
        "progress_source": "question",
        "usage": {"tokens_in": 10, "tokens_out": 2, "tool_calls": 1},
    })
    assert meta["progress_level"] == 3
    assert meta["progress_source"] == "question"


def test_meta_from_state_omits_progress_when_unknown():
    from rag_agent.multi_agent import _meta_from_state

    meta = _meta_from_state({"detected_game": "hollow_knight", "usage": {}})
    assert "progress_level" not in meta       # 未知就不写，避免落库时被当成 0 覆盖已有进度


def test_turn_record_carries_session_ids():
    rec = api_server.turn_record(ip="1.2.3.4", question="问", elapsed=1.0,
                                 meta={"route": "agent", "game": "hollow_knight"},
                                 session_id="s" * 32, client_id="c" * 32)
    assert rec["session_id"] == "s" * 32 and rec["client_id"] == "c" * 32
    bare = api_server.turn_record(ip="1.2.3.4", question="问", elapsed=1.0, meta={})
    assert "session_id" not in bare and "client_id" not in bare


# ====== 流式心跳（I-25）======

def test_stream_writes_first_byte_and_keepalive(monkeypatch, tmp_path):
    """移动端 Safari 会按「响应体多久没有字节」判死连接（12:37 那轮 iPhone 22.5s 断）。

    契约两条：
      1. prepare 之后立刻写 `: open`，首字节不等第一次 LLM 返回；
      2. 上游长时间没有事件时，每 keepalive_s 写一个 `: keepalive` 注释行。
    注释行不是 `data:` 开头，客户端解析器会忽略，不影响事件流。
    """
    monkeypatch.setitem(api_server.config, "keepalive_s", 0.05)

    async def slow_stream(question, history=None, verbose=False, meta=None):
        await asyncio.sleep(0.15)             # 跨越若干个心跳周期
        yield "token", "慢答案"
        if meta is not None:
            meta.update(_meta())

    factory, _ = _fresh(monkeypatch, tmp_path, ask_stream=slow_stream)
    status, text = _call(factory, "POST", "/api/ask/stream", {"question": "慢"})
    assert status == 200
    assert text.startswith(": open")          # 首字节立刻落地
    assert ": keepalive" in text              # 静默期有心跳
    tokens = [e for e in api_helpers.sse_events(text) if e["type"] == "token"]
    assert tokens and tokens[0]["content"] == "慢答案"


# ====== 历史会话删除（前端「历史」浮层）======

def test_delete_session_requires_client_id(monkeypatch, tmp_path):
    """不带 client_id 一律 400：没这个参数就没人能证明会话归属。"""
    factory, _ = _fresh(monkeypatch, tmp_path)
    _, body = _call(factory, "POST", "/api/ask", {"question": "要删的"})
    sid = body["session_id"]
    assert _call(factory, "DELETE", f"/api/sessions/{sid}")[0] == 400
    assert store.get_session(sid, db_path=api_server.SESSION_DB) is not None


def test_delete_session_by_owner_hides_but_keeps_data(monkeypatch, tmp_path):
    """用户点删除 = 对他不可见。库里 session 行、turns、原文全都留着（后台分析要用）。"""
    factory, _ = _fresh(monkeypatch, tmp_path)
    _, body = _call(factory, "POST", "/api/ask", {"question": "要删的"})
    sid, cid = body["session_id"], body["client_id"]

    status, data = _call(factory, "DELETE", f"/api/sessions/{sid}?client_id={cid}")
    assert status == 200 and data["deleted"] is True

    # 用户视角：列表没有、详情 404
    assert all(x["session_id"] != sid
               for x in store.list_sessions(cid, db_path=api_server.SESSION_DB))
    assert _call(factory, "GET", f"/api/sessions/{sid}")[0] == 404

    # 后台视角：一行不少
    row = store.get_session(sid, db_path=api_server.SESSION_DB)
    assert row is not None and row["hidden_at"] is not None
    rows = store.session_rows(sid, db_path=api_server.SESSION_DB)
    assert rows and rows[0]["question"] == "要删的"

    # 幂等
    status, data = _call(factory, "DELETE", f"/api/sessions/{sid}?client_id={cid}")
    assert status == 200 and data["deleted"] is False


def test_delete_session_of_other_client_is_noop(monkeypatch, tmp_path):
    factory, _ = _fresh(monkeypatch, tmp_path)
    _, body = _call(factory, "POST", "/api/ask", {"question": "别人的"})
    sid = body["session_id"]

    status, data = _call(factory, "DELETE", f"/api/sessions/{sid}?client_id={'d' * 32}")
    assert status == 200 and data["deleted"] is False
    assert store.get_session(sid, db_path=api_server.SESSION_DB) is not None
