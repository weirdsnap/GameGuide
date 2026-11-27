#!/usr/bin/env python3
"""系统资源采样（src/rag_agent/sysstat.py）与 /api/dashboard/system 的测试。

解析层是纯函数，用假文本测；采样层用 tmp 里造的假 /proc 目录测，
不读真机器状态（否则负载一变用例就飘）。
"""

import json
import sys
import time
from pathlib import Path

import api_server
from rag_agent import sysstat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_helpers  # noqa: E402

NOW = int(time.time())

MEMINFO = """MemTotal:        3812644 kB
MemFree:          100000 kB
MemAvailable:    2736000 kB
Buffers:           10000 kB
SwapTotal:       2034684 kB
SwapFree:         311000 kB
"""

LOADAVG = "0.14 0.26 0.18 5/290 3653688\n"

STAT = """cpu  100 0 100 800 100 0 0 0 0 0
cpu0 50 0 50 400 50 0 0 0 0 0
intr 12345
"""

VMSTAT = """nr_free_pages 12345
pswpin 995346
pswpout 1830508
"""

SELF_STATUS = """Name:\tpython
VmRSS:\t  133120 kB
Threads:\t12
"""


# ====== 解析层 ======

def test_parse_meminfo():
    out = sysstat.parse_meminfo(MEMINFO)
    assert out["mem_total_mb"] == 3723
    assert out["mem_avail_mb"] == 2672
    assert out["swap_total_mb"] == 1987
    assert out["swap_used_mb"] == 1683


def test_parse_meminfo_missing_fields():
    out = sysstat.parse_meminfo("MemTotal: 1000 kB\n")
    assert out["mem_total_mb"] == 1
    assert out["mem_avail_mb"] is None
    assert out["swap_used_mb"] is None  # 缺 SwapFree 时不给假数字


def test_parse_loadavg():
    out = sysstat.parse_loadavg(LOADAVG)
    assert out == {"load1": 0.14, "load5": 0.26, "load15": 0.18}
    assert sysstat.parse_loadavg("")["load1"] is None


def test_parse_stat():
    idle, total = sysstat.parse_stat(STAT)
    assert idle == 900      # idle 800 + iowait 100
    assert total == 1100    # 只取 cpu 汇总行，不含 cpu0


def test_parse_vmstat():
    out = sysstat.parse_vmstat(VMSTAT)
    assert out == {"swap_in_pages": 995346, "swap_out_pages": 1830508}


def test_parse_self_status():
    out = sysstat.parse_self_status(SELF_STATUS)
    assert out["rss_mb"] == 130
    assert out["threads"] == 12


def test_cpu_percent_needs_two_samples(monkeypatch):
    monkeypatch.setattr(sysstat, "_last_stat", None)
    assert sysstat.cpu_percent((900, 1100)) is None   # 第一次没基准
    # 第二次：idle 增 50、总增 100 → 一半时间在忙
    assert sysstat.cpu_percent((950, 1200)) == 50.0
    # 快照不动（同一秒采两次）不能算出负数或除零
    assert sysstat.cpu_percent((950, 1200)) is None
    monkeypatch.setattr(sysstat, "_last_stat", None)


# ====== 采样层（假 /proc）======

def _fake_proc(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "meminfo").write_text(MEMINFO, encoding="utf-8")
    (root / "loadavg").write_text(LOADAVG, encoding="utf-8")
    (root / "stat").write_text(STAT, encoding="utf-8")
    (root / "vmstat").write_text(VMSTAT, encoding="utf-8")
    (root / "self").mkdir(exist_ok=True)
    (root / "self" / "status").write_text(SELF_STATUS, encoding="utf-8")
    (root / "self" / "fd").mkdir(exist_ok=True)
    for i in range(7):
        (root / "self" / "fd" / str(i)).write_text("", encoding="utf-8")
    return root


def test_sample_with_fake_proc(monkeypatch, tmp_path):
    monkeypatch.setattr(sysstat, "_last_stat", None)
    proc = _fake_proc(tmp_path / "proc")
    rec = sysstat.sample(proc_root=proc, disk_path=tmp_path, now=NOW)
    assert rec["ts"] == NOW
    assert rec["mem_avail_mb"] == 2672
    assert rec["swap_used_mb"] == 1683
    assert rec["load1"] == 0.14
    assert rec["swap_out_pages"] == 1830508
    assert rec["rss_mb"] == 130
    assert rec["threads"] == 12
    assert rec["fds"] == 7
    assert rec["cpu_pct"] is None          # 首次采样没基准
    assert rec["disk_total_mb"] > 0
    monkeypatch.setattr(sysstat, "_last_stat", None)


def test_sample_survives_missing_files(monkeypatch, tmp_path):
    monkeypatch.setattr(sysstat, "_last_stat", None)
    rec = sysstat.sample(proc_root=tmp_path / "nope", disk_path=tmp_path, now=NOW)
    assert rec["ts"] == NOW
    assert rec["mem_avail_mb"] is None
    assert rec["cpu_pct"] is None
    assert rec["fds"] is None
    monkeypatch.setattr(sysstat, "_last_stat", None)


# ====== 存储层 ======

def test_append_and_load_series(tmp_path):
    old = {"ts": NOW - 3 * 3600, "mem_avail_mb": 2500}
    a = {"ts": NOW - 1800, "mem_avail_mb": 2000}
    b = {"ts": NOW - 60, "mem_avail_mb": 1500}
    for rec in (old, a, b):
        sysstat.append(tmp_path / "metrics", rec)
    series = sysstat.load_series(tmp_path / "metrics", hours=24, now=NOW)
    assert [r["ts"] for r in series] == [old["ts"], a["ts"], b["ts"]]
    # 窗口外（3 小时前那个点）的不返回
    assert [r["ts"] for r in sysstat.load_series(tmp_path / "metrics", hours=1, now=NOW)] == [
        a["ts"], b["ts"]]


def test_load_series_skips_bad_lines(tmp_path):
    d = tmp_path / "metrics"
    d.mkdir()
    day = time.strftime("%Y-%m-%d", time.localtime(NOW))
    (d / f"{day}.jsonl").write_text(
        "not json\n\n" + json.dumps({"ts": NOW - 10}) + "\n" + json.dumps({"ts": NOW}) + "\n",
        encoding="utf-8")
    series = sysstat.load_series(d, hours=1, now=NOW)
    assert [r["ts"] for r in series] == [NOW - 10, NOW]


def test_downsample_keeps_points_when_small():
    series = [{"ts": i, "mem_avail_mb": i} for i in range(10)]
    assert sysstat.downsample(series, buckets=100) == series


def test_downsample_buckets_average():
    series = [{"ts": i, "mem_avail_mb": 100.0, "load1": float(i)} for i in range(400)]
    out = sysstat.downsample(series, buckets=100)
    assert len(out) == 100
    assert out[0]["ts"] < out[-1]["ts"]
    assert out[0]["mem_avail_mb"] == 100.0
    assert 0 <= out[0]["load1"] < out[-1]["load1"]


def test_retain_removes_old_files_only(tmp_path):
    d = tmp_path / "metrics"
    d.mkdir()
    old = time.strftime("%Y-%m-%d", time.localtime(NOW - 30 * 86400))
    today = time.strftime("%Y-%m-%d", time.localtime(NOW))
    (d / f"{old}.jsonl").write_text("{}\n", encoding="utf-8")
    (d / f"{today}.jsonl").write_text("{}\n", encoding="utf-8")
    assert sysstat.retain(d, days=7, now=NOW) == 1
    assert not (d / f"{old}.jsonl").exists()
    assert (d / f"{today}.jsonl").exists()


def test_health_levels():
    good = sysstat.health({"mem_avail_mb": 2000, "swap_used_mb": 100,
                           "swap_total_mb": 2000, "disk_free_mb": 30000, "load1": 0.2})
    assert set(good.values()) == {"ok"}
    bad = sysstat.health({"mem_avail_mb": 100, "swap_used_mb": 1900,
                          "swap_total_mb": 2000, "disk_free_mb": 1000, "load1": 5})
    assert set(bad.values()) == {"bad"}
    # 当场这台机器的样子：swap 用掉八成以上 → 必须染色，不能一片绿
    warn = sysstat.health({"mem_avail_mb": 2673, "swap_used_mb": 1684,
                           "swap_total_mb": 1987, "disk_free_mb": 31000, "load1": 0.14})
    assert warn["swap"] == "bad"
    assert warn["mem"] == "ok"


# ====== 端点 ======

def test_dashboard_system_endpoint(monkeypatch, tmp_path):
    api_helpers.isolate_app_state(monkeypatch, tmp_path)
    metrics = tmp_path / "metrics"
    sysstat.append(metrics, {"ts": NOW - 120, "mem_avail_mb": 2000, "load1": 0.2})
    sysstat.append(metrics, {"ts": NOW - 60, "mem_avail_mb": 1800, "load1": 0.3})
    monkeypatch.setattr(api_server, "METRICS_DIR", metrics)

    status, body = api_helpers.call_json(api_server.create_app, "GET",
                                         "/api/dashboard/system?hours=24")
    assert status == 200
    assert body["interval_s"] == api_server.METRICS_INTERVAL_S
    assert isinstance(body["series"], list) and len(body["series"]) >= 2
    assert body["now"]["ts"] > 0
    assert set(body["health"]) == {"mem", "swap", "disk", "load"}


def test_dashboard_system_is_gated(monkeypatch, tmp_path):
    api_helpers.isolate_app_state(monkeypatch, tmp_path)
    monkeypatch.setattr(api_server, "METRICS_DIR", tmp_path / "metrics")
    status, _ = api_helpers.call_raw(api_server.create_app, "GET",
                                     "/api/dashboard/system?hours=24",
                                     headers={"X-Forwarded-For": "1.2.3.4"})
    assert status == 404


def test_summary_includes_system_snapshot(monkeypatch, tmp_path):
    api_helpers.isolate_app_state(monkeypatch, tmp_path)
    monkeypatch.setattr(api_server, "METRICS_DIR", tmp_path / "metrics")
    status, body = api_helpers.call_json(api_server.create_app, "GET",
                                        "/api/dashboard/summary?days=7")
    assert status == 200
    assert "system" in body
    assert "health" in body["system"]
