"""用户反馈（点赞/点踩）的离线测试：纯函数 + 两个端点。

不碰 LLM、不碰真实 data 目录（FEEDBACK_DIR 打到 tmp_path）。
"""

import asyncio
import json

import pytest

import api_server


# ── 纯函数 ──────────────────────────────────────────────

def test_反馈记录字段与截断():
    rec = api_server.feedback_record(
        turn_id="abcdef123456", rating=-1, ip="203.0.113.7",
        question="x" * 500, game="baldurs_gate3", route="agent",
        comment="y" * 500,
    )
    assert rec["turn_id"] == "abcdef123456"
    assert rec["rating"] == -1
    assert rec["game"] == "baldurs_gate3"
    assert len(rec["question"]) == 120
    assert len(rec["comment"]) == 300
    assert isinstance(rec["ts"], int)


def test_同一轮重复提交只算最后一次():
    records = [
        {"turn_id": "t1", "rating": -1, "game": "oni", "route": "agent"},
        {"turn_id": "t1", "rating": 1, "game": "oni", "route": "agent"},
        {"turn_id": "t2", "rating": -1, "game": "oni", "route": "agent"},
    ]
    summary = api_server.feedback_summary(records)
    assert summary["total"] == 2
    assert summary["up"] == 1
    assert summary["down"] == 1
    assert summary["by_game"]["oni"] == {"up": 1, "down": 1}
    assert [r["turn_id"] for r in summary["recent_down"]] == ["t2"]


def test_turn_id_格式校验():
    assert api_server.TURN_ID_RE.match("abcdef123456")
    assert not api_server.TURN_ID_RE.match("ABCDEF123456")
    assert not api_server.TURN_ID_RE.match("short")
    assert not api_server.TURN_ID_RE.match("abc-123")


# ── 端点 ────────────────────────────────────────────────

def _fresh(monkeypatch, tmp_path, *, ask_stream=None):
    feedback_dir = tmp_path / "feedback"
    monkeypatch.setattr(api_server, "FEEDBACK_DIR", feedback_dir)
    monkeypatch.setattr(api_server, "log_feedback", api_server.log_feedback)
    monkeypatch.setattr(api_server, "rate_limiter", api_server.RateLimiter())
    quota = api_server.DailyQuota(tmp_path / "quota.json", limit=100)
    monkeypatch.setattr(api_server, "quota", quota)
    if ask_stream is not None:
        monkeypatch.setattr(api_server, "ask_stream", ask_stream)
    return api_server.create_app


def _post(app_factory, path, payload):
    from aiohttp.test_utils import TestClient, TestServer

    async def _run():
        async with TestClient(TestServer(app_factory())) as client:
            resp = await client.post(path, json=payload)
            return resp.status, await resp.json()

    return asyncio.run(_run())


def _get(app_factory, path):
    from aiohttp.test_utils import TestClient, TestServer

    async def _run():
        async with TestClient(TestServer(app_factory())) as client:
            resp = await client.get(path)
            return resp.status, await resp.json()

    return asyncio.run(_run())


def test_提交反馈落盘并可聚合(monkeypatch, tmp_path):
    app_factory = _fresh(monkeypatch, tmp_path)

    status, data = _post(app_factory, "/api/feedback", {
        "turn_id": "abcdef123456", "rating": -1,
        "question": "明萨拉用什么武器", "game": "baldurs_gate3", "route": "agent",
    })
    assert status == 200
    assert data == {"ok": True, "turn_id": "abcdef123456", "rating": -1}

    files = list((tmp_path / "feedback").glob("*.jsonl"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert saved["rating"] == -1
    assert saved["question"] == "明萨拉用什么武器"

    status, stats = _get(app_factory, "/api/feedback/stats?days=7")
    assert status == 200
    assert stats["total"] == 1
    assert stats["down"] == 1
    assert stats["by_game"]["baldurs_gate3"]["down"] == 1
    assert stats["recent_down"][0]["turn_id"] == "abcdef123456"


def test_非法rating与非法turn_id被拒绝(monkeypatch, tmp_path):
    app_factory = _fresh(monkeypatch, tmp_path)

    status, data = _post(app_factory, "/api/feedback",
                         {"turn_id": "abcdef123456", "rating": 0})
    assert status == 400
    assert "rating" in data["error"]

    status, data = _post(app_factory, "/api/feedback",
                         {"turn_id": "../etc/passwd", "rating": 1})
    assert status == 400

    assert not list((tmp_path / "feedback").glob("*.jsonl"))


def test_点赞也计入统计(monkeypatch, tmp_path):
    app_factory = _fresh(monkeypatch, tmp_path)
    _post(app_factory, "/api/feedback", {"turn_id": "aaaabbbbcccc", "rating": 1})
    status, stats = _get(app_factory, "/api/feedback/stats?days=7")
    assert status == 200
    assert stats["up"] == 1
    assert stats["down"] == 0


# ── 流式 done 事件带 turn_id ────────────────────────────

def test_流式结束时下发turn_id(monkeypatch, tmp_path):
    async def fake_stream(question, history=None, verbose=False, meta=None):
        meta.update({"route": "agent", "game": "oni", "tokens_in": 1,
                     "tokens_out": 1, "tool_calls": 0})
        yield "token", "氧气"
        yield "done", {}

    app_factory = _fresh(monkeypatch, tmp_path, ask_stream=fake_stream)

    from aiohttp.test_utils import TestClient, TestServer

    async def _run():
        async with TestClient(TestServer(app_factory())) as client:
            resp = await client.post("/api/ask/stream", json={"question": "高压制氧"})
            body = await resp.text()
            return resp.status, body

    status, body = asyncio.run(_run())
    assert status == 200
    done_lines = [ln for ln in body.splitlines() if ln.startswith("data: ") and '"done"' in ln]
    assert done_lines, body
    payload = json.loads(done_lines[-1][6:])
    assert api_server.TURN_ID_RE.match(payload["turn_id"])
