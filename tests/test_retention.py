#!/usr/bin/env python3
"""留存策略契约测试：**只隐藏、不删除**。

2026-11-25 口径变更。以前 `store.purge` 每小时把 3 天前的 answer 置 NULL、把 90 天没活动的
会话整行删掉；现在不做任何物理清理——用户主动删除（sessions.hidden_at）和到期过期都只
影响「用户能不能看到」，原文与行全都留在库里，后台分析照常。

规格：docs/SESSION_STORE_IMPL.md §1（可见窗口）、§3（retention_stats）、§10。
时间全部靠 now= 注入，不 sleep、不依赖系统时钟。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from rag_agent import store  # noqa: E402

DAY = 86400
NOW = 1_800_000_000       # 任意固定时刻


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "sessions.db"
    store.init_db(path)
    return path


def _session_at(db, sid, last_active):
    store.ensure_session(session_id=sid, client_id="c", now=last_active, db_path=db)


def _turn_at(db, sid, seq_ts, answer="答"):
    store.append_turn(session_id=sid, turn_id=f"t{seq_ts}", question="问",
                      answer=answer, now=seq_ts, db_path=db)


def _answers(db, sid, now=NOW):
    return [r["answer"] for r in store.session_rows(sid, limit=100, now=now, db_path=db)]


# ====== 答案窗口：只影响展示 ======

def test_answer_hidden_after_three_days_but_kept(db):
    """3 天前的答案不再返回给用户，但库里的 answer 一个字都没少。"""
    _session_at(db, "s", NOW)
    _turn_at(db, "s", NOW - 3 * DAY + 60)      # 2 天 23 小时前 → 还给用户看
    _session_at(db, "s2", NOW)
    _turn_at(db, "s2", NOW - 3 * DAY - 60)     # 3 天 1 分钟前 → 对他隐藏

    assert _answers(db, "s") == ["答"]
    assert _answers(db, "s2") == [None]

    # 后台视角（放宽窗口）原文还在
    assert _answers(db, "s2", now=NOW - 10 * DAY) == ["答"]


def test_hidden_answer_keeps_question_row_and_receipts(db):
    """答案过期只隐藏 answer，问题、行、receipts 都还在。"""
    _session_at(db, "s", NOW)
    _turn_at(db, "s", NOW - 10 * DAY)
    store.save_receipt(turn_id=f"t{NOW - 10 * DAY}", session_id="s", seq=1,
                       conclusion="结论还在", db_path=db)

    rows = store.session_rows("s", now=NOW, db_path=db)
    assert rows[0]["question"] == "问" and rows[0]["answer_expired"] is True
    assert rows[0]["answer"] is None                    # 对用户不可见
    assert store.session_receipts("s", db_path=db)[0]["conclusion"] == "结论还在"


def test_answer_window_is_configurable(db):
    """窗口走参数（api_server 从 env 读后传进来），改 1 天就该只留 1 天可见。"""
    _session_at(db, "s", NOW)
    _turn_at(db, "s", NOW - 2 * DAY)
    assert _answers(db, "s") == ["答"]                                     # 默认 3 天
    rows = store.session_rows("s", answer_days=1, now=NOW, db_path=db)
    assert rows[0]["answer"] is None                                       # 改 1 天
    assert _answers(db, "s") == ["答"]                                     # 库里没动


# ====== 会话窗口：只影响列表 ======

def test_expired_session_leaves_list_but_stays_in_db(db):
    _session_at(db, "old", NOW - 91 * DAY)
    _turn_at(db, "old", NOW - 91 * DAY)
    _session_at(db, "fresh", NOW - 89 * DAY)

    listed = [r["session_id"] for r in store.list_sessions("c", now=NOW, db_path=db)]
    assert listed == ["fresh"]

    old = store.get_session("old", db_path=db)
    assert old is not None                                     # 行还在
    assert store.session_visible(old, now=NOW) is False
    assert store.session_rows("old", now=NOW, db_path=db)      # turns 还在


def test_visible_window_is_configurable(db):
    _session_at(db, "s", NOW - 10 * DAY)
    assert store.list_sessions("c", now=NOW, db_path=db)                       # 默认 90 天
    assert store.list_sessions("c", visible_days=7, now=NOW, db_path=db) == []  # 改 7 天


# ====== 统计：只读，永远不写 ======

def test_retention_stats_is_readonly_and_idempotent(db):
    _session_at(db, "s", NOW)
    _turn_at(db, "s", NOW - 5 * DAY)
    _session_at(db, "gone", NOW - 120 * DAY)

    first = store.retention_stats(now=NOW, db_path=db)
    second = store.retention_stats(now=NOW, db_path=db)
    assert first == second
    assert first["answers_expired_kept"] == 1
    assert first["sessions_expired_kept"] == 1
    # 什么都没被删
    assert store.get_session("gone", db_path=db) is not None
    assert store.session_rows("s", now=NOW - 10 * DAY, db_path=db)[0]["answer"] == "答"


def test_retention_stats_counts_hidden(db):
    _session_at(db, "a", NOW)
    _session_at(db, "b", NOW)
    store.hide_session("a", db_path=db)
    stats = store.retention_stats(now=NOW, db_path=db)
    assert stats["sessions_hidden_kept"] == 1
    assert store.get_session("a", db_path=db) is not None      # 隐藏也没删
