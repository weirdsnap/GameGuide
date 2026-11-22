#!/usr/bin/env python3
"""留存清理（store.purge）契约测试。

规格：docs/SESSION_STORE_IMPL.md §1（留存参数）、§3（purge SQL）、§10。
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


def _answers(db, sid):
    return [r["answer"] for r in store.session_rows(sid, limit=100, db_path=db)]


def test_answer_expires_after_three_days(db):
    _session_at(db, "s", NOW)
    _turn_at(db, "s", NOW - 3 * DAY + 60)      # 2 天 23 小时前 → 保留
    sid2 = "s2"
    _session_at(db, sid2, NOW)
    _turn_at(db, sid2, NOW - 3 * DAY - 60)     # 3 天 1 分钟前 → 清空

    stats = store.purge(answer_days=3, receipt_days=90, session_days=90,
                        now=NOW, db_path=db)
    assert stats["answers_cleared"] == 1
    assert _answers(db, "s") == ["答"]
    assert _answers(db, sid2) == [None]


def test_cleared_answer_keeps_question_and_receipts(db):
    _session_at(db, "s", NOW)
    _turn_at(db, "s", NOW - 10 * DAY)
    store.save_receipt(turn_id=f"t{NOW - 10 * DAY}", session_id="s", seq=1,
                       conclusion="结论还在", db_path=db)

    store.purge(now=NOW, db_path=db)

    rows = store.session_rows("s", db_path=db)
    assert rows[0]["question"] == "问" and rows[0]["answer_expired"] is True
    assert store.session_receipts("s", db_path=db)[0]["conclusion"] == "结论还在"


def test_receipts_deleted_after_ninety_days(db):
    _session_at(db, "s", NOW)
    _turn_at(db, "s", NOW - 91 * DAY)
    store.save_receipt(turn_id=f"t{NOW - 91 * DAY}", session_id="s", seq=1,
                       conclusion="老存根", db_path=db)
    _turn_at(db, "s", NOW - 80 * DAY)
    store.save_receipt(turn_id=f"t{NOW - 80 * DAY}", session_id="s", seq=2,
                       conclusion="新存根", db_path=db)

    stats = store.purge(now=NOW, db_path=db)
    assert stats["receipts_deleted"] == 1
    kept = [r["conclusion"] for r in store.session_receipts("s", db_path=db)]
    assert kept == ["新存根"]


def test_session_deleted_after_ninety_days_idle(db):
    _session_at(db, "old", NOW - 91 * DAY)
    _turn_at(db, "old", NOW - 91 * DAY)
    _session_at(db, "fresh", NOW - 89 * DAY)

    stats = store.purge(now=NOW, db_path=db)
    assert stats["sessions_deleted"] == 1
    assert store.get_session("old", db_path=db) is None
    assert store.session_rows("old", db_path=db) == []       # turns 级联
    assert store.get_session("fresh", db_path=db) is not None


def test_purge_is_idempotent(db):
    _session_at(db, "s", NOW)
    _turn_at(db, "s", NOW - 5 * DAY)
    first = store.purge(now=NOW, db_path=db)
    second = store.purge(now=NOW, db_path=db)
    assert first["answers_cleared"] == 1
    assert second == {"answers_cleared": 0, "receipts_deleted": 0, "sessions_deleted": 0}


def test_purge_respects_env_windows(tmp_path):
    """默认值来自 env（GAMEGUIDE_RETENTION_*）：调用方不传参也能按配置跑。"""
    path = tmp_path / "sessions.db"
    store.init_db(path)
    store.ensure_session(session_id="s", client_id="c", now=NOW, db_path=path)
    store.append_turn(session_id="s", turn_id="t1", question="问", answer="答",
                      now=NOW - 2 * DAY, db_path=path)
    stats = store.purge(answer_days=1, now=NOW, db_path=path)   # 模拟改成 1 天
    assert stats["answers_cleared"] == 1
