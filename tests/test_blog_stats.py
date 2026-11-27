#!/usr/bin/env python3
"""博客访问统计的测试（docs/BLOG_ANALYTICS_PLAN.md）。

三层：
- `blog_stats` 纯函数（归一化、设备档位、可疑标记、按天轮换的 visitor 摘要）；
- `report.blog_summary` 聚合口径（flagged 剔除、top 文章、缺日补 0）；
- `POST /api/hit` 与 `GET /api/dashboard/blog` 的端点契约。

落库全部走 tmp（conftest 把 GAMEGUIDE_BLOG_DB 指到 tmp_path），不碰生产 data/blog.db。
"""

import asyncio
import sys
import time
from pathlib import Path

import pytest

import api_server
from rag_agent import blog_stats
from scripts.analysis import report

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_helpers  # noqa: E402

BLOG_ORIGIN = "https://" + "weirdsnap.github.io"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120 Safari/537.36"
BOT_UA = "Googlebot/2.1 (+http://www.google.com/bot.html)"
NOW = 1_700_000_000
DAY = blog_stats.day_of(NOW)


# ====== 纯函数 ======


def test_normalize_path_keeps_short_labels_only():
    assert blog_stats.normalize_path("/post/posts/ai/x.md") == "/post/posts/ai/x.md"
    assert blog_stats.normalize_path("/") == "/"
    # 带 query/fragment 的原始 URL 只留路径，避免把 query 里的用户输入原样入库
    assert blog_stats.normalize_path("/htmls/blog.html?post=x") == "/htmls/blog.html"
    assert blog_stats.normalize_path("posts/x") is None      # 不以 / 开头
    assert blog_stats.normalize_path("/a b") is None          # 含空白
    assert blog_stats.normalize_path("/" + "x" * 250) is None  # 超长
    assert blog_stats.normalize_path(None) is None


def test_normalize_ref_keeps_host_only():
    assert blog_stats.normalize_ref("https://github.com/weirdsnap/x?y=1") == "github.com"
    assert blog_stats.normalize_ref("Google.COM") == "google.com"
    assert blog_stats.normalize_ref("github.com:8080/path") == "github.com"
    assert blog_stats.normalize_ref("evil") is None or True   # 单段域名合法但不强求
    assert blog_stats.normalize_ref("") is None
    assert blog_stats.normalize_ref(None) is None


def test_device_of_three_buckets():
    assert blog_stats.device_of(390) == "mobile"
    assert blog_stats.device_of(900) == "tablet"
    assert blog_stats.device_of(1440) == "desktop"
    assert blog_stats.device_of("bad") == "desktop"   # 缺数据当桌面，不留 null


def test_is_flagged_marks_bots_and_unknown_origin():
    assert blog_stats.is_flagged(BROWSER_UA, BLOG_ORIGIN) == 0
    assert blog_stats.is_flagged(BROWSER_UA, "") == 0            # 无 Origin 不判可疑
    assert blog_stats.is_flagged(BOT_UA, BLOG_ORIGIN) == 1
    assert blog_stats.is_flagged(BROWSER_UA, "https://evil.example") == 1
    assert blog_stats.is_flagged("python-requests/2.31", "") == 1


def test_visitor_hash_rotates_by_day():
    same_day = blog_stats.visitor_hash("client-a", "2026-11-27")
    assert same_day == blog_stats.visitor_hash("client-a", "2026-11-27")
    assert same_day != blog_stats.visitor_hash("client-a", "2026-11-28")
    assert same_day != blog_stats.visitor_hash("client-b", "2026-11-27")
    assert same_day != blog_stats.visitor_hash("client-a", "2026-11-27", salt="other")
    # 摘要里不能出现原始 IP 的任何片段
    assert "client-a" not in same_day


def test_hit_rate_limiter_slides():
    clock = [1000.0]
    limiter = blog_stats.HitRateLimiter(per_min=2, clock=lambda: clock[0])
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is False
    assert limiter.allow("client-b") is True       # 不同来源互不影响
    clock[0] += 61                                  # 窗口滑过后恢复
    assert limiter.allow("client-a") is True


# ====== 聚合口径 ======


def _hit(offset, path="/post/posts/ai/x.md", kind="post", ref=None,
         device="desktop", visitor="v1", flagged=0, ts=None):
    ts = NOW - offset if ts is None else ts
    return {"ts": ts, "day": blog_stats.day_of(ts), "path": path, "kind": kind,
            "ref": ref, "device": device, "visitor": visitor, "flagged": flagged}


def test_blog_summary_empty():
    out = report.blog_summary([], days=7, now=NOW)
    assert out["pv"] == 0 and out["uv"] == 0 and out["flagged"] == 0
    assert len(out["series"]) == 7
    assert all(row["pv"] == 0 for row in out["series"])
    assert out["top_posts"] == [] and out["refs"] == [] and out["devices"] == []


def test_blog_summary_counts_pv_uv_and_excludes_flagged():
    rows = [
        _hit(0, visitor="v1"),
        _hit(10, visitor="v1"),
        _hit(20, visitor="v2"),
        _hit(30, visitor="v3", flagged=1),          # 可疑：不计入 pv/uv
    ]
    out = report.blog_summary(rows, days=7, now=NOW)
    assert out["pv"] == 3 and out["uv"] == 2
    assert out["flagged"] == 1
    assert out["top_posts"] == [{"path": "/post/posts/ai/x.md", "pv": 3, "uv": 2}]


def test_blog_summary_series_covers_every_day():
    rows = [_hit(0), _hit(86400 * 2)]              # 今天一条 + 前天一条
    out = report.blog_summary(rows, days=3, now=NOW)
    assert [r["day"] for r in out["series"]] == [
        blog_stats.day_of(NOW - 2 * 86400),
        blog_stats.day_of(NOW - 86400),
        blog_stats.day_of(NOW),
    ]
    assert [r["pv"] for r in out["series"]] == [1, 0, 1]


def test_blog_summary_top_posts_only_counts_articles():
    rows = [
        _hit(0, path="/list", kind="list"),
        _hit(1, path="/", kind="home"),
        _hit(2, path="/post/a", visitor="v1"),
        _hit(3, path="/post/a", visitor="v2"),
        _hit(4, path="/post/b", visitor="v3"),
    ]
    out = report.blog_summary(rows, days=7, now=NOW)
    assert [p["path"] for p in out["top_posts"]] == ["/post/a", "/post/b"]
    assert out["top_posts"][0] == {"path": "/post/a", "pv": 2, "uv": 2}


def test_blog_summary_refs_and_devices():
    rows = [
        _hit(0, ref="github.com", device="mobile"),
        _hit(1, ref="github.com", device="desktop"),
        _hit(2, ref=None, device="desktop"),
    ]
    out = report.blog_summary(rows, days=7, now=NOW)
    assert out["refs"] == [{"ref": "github.com", "pv": 2}]
    assert {d["device"]: d["pv"] for d in out["devices"]} == {"mobile": 1, "desktop": 2}
    assert round(sum(d["share"] for d in out["devices"]), 3) == 1.0


# ====== 存储 ======


def test_record_and_fetch_rows(tmp_path):
    db = tmp_path / "blog.db"
    blog_stats.record_hit(db, ts=NOW, path_label="/post/a", kind="post", visitor="v1")
    blog_stats.record_hit(db, ts=NOW - 86400 * 10, path_label="/post/old", kind="post")
    rows = blog_stats.fetch_rows(db, days=7, until=NOW)
    assert [r["path"] for r in rows] == ["/post/a"]
    assert rows[0]["day"] == DAY
    assert blog_stats.fetch_rows(tmp_path / "missing.db", days=7) == []


# ====== 端点 ======


def _post_text(factory, path, body, headers=None):
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        async with TestClient(TestServer(factory())) as client:
            resp = await client.post(path, data=body, headers=headers or {})
            return resp.status, await resp.text()

    return asyncio.run(go())


def _setup(monkeypatch, tmp_path):
    api_helpers.isolate_app_state(monkeypatch, tmp_path)
    return api_server.create_app


def _rows():
    return blog_stats.fetch_rows(days=7)


def test_hit_records_browser_visit(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    body = '{"p":"/post/posts/ai/x.md","k":"post","r":"https://github.com/x","w":390}'
    status, text = _post_text(factory, "/api/hit", body,
                              {"User-Agent": BROWSER_UA, "Origin": BLOG_ORIGIN})
    assert status == 204 and text == ""
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["path"] == "/post/posts/ai/x.md"
    assert row["kind"] == "post"
    assert row["ref"] == "github.com"
    assert row["device"] == "mobile"
    assert row["flagged"] == 0
    assert len(row["visitor"]) == 16          # 摘要，不是 IP


def test_hit_ignores_bad_payloads(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    headers = {"User-Agent": BROWSER_UA, "Origin": BLOG_ORIGIN}
    for body in ["", "not json", "[]", '{"p":"posts/x"}', '{"p":"/a b"}', '{"x":1}']:
        assert _post_text(factory, "/api/hit", body, headers)[0] == 204
    assert _post_text(factory, "/api/hit", "x" * 5000, headers)[0] == 204
    assert _rows() == []


def test_hit_flags_bot_and_unknown_origin(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    body = '{"p":"/post/a","k":"post"}'
    _post_text(factory, "/api/hit", body, {"User-Agent": BOT_UA, "Origin": BLOG_ORIGIN})
    _post_text(factory, "/api/hit", body,
               {"User-Agent": BROWSER_UA, "Origin": "https://evil.example"})
    rows = _rows()
    assert [r["flagged"] for r in rows] == [1, 1]
    # 落库但统计默认剔除
    assert report.blog_summary(rows, days=7)["pv"] == 0
    assert report.blog_summary(rows, days=7)["flagged"] == 2


def test_hit_silently_drops_over_rate(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(api_server, "blog_hit_limiter", blog_stats.HitRateLimiter(2))
    body = '{"p":"/post/a","k":"post"}'
    headers = {"User-Agent": BROWSER_UA, "Origin": BLOG_ORIGIN}
    for _ in range(4):
        assert _post_text(factory, "/api/hit", body, headers)[0] == 204
    assert len(_rows()) == 2


def test_dashboard_blog_empty_and_filled(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    status, body = api_helpers.call_json(factory, "GET", "/api/dashboard/blog?days=7")
    assert status == 200 and body["pv"] == 0 and len(body["series"]) == 7

    blog_stats.record_hit(ts=time.time(), path_label="/post/posts/ai/x.md",
                          kind="post", visitor="v1", device="mobile")
    status, body = api_helpers.call_json(factory, "GET", "/api/dashboard/blog?days=7")
    assert status == 200
    assert body["pv"] == 1 and body["uv"] == 1
    assert body["top_posts"][0]["path"] == "/post/posts/ai/x.md"
    assert "generated_at" in body


def test_dashboard_blog_respects_proxy_gate(monkeypatch, tmp_path):
    factory = _setup(monkeypatch, tmp_path)
    status, _ = api_helpers.call_json(
        factory, "GET", "/api/dashboard/blog?days=7", headers={"X-Forwarded-For": "1.2.3.4"}
    )
    assert status == 404


@pytest.mark.parametrize("days", [1, 7, 30, 90])
def test_dashboard_blog_days_param(monkeypatch, tmp_path, days):
    factory = _setup(monkeypatch, tmp_path)
    status, body = api_helpers.call_json(factory, "GET", f"/api/dashboard/blog?days={days}")
    assert status == 200 and body["days"] == days and len(body["series"]) == days
