#!/usr/bin/env python3
"""数据看板只读端点的测试（docs/DASHBOARD_PLAN.md §6、§10）。

数据来源是 tmp 里的 JSONL（conftest 的 autouse fixture 已把 TURNS_DIR/FEEDBACK_DIR
指到 tmp_path），不读生产日志。
"""

import json
import sys
import time
from pathlib import Path

import api_server
from scripts.analysis import report

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_helpers  # noqa: E402

NOW = int(time.time())


def _turn(offset, *, turn_id=None, game="baldurs_gate3", route="agent",
          elapsed_ms=1000, tokens_in=1000, **kw):
    rec = {"ts": NOW - offset, "turn_id": turn_id or f"t{offset}", "ip": "1.2.3.4",
           "question": "问题", "game": game, "route": route,
           "elapsed_ms": elapsed_ms, "tokens_in": tokens_in,
           "tokens_out": 100, "tool_calls": 1, "history_msgs": 4}
    rec.update(kw)
    return rec


def _append(directory, records):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    by_day = {}
    for r in records:
        by_day.setdefault(report.day_of(r["ts"]), []).append(r)
    for day, rows in by_day.items():
        with open(directory / f"{day}.jsonl", "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _setup(monkeypatch, tmp_path):
    factory = api_server.create_app
    api_helpers.isolate_app_state(monkeypatch, tmp_path)
    return factory


def _get(factory, path):
    return api_helpers.call_json(factory, "GET", path)


# ====== summary ======

def test_summary_with_no_data(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    status, body = _get(factory, "/api/dashboard/summary?days=7")
    assert status == 200
    assert body["summary"]["turns"] == 0
    assert body["daily"] == [] and body["today"] is None
    assert body["budget"]["ok"] is True
    assert body["quota"]["limit"] > 0


def test_summary_aggregates_turns_and_feedback(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    _append(api_server.TURNS_DIR, [
        _turn(10, turn_id="a1", tokens_in=1000),
        _turn(20, turn_id="a2", tokens_in=50000),          # 超预算
        _turn(30, turn_id="a3", route="error", error="超时"),
    ])
    _append(api_server.FEEDBACK_DIR, [
        {"ts": NOW - 5, "turn_id": "a2", "rating": -1},
        {"ts": NOW - 6, "turn_id": "a1", "rating": 1},
    ])

    status, body = _get(factory, "/api/dashboard/summary?days=7")
    assert status == 200
    assert body["summary"]["turns"] == 3
    assert body["summary"]["failed"] == 1
    assert body["budget"]["ok"] is False           # p90 被 50000 拉过 40000
    assert body["feedback"] == {"total": 2, "good": 1, "bad": 1, "bad_rate": 0.5}
    assert body["today"]["turns"] == 3
    assert body["daily"][0]["feedback_bad"] == 1


def test_summary_excludes_local_test_traffic(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    _append(api_server.TURNS_DIR, [
        _turn(10, turn_id="real"),
        dict(_turn(20, turn_id="local"), ip="127.0.0.1"),
    ])
    _, body = _get(factory, "/api/dashboard/summary?days=7")
    assert body["summary"]["turns"] == 1


# ====== turns ======

def test_turns_pagination_walks_all_rows(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    _append(api_server.TURNS_DIR, [_turn(10 * i, turn_id=f"t{i}") for i in range(1, 6)])

    seen, path = [], "/api/dashboard/turns?days=7&limit=2"
    for _ in range(5):
        status, body = _get(factory, path)
        assert status == 200
        seen.extend(t["turn_id"] for t in body["turns"])
        if body["next_before"] is None:
            break
        path = (f"/api/dashboard/turns?days=7&limit=2&before={body['next_before']}"
                f"&before_id={body['next_before_id']}")

    assert len(seen) == len(set(seen)) == 5


def test_turns_filters(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    _append(api_server.TURNS_DIR, [
        _turn(10, turn_id="slow", elapsed_ms=60000),
        _turn(20, turn_id="l3", spoiler_hits=[{"term": "Bhaal", "level": 3}]),
        _turn(30, turn_id="hk", game="hollow_knight"),
    ])
    _append(api_server.FEEDBACK_DIR, [{"ts": NOW - 5, "turn_id": "hk", "rating": -1}])

    _, slow = _get(factory, "/api/dashboard/turns?slow=1")
    assert [t["turn_id"] for t in slow["turns"]] == ["slow"]

    _, l3 = _get(factory, "/api/dashboard/turns?l3=1")
    assert [t["turn_id"] for t in l3["turns"]] == ["l3"]

    _, bad = _get(factory, "/api/dashboard/turns?bad=1")
    assert [t["turn_id"] for t in bad["turns"]] == ["hk"]

    _, by_game = _get(factory, "/api/dashboard/turns?game=hollow_knight")
    assert [t["turn_id"] for t in by_game["turns"]] == ["hk"]


def test_turns_row_hides_full_ip(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    _append(api_server.TURNS_DIR, [_turn(10, turn_id="x", ip="163.125.180.93")])
    _, body = _get(factory, "/api/dashboard/turns?days=7")
    assert body["turns"][0]["ip"] == "163.125.x.x"


def test_bad_query_params_do_not_500(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    for path in ("/api/dashboard/summary?days=abc",
                 "/api/dashboard/turns?limit=-5&days=999&before=xx",
                 "/api/dashboard/feedback?days=0"):
        status, _ = _get(factory, path)
        assert status == 200, path


# ====== feedback ======

def test_feedback_endpoint_links_turns(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    _append(api_server.TURNS_DIR, [_turn(10, turn_id="abc", elapsed_ms=4000)])
    _append(api_server.FEEDBACK_DIR, [
        {"ts": NOW - 5, "turn_id": "abc", "rating": -1},
        {"ts": NOW - 6, "turn_id": "gone", "rating": -1, "question": "找不到的老轮"},
    ])
    status, body = _get(factory, "/api/dashboard/feedback?days=7")
    assert status == 200
    assert body["counts"]["bad"] == 2
    by_id = {i["turn_id"]: i for i in body["items"]}
    assert by_id["abc"]["matched"] is True and by_id["abc"]["elapsed_ms"] == 4000
    assert by_id["gone"]["matched"] is False


# ====== 访问控制（SSH 隧道方案，docs/DASHBOARD_PLAN.md §9）======
# 看板返回真实提问原话，只准许「不经反向代理的直连」。
# 判定依据：经 Caddy 进来的请求必带 X-Forwarded-For；隧道/本机 curl 不带。

PROXIED = {"X-Forwarded-For": "203.0.113.9"}


def test_dashboard_page_served_to_direct_client(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    status, text = api_helpers.call_raw(factory, "GET", "/dashboard")
    assert status == 200
    assert "<html" in text.lower()
    # 页面里必须带 noindex，避免哪天配置漂了被搜索引擎收录
    assert "noindex" in text


def test_dashboard_page_404_through_proxy(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    status, _ = api_helpers.call_raw(factory, "GET", "/dashboard", headers=PROXIED)
    assert status == 404


def test_dashboard_apis_404_through_proxy(monkeypatch, tmp_path):
    """三个数据端点都要罩住——只保护 HTML 等于没保护。"""
    factory = _setup(monkeypatch, tmp_path)
    for path in ("/api/dashboard/summary", "/api/dashboard/turns",
                 "/api/dashboard/feedback"):
        status, _ = api_helpers.call_json(factory, "GET", path, headers=PROXIED)
        assert status == 404, path


def test_dashboard_disabled_switch(monkeypatch, tmp_path):
    """GAMEGUIDE_DASHBOARD=0 时连直连也 404（兜底开关）。"""
    factory = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(api_server, "DASHBOARD_ENABLED", False)
    assert api_helpers.call_raw(factory, "GET", "/dashboard")[0] == 404
    assert api_helpers.call_json(factory, "GET", "/api/dashboard/summary")[0] == 404


def test_dashboard_allow_proxied_switch(monkeypatch, tmp_path):
    """将来改走 Caddy basic_auth 时，把 ALLOW_PROXIED 打开即可放行。"""
    factory = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(api_server, "DASHBOARD_ALLOW_PROXIED", True)
    status, _ = api_helpers.call_json(factory, "GET", "/api/dashboard/summary",
                                      headers=PROXIED)
    assert status == 200


def test_dashboard_denied_from_non_loopback(monkeypatch, tmp_path):
    """非回环来源一律拒（防安全组把 8765 开到公网后直连 IP 绕过）。"""
    from types import SimpleNamespace

    monkeypatch.setattr(api_server, "DASHBOARD_ALLOW_PROXIED", False)
    assert api_server.dashboard_denied(SimpleNamespace(remote="203.0.113.9", headers={}))
    assert api_server.dashboard_denied(SimpleNamespace(remote=None, headers={}))
    assert not api_server.dashboard_denied(SimpleNamespace(remote="127.0.0.1", headers={}))
    assert not api_server.dashboard_denied(SimpleNamespace(remote="::1", headers={}))
    # 回环但带代理头 = 经 Caddy 从公网进来，仍然拒
    assert api_server.dashboard_denied(
        SimpleNamespace(remote="127.0.0.1", headers=PROXIED))


# ====== 口径一致性 ======

def test_stats_matches_summarize_parity(monkeypatch, tmp_path):
    """旧 /api/stats 与看板 summary 必须同源，否则两边数字会漂。"""
    factory = _setup(monkeypatch, tmp_path)
    _append(api_server.TURNS_DIR, [_turn(10 * i, turn_id=f"p{i}") for i in range(1, 6)])

    _, stats = _get(factory, "/api/stats?days=7")
    _, summary = _get(factory, "/api/dashboard/summary?days=7")
    s = summary["summary"]

    assert stats["turns"] == s["turns"]
    assert stats["by_day"] == s["by_day"]
    assert stats["games"] == s["games"]
    assert stats["routes"] == s["routes"]
    assert stats["elapsed_s"]["p90"] == s["elapsed_s"]["p90"]
    assert stats["tokens_in"] == s["tokens_in"]
    assert stats["spoiler_hit_turns"] == s["spoiler_hit_turns"]
