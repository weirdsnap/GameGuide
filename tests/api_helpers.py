"""处理器层测试的公共设施。

放独立模块而不是 conftest：这里要返回「构造好的 app 工厂 + 收集器」，
fixture 的注入形态表达不了，显式 import 更直白。

被 `test_api_handlers.py` 与 `test_session_api.py` 共用，避免两边各自维护
一套「替换 log_turn / 限速 / 额度」的样板（也避免固定时钟被改成两个值）。
"""

import asyncio
import json
import time

import api_server

FIXED_TS = 1_700_000_000
# 按本机时区算，避免用例里写死日期后换时区就红
FIXED_DAY = time.strftime("%Y-%m-%d", time.localtime(FIXED_TS))


def isolate_app_state(monkeypatch, tmp_path, *, limit: int = 100):
    """替换 log_turn / rate_limiter / quota，返回 (turn_records, quota)。

    落盘目录由 conftest 的 autouse fixture 指向 tmp；这里只管内存态，
    保证用例之间不串额度、不真写 jsonl。
    """
    records = []
    monkeypatch.setattr(api_server, "log_turn", records.append)
    monkeypatch.setattr(api_server, "rate_limiter", api_server.RateLimiter())
    quota = api_server.DailyQuota(tmp_path / "quota.json", limit=limit,
                                  clock=lambda: FIXED_TS)
    monkeypatch.setattr(api_server, "quota", quota)
    return records, quota


async def _request(factory, method, path, payload):
    from aiohttp.test_utils import TestClient, TestServer

    # 每个测试都新建 app：aiohttp 的 Application 绑定事件循环，
    # 一个测试里发多个请求时必须每次新建（asyncio.run 每次都是新循环）
    async with TestClient(TestServer(factory())) as client:
        if method == "GET":
            resp = await client.get(path)
        else:
            resp = await client.request(method, path, json=payload)
        return resp.status, await resp.text()


def call_raw(factory, method, path, payload=None):
    """发一个请求，返回 (status, text)。SSE / 非 JSON 响应用这个。"""
    return asyncio.run(_request(factory, method, path, payload))


def call_json(factory, method, path, payload=None):
    """发一个请求，返回 (status, body)。body 能解析成 JSON 就是 dict，否则是 str。"""
    status, text = asyncio.run(_request(factory, method, path, payload))
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, text


def sse_events(text: str):
    """把 SSE 文本拆成事件列表。"""
    return [json.loads(line[5:]) for line in text.splitlines()
            if line.startswith("data: ")]
