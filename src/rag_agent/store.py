"""会话存储（SQLite）：sessions / turns / receipts。

实施规格：`docs/SESSION_STORE_IMPL.md`；设计动机：`docs/SESSION_MEMORY_PLAN.md`。

约定：
- 每次调用开一个连接、用完关（单进程低 QPS，SQLite 足够；WAL + busy_timeout）
- 失败统一抛 `StoreError`，由调用方降级到旧路径——存储故障不能变成 5xx
- 时间一律 unix 秒整数，与 `turn_record` 的 ts 同源，便于和 jsonl 对账
- 不存 IP、不存 UA、不存请求侧 history 原文
- 单条读写走 `_read_one/_read_all/_write`；需要多语句原子性的（append_turn、purge）
  自己开场事务
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

# 单条消息截断上限与 trim_history 共用同一个常量，避免两处规则漂移
from rag_agent.history_trim import MAX_MSG_CHARS

__all__ = [
    "StoreError",
    "MAX_MSG_CHARS",
    "DEFAULT_DB",
    "set_db_path",
    "current_db_path",
    "init_db",
    "ensure_session",
    "touch_session",
    "get_session",
    "list_sessions",
    "delete_session",
    "reset_progress",
    "update_game",
    "append_turn",
    "session_turns",
    "session_rows",
    "save_receipt",
    "session_receipts",
    "purge",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "sessions.db"

# 模块级默认库路径；api_server 启动时用 set_db_path() 指过去，测试用 tmp 目录
_DB_PATH: Optional[Path] = None


class StoreError(RuntimeError):
    """存储不可用（打不开 / 写失败 / 表缺失）。调用方应降级而不是报错给用户。"""


def set_db_path(path) -> None:
    global _DB_PATH
    _DB_PATH = Path(path) if path else None


def current_db_path() -> Path:
    return _DB_PATH or Path(os.environ.get("GAMEGUIDE_SESSION_DB") or DEFAULT_DB)


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    client_id    TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    last_active  INTEGER NOT NULL,
    game         TEXT,
    progress     INTEGER,
    progress_src TEXT,
    title        TEXT,
    n_turns      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_client ON sessions(client_id, last_active DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(last_active);

CREATE TABLE IF NOT EXISTS turns (
    turn_id         TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    ts              INTEGER NOT NULL,
    question        TEXT NOT NULL,
    answer          TEXT,
    route           TEXT,
    game            TEXT,
    progress_level  INTEGER,
    progress_source TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    tool_calls      INTEGER,
    elapsed_ms      INTEGER,
    answer_partial  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, seq);

CREATE TABLE IF NOT EXISTS receipts (
    turn_id    TEXT PRIMARY KEY REFERENCES turns(turn_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    conclusion TEXT,
    sources    TEXT,
    entry_acts TEXT,
    entities   TEXT
);
CREATE INDEX IF NOT EXISTS idx_receipts_session ON receipts(session_id, seq);
"""


def _now(now=None) -> int:
    return int(now if now is not None else time.time())


def _connect(path=None) -> sqlite3.Connection:
    target = Path(path) if path else current_db_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None：关掉隐式事务，写操作自己 BEGIN IMMEDIATE
        conn = sqlite3.connect(str(target), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except (sqlite3.Error, OSError) as e:
        raise StoreError(f"打不开会话库 {target}: {e}") from e


def _write(sql: str, params=(), *, db_path=None) -> int:
    """单条写语句，返回 rowcount。"""
    conn = _connect(db_path)
    try:
        return conn.execute(sql, params).rowcount
    except sqlite3.Error as e:
        raise StoreError(f"写库失败: {e}") from e
    finally:
        conn.close()


def _read_all(sql: str, params=(), *, db_path=None) -> List[Dict]:
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.Error as e:
        raise StoreError(f"读库失败: {e}") from e
    finally:
        conn.close()


def _read_one(sql: str, params=(), *, db_path=None) -> Optional[Dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None
    except sqlite3.Error as e:
        raise StoreError(f"读库失败: {e}") from e
    finally:
        conn.close()


def init_db(path=None) -> None:
    """幂等建表。失败抛 StoreError（调用方只告警，服务照常起）。"""
    conn = _connect(path)
    try:
        conn.executescript(SCHEMA)
    except sqlite3.Error as e:
        raise StoreError(f"建表失败: {e}") from e
    finally:
        conn.close()


# ====== sessions ======


def ensure_session(*, session_id: str, client_id: str, now=None, db_path=None) -> bool:
    """不存在就建空 session。返回 True=新建，False=已存在。"""
    ts = _now(now)
    rows = _write(
        "INSERT OR IGNORE INTO sessions"
        " (session_id, client_id, created_at, last_active, n_turns)"
        " VALUES (?, ?, ?, ?, 0)",
        (session_id, client_id, ts, ts), db_path=db_path,
    )
    return rows > 0


def touch_session(session_id: str, *, now=None, db_path=None) -> None:
    _write("UPDATE sessions SET last_active=? WHERE session_id=?",
           (_now(now), session_id), db_path=db_path)


def get_session(session_id: str, *, db_path=None) -> Optional[Dict]:
    return _read_one("SELECT * FROM sessions WHERE session_id=?",
                     (session_id,), db_path=db_path)


def list_sessions(client_id: str, *, limit: int = 10, db_path=None) -> List[Dict]:
    """只返回该 client 的会话，按最近活跃倒序。跨 client 不可见。"""
    return _read_all(
        "SELECT s.*, (SELECT COUNT(*) FROM turns t"
        "             WHERE t.session_id = s.session_id AND t.answer IS NOT NULL)"
        "        AS turns_available"
        " FROM sessions s WHERE s.client_id=?"
        " ORDER BY s.last_active DESC LIMIT ?",
        (client_id, int(limit)), db_path=db_path,
    )


def delete_session(session_id: str, *, db_path=None) -> bool:
    return _write("DELETE FROM sessions WHERE session_id=?", (session_id,),
                  db_path=db_path) > 0


def reset_progress(session_id: str, *, db_path=None) -> None:
    """进度归零（用户明说重开）。单调不减的唯一例外。"""
    _write("UPDATE sessions SET progress=NULL, progress_src='reset'"
           " WHERE session_id=?", (session_id,), db_path=db_path)


def update_game(session_id: str, game: str, *, db_path=None) -> None:
    if not game:
        return
    _write("UPDATE sessions SET game=? WHERE session_id=?", (game, session_id),
           db_path=db_path)


# ====== turns ======


def append_turn(*, session_id: str, turn_id: str, question: str,
                answer: Optional[str], route: Optional[str] = None,
                game: Optional[str] = None,
                progress_level: Optional[int] = None,
                progress_source: Optional[str] = None,
                tokens_in: Optional[int] = None, tokens_out: Optional[int] = None,
                tool_calls: Optional[int] = None, elapsed_ms: Optional[int] = None,
                answer_partial: bool = False, now=None, db_path=None) -> int:
    """落一轮问答，返回会话内 seq。

    单事务：BEGIN IMMEDIATE（先拿写锁，防并发算出同一个 seq）→ INSERT turns
    → UPDATE sessions（last_active / n_turns / title / game / 进度 max）。
    session 已被删除时抛 StoreError，不留孤儿行。
    """
    ts = _now(now)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as e:
        conn.close()
        raise StoreError(f"取写锁失败: {e}") from e
    try:
        row = conn.execute("SELECT progress, progress_src FROM sessions WHERE session_id=?",
                           (session_id,)).fetchone()
        if row is None:
            raise StoreError(f"session 不存在: {session_id}")
        old_progress, old_src = row["progress"], row["progress_src"]

        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM turns WHERE session_id=?",
                           (session_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO turns (turn_id, session_id, seq, ts, question, answer, route,"
            " game, progress_level, progress_source, tokens_in, tokens_out, tool_calls,"
            " elapsed_ms, answer_partial)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (turn_id, session_id, seq, ts, question, answer, route, game,
             progress_level, progress_source, tokens_in, tokens_out, tool_calls,
             elapsed_ms, 1 if answer_partial else 0),
        )
        # 进度单调不减：本轮没推断出来就不覆盖已有值
        if progress_level is not None and (old_progress is None or progress_level > old_progress):
            old_progress, old_src = int(progress_level), progress_source
        conn.execute(
            "UPDATE sessions SET last_active=?, n_turns=n_turns+1,"
            " title=COALESCE(title, ?), game=COALESCE(?, game),"
            " progress=?, progress_src=? WHERE session_id=?",
            (ts, question[:40], game, old_progress, old_src, session_id),
        )
        conn.execute("COMMIT")
        return int(seq)
    except sqlite3.Error as e:
        conn.execute("ROLLBACK")
        raise StoreError(f"append_turn 失败: {e}") from e
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _truncate(text: str) -> str:
    if len(text) <= MAX_MSG_CHARS:
        return text
    return text[:MAX_MSG_CHARS] + "（后略）"


def session_turns(session_id: str, *, limit_turns: int = 6, max_chars: int = 6000,
                  db_path=None) -> List[Dict]:
    """最近 N 轮原文，展开成 [{"role","content"}, ...] 供模型上下文使用。

    规则与 trim_history 对齐：单条 >2000 字截断；总字符超预算从旧往新丢整轮；
    最后一轮无条件保留。answer 已过期（NULL）的轮次跳过——问题没有答案接不上。
    """
    rows = _read_all(
        "SELECT question, answer FROM turns"
        " WHERE session_id=? AND answer IS NOT NULL ORDER BY seq DESC LIMIT ?",
        (session_id, int(limit_turns)), db_path=db_path,
    )

    msgs: List[Dict] = []
    for r in reversed(rows):
        msgs.append({"role": "user", "content": _truncate(r["question"] or "")})
        msgs.append({"role": "assistant", "content": _truncate(r["answer"] or "")})

    total = sum(len(m["content"]) for m in msgs)
    while len(msgs) > 2 and total > max_chars:
        total -= len(msgs[0]["content"]) + len(msgs[1]["content"])
        del msgs[:2]
    return msgs


def session_rows(session_id: str, *, limit: int = 50, db_path=None) -> List[Dict]:
    """给前端恢复历史用：最近 limit 轮，按 seq 升序，含过期与半截标记。"""
    rows = _read_all(
        "SELECT turn_id, seq, ts, question, answer, game, route, answer_partial"
        " FROM turns WHERE session_id=? ORDER BY seq DESC LIMIT ?",
        (session_id, int(limit)), db_path=db_path,
    )
    out = []
    for r in reversed(rows):
        r["answer_expired"] = r["answer"] is None
        r["answer_partial"] = bool(r["answer_partial"])
        out.append(r)
    return out


# ====== receipts（v2：更早轮次的确定性存根）======


def save_receipt(*, turn_id: str, session_id: str, seq: int,
                 conclusion: Optional[str] = None, sources=None,
                 entry_acts=None, entities=None, db_path=None) -> None:
    def _dump(value):
        return json.dumps(value, ensure_ascii=False) if value else None

    _write(
        "INSERT OR REPLACE INTO receipts"
        " (turn_id, session_id, seq, conclusion, sources, entry_acts, entities)"
        " VALUES (?,?,?,?,?,?,?)",
        (turn_id, session_id, seq, conclusion,
         _dump(sources), _dump(entry_acts), _dump(entities)),
        db_path=db_path,
    )


def session_receipts(session_id: str, *, limit: int = 20, db_path=None) -> List[Dict]:
    """最早的 N 条存根（原文窗口负责最近几轮，存根补更早的）。"""
    return _read_all("SELECT * FROM receipts WHERE session_id=? ORDER BY seq ASC LIMIT ?",
                     (session_id, int(limit)), db_path=db_path)


# ====== 留存清理 ======


def purge(*, answer_days: int = 3, receipt_days: int = 90, session_days: int = 90,
          now=None, db_path=None) -> Dict[str, int]:
    """按留存策略清理：答案过期置空 / 存根过期删除 / 会话过期级联删除。

    幂等，可以每小时跑。返回各表影响行数。
    """
    ts = _now(now)
    answer_cutoff = ts - int(answer_days) * 86400
    receipt_cutoff = ts - int(receipt_days) * 86400
    session_cutoff = ts - int(session_days) * 86400

    conn = _connect(db_path)
    stats = {"answers_cleared": 0, "receipts_deleted": 0, "sessions_deleted": 0}
    try:
        conn.execute("BEGIN IMMEDIATE")
        stats["answers_cleared"] = conn.execute(
            "UPDATE turns SET answer=NULL, answer_partial=0"
            " WHERE answer IS NOT NULL AND ts < ?", (answer_cutoff,)).rowcount
        stats["receipts_deleted"] = conn.execute(
            "DELETE FROM receipts WHERE turn_id IN"
            " (SELECT turn_id FROM turns WHERE ts < ?)", (receipt_cutoff,)).rowcount
        stats["sessions_deleted"] = conn.execute(
            "DELETE FROM sessions WHERE last_active < ?", (session_cutoff,)).rowcount
        conn.execute("COMMIT")
        return stats
    except sqlite3.Error as e:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise StoreError(f"purge 失败: {e}") from e
    finally:
        conn.close()
