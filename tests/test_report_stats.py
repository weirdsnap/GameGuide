#!/usr/bin/env python3
"""统计纯函数层（scripts/analysis/report.py）的契约测试。

规格：docs/DASHBOARD_PLAN.md §4、§10。
全部离线：造 dict 直接算；I/O 用例用 tmp_path 造 JSONL。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.analysis import report  # noqa: E402


def _turn(ts, *, game="hollow_knight", route="agent", elapsed_ms=1000,
          tokens_in=1000, history_msgs=4, turn_id=None, **kw):
    rec = {"ts": ts, "turn_id": turn_id or f"t{ts}", "ip": "1.2.3.4",
           "question": "问题", "game": game, "route": route,
           "elapsed_ms": elapsed_ms, "tokens_in": tokens_in,
           "tokens_out": 100, "tool_calls": 1, "history_msgs": history_msgs}
    rec.update(kw)
    return rec


# ====== 分位数 ======

def test_percentiles_empty_is_all_zero():
    assert report.percentiles([]) == {"avg": 0.0, "p50": 0.0, "p90": 0.0,
                                      "p99": 0.0, "max": 0.0}


def test_percentiles_single_value():
    p = report.percentiles([7])
    assert p["p50"] == p["p90"] == p["max"] == 7.0


def test_percentiles_two_values():
    p = report.percentiles([10, 20])
    assert p["p50"] == 10.0 and p["p90"] == 20.0


def test_percentiles_hundred_values():
    p = report.percentiles(range(1, 101))       # 1..100
    assert p["p50"] == 51.0 and p["p90"] == 90.0 and p["p99"] == 99.0
    assert p["max"] == 100.0


# ====== 时区与日期分组 ======

def test_day_of_uses_fixed_shanghai_offset():
    # UTC 2026-11-22 16:30 == 北京时间 11-23 00:30
    ts = 1795365000
    utc_day = report.day_of(ts, tz_offset=0)
    assert utc_day == "2026-11-22"
    assert report.day_of(ts) == "2026-11-23"


def test_day_boundary_2359_and_0001_stay_in_different_buckets():
    base = 1795365000 - 60 * 30                     # 回到 11-23 00:00 附近
    late = base - 60                                # 11-22 23:59
    early = base + 120                              # 11-23 00:02
    assert report.day_of(late) != report.day_of(early)


# ====== 聚合 ======

def test_summarize_empty():
    s = report.summarize([])
    assert s["turns"] == 0 and s["fail_rate"] == 0.0
    assert s["by_day"] == {} and s["games"] == {}
    assert s["elapsed_s"]["p90"] == 0.0


def test_summarize_counts_and_rates():
    records = [
        _turn(1795365000, game="hollow_knight", elapsed_ms=1000, tokens_in=100),
        _turn(1795365001, game="baldurs_gate3", elapsed_ms=3000, tokens_in=300),
        _turn(1795365002, game="baldurs_gate3", route="error", elapsed_ms=500,
              error="超时"),
        _turn(1795365003, game="baldurs_gate3",
              spoiler_hits=[{"term": "Nightsong", "act": 2, "level": 3}]),
    ]
    s = report.summarize(records)
    assert s["turns"] == 4
    assert s["failed"] == 1 and s["fail_rate"] == 0.25
    assert s["games"]["baldurs_gate3"] == 3
    assert s["routes"]["error"] == 1
    assert s["spoiler_hit_turns"] == 1 and s["spoiler_l3_turns"] == 1
    assert s["tokens_in"] == 100 + 300 + 1000 + 1000


def test_summarize_tolerates_old_records_without_new_fields():
    """2026-11 之前的记录没有 tokens_in/history_msgs，不能 KeyError 也不能算出负数。"""
    records = [{"ts": 1795365000, "ip": "1.2.3.4", "question": "老记录",
                "route": "agent", "game": "hollow_knight"}]
    s = report.summarize(records)
    assert s["turns"] == 1 and s["tokens_in"] == 0
    assert s["tokens_out_stats"]["max"] == 0.0


def test_daily_series_sorted_and_grouped():
    records = [_turn(1795365000), _turn(1795365000 + 86400), _turn(1795365000 + 60)]
    series = report.daily_series(records)
    assert [d["date"] for d in series] == sorted(d["date"] for d in series)
    assert sum(d["turns"] for d in series) == 3


def test_daily_series_merges_feedback_counts():
    ts = 1795365000
    records = [_turn(ts)]
    feedback = [{"ts": ts, "turn_id": "t1", "rating": -1},
                {"ts": ts, "turn_id": "t2", "rating": 1}]
    series = report.daily_series(records, feedback)
    assert series[0]["feedback_bad"] == 1 and series[0]["feedback_good"] == 1


def test_daily_series_pads_empty_days_when_days_given():
    """看板日期轴必须连续：某天没人问也要占一个格，否则图上少一根柱、日期不对齐。"""
    now = 1795365000
    records = [_turn(now), _turn(now - 2 * 86400), _turn(now - 2 * 86400 + 60)]
    series = report.daily_series(records, days=4, now=now)
    assert [d["date"] for d in series] == [
        report.day_of(now - 3 * 86400),
        report.day_of(now - 2 * 86400),
        report.day_of(now - 86400),
        report.day_of(now),
    ]
    assert [d["turns"] for d in series] == [0, 2, 0, 1]
    assert series[0]["tokens_in_p90"] == 0 and series[0]["elapsed_p90"] == 0.0
    # 不传 days 时保持旧语义（只列有数据的天），CLI 与其它调方不受影响
    assert len(report.daily_series(records)) == 2


# ====== 反馈 ======

def test_feedback_counts_dedupes_by_turn_id():
    feedback = [
        {"ts": 100, "turn_id": "a", "rating": -1},
        {"ts": 200, "turn_id": "a", "rating": 1},     # 改主意，只算最后一次
        {"ts": 300, "turn_id": "b", "rating": -1},
    ]
    counts = report.feedback_counts(feedback)
    assert counts == {"total": 2, "good": 1, "bad": 1, "bad_rate": 0.5}


def test_link_feedback_joins_turn_details():
    ts = 1795365000
    turns = [_turn(ts, turn_id="abc", elapsed_ms=5000, tokens_in=42000)]
    feedback = [{"ts": ts + 10, "turn_id": "abc", "rating": -1}]
    items = report.link_feedback(turns, feedback)
    assert len(items) == 1
    item = items[0]
    assert item["matched"] is True
    assert item["elapsed_ms"] == 5000 and item["tokens_in"] == 42000
    assert item["question"] == "问题"


def test_link_feedback_without_matching_turn_still_returns_row():
    """轮次日志被清理后，反馈本身还是要能看见（标 matched=False）。"""
    items = report.link_feedback([], [{"ts": 100, "turn_id": "gone",
                                       "rating": -1, "question": "旧问题"}])
    assert items[0]["matched"] is False and items[0]["question"] == "旧问题"


# ====== 预算 ======

def test_budget_status_ok_and_over():
    ok = report.budget_status([_turn(1, tokens_in=1000)], limit=40000)
    assert ok["ok"] is True and ok["over_by"] == 0
    over = report.budget_status([_turn(1, tokens_in=150000)], limit=40000)
    assert over["ok"] is False and over["over_by"] == 110000


# ====== 轮次列表 ======

def test_turn_row_redacts_ip_and_flattens_spoilers():
    row = report.turn_row(_turn(1795365000, ip="163.125.180.93",
                                spoiler_hits=[{"term": "下城区", "act": 3, "level": 1}]))
    assert row["ip"] == "163.125.x.x"
    assert row["spoiler_level"] == 1 and row["spoiler_terms"] == ["下城区"]


def test_filter_turns_filters():
    ts = 1795365000
    records = [
        _turn(ts, turn_id="a", game="hollow_knight"),
        _turn(ts + 10, turn_id="b", game="baldurs_gate3", elapsed_ms=45000),
        _turn(ts + 20, turn_id="c", game="baldurs_gate3",
              spoiler_hits=[{"term": "Bhaal", "act": 3, "level": 3}]),
    ]
    assert report.filter_turns(records, game="baldurs_gate3")["matched"] == 2
    assert report.filter_turns(records, only_slow=True)["turns"][0]["turn_id"] == "b"
    assert report.filter_turns(records, only_l3=True)["turns"][0]["turn_id"] == "c"
    assert report.filter_turns(records, only_bad=True,
                               bad_turn_ids={"a"})["turns"][0]["turn_id"] == "a"


def test_filter_turns_cursor_has_no_duplicates_or_gaps():
    ts = 1795365000
    records = [_turn(ts + i, turn_id=f"t{i}") for i in range(5)]

    page1 = report.filter_turns(records, limit=2)
    page2 = report.filter_turns(records, limit=2, before=page1["next_before"],
                                before_id=page1["next_before_id"])
    page3 = report.filter_turns(records, limit=2, before=page2["next_before"],
                                before_id=page2["next_before_id"])

    seen = [r["turn_id"] for p in (page1, page2, page3) for r in p["turns"]]
    assert len(seen) == len(set(seen)) == 5
    assert page3["next_before"] is None        # 最后一页不再给游标


def test_filter_turns_same_second_does_not_drop_rows():
    """同一秒多条（评测批量跑）时，只用 ts 做游标会丢数据；复合游标必须全取到。"""
    ts = 1795365000
    records = [_turn(ts, turn_id=f"same{i}") for i in range(4)]

    collected, cursor = [], (None, None)
    for _ in range(4):
        page = report.filter_turns(records, limit=1, before=cursor[0],
                                   before_id=cursor[1])
        collected.extend(r["turn_id"] for r in page["turns"])
        if page["next_before"] is None:
            break
        cursor = (page["next_before"], page["next_before_id"])

    assert sorted(collected) == [f"same{i}" for i in range(4)]


# ====== I/O ======

def test_load_turns_filters_by_day_and_skips_bad_lines(tmp_path):
    day = report.day_of(1795365000)
    path = tmp_path / f"{day}.jsonl"
    path.write_text(
        '{"ts": 1795365000, "ip": "1.2.3.4", "game": "hollow_knight"}\n'
        'not-json-at-all\n'
        "\n"
        '{"ts": 1795365001, "ip": "127.0.0.1", "game": "hollow_knight"}\n',
        encoding="utf-8",
    )
    records = report.load_turns(7, directory=tmp_path)
    assert len(records) == 1                      # 坏行跳过、本机请求剔除
    records_all = report.load_turns(7, include_local=True, directory=tmp_path)
    assert len(records_all) == 2


def test_load_turns_ignores_old_files_outside_window(tmp_path):
    old_day = "2020-01-01"
    (tmp_path / f"{old_day}.jsonl").write_text('{"ts": 1, "ip": "1.2.3.4"}\n',
                                               encoding="utf-8")
    assert report.load_turns(3, directory=tmp_path) == []
    assert len(report.load_turns(None, directory=tmp_path)) == 1


def test_load_feedback_reads_files(tmp_path):
    day = report.day_of(1795365000)
    (tmp_path / f"{day}.jsonl").write_text(
        '{"ts": 1795365000, "turn_id": "a", "rating": -1}\n', encoding="utf-8")
    assert len(report.load_feedback(7, directory=tmp_path)) == 1


def test_redact_ip_handles_missing_and_ipv6():
    assert report.redact_ip("") == ""
    assert report.redact_ip("163.125.180.93") == "163.125.x.x"
    assert report.redact_ip("2001:db8::1") == "ipv6"
