#!/usr/bin/env python3
"""会话存储（rag_agent.store）契约测试。

规格：docs/SESSION_STORE_IMPL.md §2–§3、§10。
全部用 tmp_path 的独立库，不碰生产 data/sessions.db。
"""

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from rag_agent import store  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "sessions.db"
    store.init_db(path)
    return path


def _mk_session(db, sid="s1", cid="c1", now=1000):
    store.ensure_session(session_id=sid, client_id=cid, now=now, db_path=db)
    return sid


def _add(db, sid="s1", q="问题", a="答案", **kw):
    kw.setdefault("now", 1000)
    return store.append_turn(session_id=sid, turn_id=kw.pop("turn_id", f"t{kw['now']}{q}"),
                             question=q, answer=a, db_path=db, **kw)


# ====== 建表与基本读写 ======

def test_init_db_idempotent(db):
    store.init_db(db)          # 再来一次不报错
    store.init_db(db)
    assert store.get_session("nope", db_path=db) is None


def test_ensure_session_reports_created(db):
    assert store.ensure_session(session_id="a", client_id="c", db_path=db) is True
    assert store.ensure_session(session_id="a", client_id="c", db_path=db) is False
    row = store.get_session("a", db_path=db)
    assert row["client_id"] == "c" and row["n_turns"] == 0


def test_append_turn_seq_and_counters(db):
    sid = _mk_session(db)
    seq1 = _add(db, sid, q="第一问", a="第一答", now=1001)
    seq2 = _add(db, sid, q="第二问", a="第二答", now=1002)
    assert (seq1, seq2) == (1, 2)
    row = store.get_session(sid, db_path=db)
    assert row["n_turns"] == 2
    assert row["title"] == "第一问"          # 标题取首问，不被后一轮覆盖
    assert row["last_active"] == 1002


def test_append_turn_unknown_session_raises(db):
    with pytest.raises(store.StoreError):
        store.append_turn(session_id="ghost", turn_id="t1", question="q", answer="a",
                          db_path=db)
    # 不留孤儿行
    assert store.session_rows("ghost", db_path=db) == []


def test_append_turn_partial_answer_flagged(db):
    sid = _mk_session(db)
    _add(db, sid, q="q", a="半截", answer_partial=True)
    rows = store.session_rows(sid, db_path=db)
    assert rows[-1]["answer"] == "半截"


def test_concurrent_append_no_duplicate_seq(db):
    sid = _mk_session(db)
    errors = []

    def worker(tag):
        try:
            for i in range(10):
                store.append_turn(session_id=sid, turn_id=f"{tag}-{i}",
                                  question=f"{tag}{i}", answer="a",
                                  now=2000 + i, db_path=db)
        except Exception as e:          # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    rows = store.session_rows(sid, limit=100, db_path=db)
    seqs = [r["seq"] for r in rows]
    assert seqs == list(range(1, 21))   # 连续且不重号
    assert store.get_session(sid, db_path=db)["n_turns"] == 20


# ====== 进度单调 ======

def test_progress_only_increases(db):
    sid = _mk_session(db)
    _add(db, sid, q="第一章", a="a", progress_level=1, progress_source="question")
    assert store.get_session(sid, db_path=db)["progress"] == 1
    _add(db, sid, q="随便问问", a="a", progress_level=None)
    assert store.get_session(sid, db_path=db)["progress"] == 1     # 不回退、不覆盖
    _add(db, sid, q="第三章", a="a", progress_level=3, progress_source="question")
    row = store.get_session(sid, db_path=db)
    assert row["progress"] == 3 and row["progress_src"] == "question"
    _add(db, sid, q="第二章", a="a", progress_level=2, progress_source="question")
    assert store.get_session(sid, db_path=db)["progress"] == 3     # 只增不减


def test_reset_progress_is_explicit_escape_hatch(db):
    sid = _mk_session(db)
    _add(db, sid, q="第三章", a="a", progress_level=3, progress_source="question")
    store.reset_progress(sid, db_path=db)
    row = store.get_session(sid, db_path=db)
    assert row["progress"] is None and row["progress_src"] == "reset"


# ====== 上下文装配 ======

def test_session_turns_skips_expired_answers(db):
    sid = _mk_session(db)
    _add(db, sid, q="问一", a="答一", now=1001)
    _add(db, sid, q="问二", a=None, now=1002)          # 过期/未答
    _add(db, sid, q="问三", a="答三", now=1003)
    msgs = store.session_turns(sid, db_path=db)
    contents = [m["content"] for m in msgs]
    assert contents == ["问一", "答一", "问三", "答三"]


def test_session_turns_truncates_long_message(db):
    sid = _mk_session(db)
    _add(db, sid, q="问", a="字" * 5000)
    msgs = store.session_turns(sid, db_path=db)
    assert len(msgs[-1]["content"]) == store.MAX_MSG_CHARS + len("（后略）")
    assert msgs[-1]["content"].endswith("（后略）")


def test_session_turns_drops_oldest_over_budget(db):
    sid = _mk_session(db)
    for i in range(6):
        _add(db, sid, q=f"问{i}" + "字" * 900, a=f"答{i}" + "字" * 900, now=1000 + i)
    msgs = store.session_turns(sid, limit_turns=6, max_chars=4000, db_path=db)
    # 每轮 ~1800 字，4000 字预算只装得下最近两轮
    assert len(msgs) == 4
    assert msgs[0]["content"].startswith("问4")
    assert msgs[-1]["content"].startswith("答5")       # 最后一轮永远保留


def test_session_turns_never_empty_when_last_turn_exists(db):
    sid = _mk_session(db)
    _add(db, sid, q="长" * 3000, a="长" * 3000)
    msgs = store.session_turns(sid, max_chars=100, db_path=db)
    assert len(msgs) == 2


# ====== 会话列表与隔离 ======

def test_list_sessions_isolated_and_sorted(db):
    store.ensure_session(session_id="a1", client_id="cA", now=100, db_path=db)
    store.ensure_session(session_id="a2", client_id="cA", now=300, db_path=db)
    store.ensure_session(session_id="b1", client_id="cB", now=200, db_path=db)
    rows = store.list_sessions("cA", db_path=db)
    assert [r["session_id"] for r in rows] == ["a2", "a1"]
    assert rows[0]["turns_available"] == 0
    assert store.list_sessions("cZ", db_path=db) == []


def test_list_sessions_limit(db):
    for i in range(5):
        store.ensure_session(session_id=f"s{i}", client_id="c", now=i, db_path=db)
    assert len(store.list_sessions("c", limit=2, db_path=db)) == 2


def test_delete_session_cascades(db):
    sid = _mk_session(db)
    _add(db, sid, q="q", a="a")
    assert store.delete_session(sid, db_path=db) is True
    assert store.get_session(sid, db_path=db) is None
    assert store.session_rows(sid, db_path=db) == []


def test_delete_session_requires_matching_client(db):
    """别人的会话删不掉，而且返回值和「不存在」一模一样（不能拿来探测）。"""
    _mk_session(db, sid="mine", cid="c1")
    _add(db, "mine", q="q", a="a")
    _mk_session(db, sid="other", cid="c2")

    assert store.delete_session("mine", client_id="c2", db_path=db) is False
    assert store.delete_session("nope", client_id="c1", db_path=db) is False
    assert store.get_session("mine", db_path=db) is not None      # 还在

    assert store.delete_session("mine", client_id="c1", db_path=db) is True
    assert store.get_session("other", db_path=db) is not None     # 别误删


def test_session_rows_marks_expired_answer(db):
    sid = _mk_session(db)
    _add(db, sid, q="问", a="答", now=1001)
    _add(db, sid, q="问2", a=None, now=1002)
    rows = store.session_rows(sid, db_path=db)
    assert [r["answer_expired"] for r in rows] == [False, True]


# ====== receipts ======

def test_save_and_read_receipts(db):
    sid = _mk_session(db)
    _add(db, sid, q="q", a="a", now=1001)
    store.save_receipt(turn_id="t1001q", session_id=sid, seq=1,
                       conclusion="铁手套在第三章希望之邸",
                       sources=["BG3 Wiki"], entry_acts=[3], db_path=db)
    rows = store.session_receipts(sid, db_path=db)
    assert rows[0]["conclusion"].startswith("铁手套")
    assert rows[0]["entry_acts"] == "[3]"


def test_store_error_on_unwritable_path(tmp_path):
    bad = tmp_path / "afile"
    bad.write_text("x")
    with pytest.raises(store.StoreError):
        store.init_db(bad / "sessions.db")      # 父路径是文件
