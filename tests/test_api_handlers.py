"""API 处理器层的离线测试（无 LLM 调用，用假 ask/ask_stream）。

覆盖：无密码访问、全局每日额度（含流式）、登录端点兼容、SSE 协议、观测记录。
"""

import asyncio
import json
import time

import api_server
from api_server import DailyQuota

FIXED_TS = 1_700_000_000
FIXED_DAY = time.strftime("%Y-%m-%d", time.localtime(FIXED_TS))   # 按本机时区算，避免时区依赖


def _fresh(monkeypatch, tmp_path, *, limit=100, ask=None, ask_stream=None):
    """构造应用，替换额度、限速与 Agent 入口，返回 (app, records, quota)。"""
    records = []
    monkeypatch.setattr(api_server, "log_turn", records.append)
    monkeypatch.setattr(api_server, "rate_limiter", api_server.RateLimiter())
    quota = DailyQuota(tmp_path / "quota.json", limit=limit, clock=lambda: FIXED_TS)
    monkeypatch.setattr(api_server, "quota", quota)
    if ask is not None:
        monkeypatch.setattr(api_server, "ask", ask)
    if ask_stream is not None:
        monkeypatch.setattr(api_server, "ask_stream", ask_stream)
    # 返回工厂而不是实例：aiohttp 的 Application 绑定事件循环，
    # 一个测试里发多个请求时必须每次新建（asyncio.run 每次都是新循环）
    return api_server.create_app, records, quota


def _post(app_factory, path, payload):
    from aiohttp.test_utils import TestClient, TestServer

    async def _run():
        async with TestClient(TestServer(app_factory())) as client:
            resp = await client.post(path, json=payload)
            return resp.status, await resp.text()

    return asyncio.run(_run())


def _get(app_factory, path):
    from aiohttp.test_utils import TestClient, TestServer

    async def _run():
        async with TestClient(TestServer(app_factory())) as client:
            resp = await client.get(path)
            return resp.status, await resp.json()

    return asyncio.run(_run())


def _ok_ask(question, history=None, verbose=False, meta=None):
    meta.update({"route": "agent", "game": "hollow_knight",
                 "tokens_in": 6401, "tokens_out": 845, "tool_calls": 3})
    return "螳螂爪可以攀墙"


# ── 反向代理后的客户端 IP ────────────────────────────────

def test_可信代理使用转发链中的真实客户端ip():
    from types import SimpleNamespace

    request = SimpleNamespace(
        remote="127.0.0.1",
        headers={"X-Forwarded-For": "203.0.113.10, 127.0.0.1"},
    )

    assert api_server._client_ip(request) == "203.0.113.10"


def test_非可信来源不能伪造转发ip():
    from types import SimpleNamespace

    request = SimpleNamespace(
        remote="198.51.100.20",
        headers={"X-Forwarded-For": "203.0.113.10"},
    )

    assert api_server._client_ip(request) == "198.51.100.20"


def test_可信代理没有转发头时回退到对端ip():
    from types import SimpleNamespace

    request = SimpleNamespace(remote="::1", headers={})

    assert api_server._client_ip(request) == "::1"


# ── 无密码访问 ───────────────────────────────────────────

def test_不带任何_token_也能提问(monkeypatch, tmp_path):
    app, records, _ = _fresh(monkeypatch, tmp_path, ask=_ok_ask)
    status, body = _post(app, "/api/ask", {"question": "螳螂爪怎么拿"})

    assert status == 200
    assert json.loads(body)["answer"] == "螳螂爪可以攀墙"
    assert len(records) == 1


def test_登录端点保留为兼容占位(monkeypatch, tmp_path):
    app, _, _ = _fresh(monkeypatch, tmp_path)
    status, body = _post(app, "/api/login", {"password": "随便填"})

    assert status == 200
    data = json.loads(body)
    assert data["token"]                      # 旧页面拿得到 token，不再校验密码


# ── 全局每日额度 ─────────────────────────────────────────

def test_成功请求返回剩余额度(monkeypatch, tmp_path):
    app, _, _ = _fresh(monkeypatch, tmp_path, limit=3, ask=_ok_ask)
    _, body = _post(app, "/api/ask", {"question": "q"})

    data = json.loads(body)
    assert data["quota"] == {"date": FIXED_DAY, "used": 1, "limit": 3, "remaining": 2}


def test_额度用尽后返回429和明天再来(monkeypatch, tmp_path):
    app, records, _ = _fresh(monkeypatch, tmp_path, limit=1, ask=_ok_ask)
    assert _post(app, "/api/ask", {"question": "第一个"})[0] == 200

    status, body = _post(app, "/api/ask", {"question": "第二个"})
    data = json.loads(body)

    assert status == 429
    assert "明天再来" in data["error"]
    assert data["quota"]["remaining"] == 0
    assert data["quota"]["used"] == 1
    assert len(records) == 1, "被额度挡下的请求不应产生提问记录"


def test_额度用尽时不会调用_agent(monkeypatch, tmp_path):
    calls = []

    def counting_ask(question, history=None, verbose=False, meta=None):
        calls.append(question)
        return "答案"

    app, _, _ = _fresh(monkeypatch, tmp_path, limit=1, ask=counting_ask)
    _post(app, "/api/ask", {"question": "a"})
    _post(app, "/api/ask", {"question": "b"})

    assert calls == ["a"]


def test_额度是全局的_换个_ip_也挡(monkeypatch, tmp_path):
    # 额度存在文件里、不按 IP 分桶：不同 source 共享同一个计数器
    app, _, quota = _fresh(monkeypatch, tmp_path, limit=1, ask=_ok_ask)
    quota.consume()                           # 假设别的访客已经用掉

    status, body = _post(app, "/api/ask", {"question": "q"})
    assert status == 429
    assert "明天再来" in json.loads(body)["error"]


def test_流式接口额度用尽也是429(monkeypatch, tmp_path):
    async def fake_stream(question, history=None, verbose=False, meta=None):
        yield "token", "不该走到这里"

    app, _, quota = _fresh(monkeypatch, tmp_path, limit=1, ask_stream=fake_stream)
    quota.consume()

    status, body = _post(app, "/api/ask/stream", {"question": "q"})
    assert status == 429
    assert "明天再来" in json.loads(body)["error"]


def test_额度端点返回今日状态(monkeypatch, tmp_path):
    app, _, quota = _fresh(monkeypatch, tmp_path, limit=5, ask=_ok_ask)
    quota.consume()

    status, data = _get(app, "/api/quota")
    assert status == 200
    assert data == {"date": FIXED_DAY, "used": 1, "limit": 5, "remaining": 4}


def test_健康检查带额度信息(monkeypatch, tmp_path):
    app, _, _ = _fresh(monkeypatch, tmp_path)
    status, data = _get(app, "/api/status")

    assert status == 200
    assert data["status"] == "ok"
    assert data["quota"]["limit"] == 100


# ── 非流式 /api/ask ──────────────────────────────────────

def test_ask_记录一轮观测(monkeypatch, tmp_path):
    app, records, _ = _fresh(monkeypatch, tmp_path, ask=_ok_ask)
    status, body = _post(app, "/api/ask", {"question": "螳螂爪怎么拿"})

    assert status == 200
    assert "elapsed" in json.loads(body)
    rec = records[0]
    assert rec["route"] == "agent"
    assert rec["game"] == "hollow_knight"
    assert (rec["tokens_in"], rec["tokens_out"], rec["tool_calls"]) == (6401, 845, 3)
    assert rec["stream"] is False


def test_ask_空问题返回400且不占额度(monkeypatch, tmp_path):
    app, records, quota = _fresh(monkeypatch, tmp_path)
    status, _ = _post(app, "/api/ask", {"question": "   "})

    assert status == 400
    assert records == []
    assert quota.status()["used"] == 0


def test_ask_超时返回504并记录错误(monkeypatch, tmp_path):
    def slow_ask(question, history=None, verbose=False, meta=None):
        time.sleep(0.05)
        return "迟到答案"

    app, records, _ = _fresh(monkeypatch, tmp_path, ask=slow_ask)
    monkeypatch.setitem(api_server.config, "request_timeout", 0.001)

    status, body = _post(app, "/api/ask", {"question": "慢问题"})

    assert status == 504
    assert "超时" in json.loads(body)["error"]
    assert records[0]["route"] == "error"
    assert "超时" in records[0]["error"]


# ── 流式 /api/ask/stream ─────────────────────────────────

def test_ask_stream_SSE_事件与观测记录(monkeypatch, tmp_path):
    async def fake_stream(question, history=None, verbose=False, meta=None):
        yield "meta", {"game": "terraria", "game_name": "泰拉瑞亚", "model": "fake"}
        yield "token", "克苏鲁"
        yield "token", "之眼"
        meta.update({"route": "agent", "game": "terraria",
                     "tokens_in": 120, "tokens_out": 30, "tool_calls": 1})

    app, records, _ = _fresh(monkeypatch, tmp_path, ask_stream=fake_stream)
    status, body = _post(app, "/api/ask/stream", {"question": "克苏鲁之眼"})

    assert status == 200
    events = [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["meta", "token", "token", "done"]
    assert "".join(e.get("content", "") for e in events if e["type"] == "token") == "克苏鲁之眼"
    assert events[-1]["quota"]["remaining"] == 99

    rec = records[0]
    assert rec["stream"] is True
    assert rec["stream_chunks"] == 2
    assert rec["tokens_in"] == 120


def test_ask_stream_报错事件仍记录(monkeypatch, tmp_path):
    async def fake_stream(question, history=None, verbose=False, meta=None):
        meta.update({"route": "error", "error": "上游超时"})
        yield "error", "上游超时"

    app, records, _ = _fresh(monkeypatch, tmp_path, ask_stream=fake_stream)
    status, body = _post(app, "/api/ask/stream", {"question": "q"})

    assert status == 200                      # SSE 已开流，错误在流内表达
    assert '"type": "error"' in body
    assert records[0]["route"] == "error"
    assert records[0]["error"] == "上游超时"
