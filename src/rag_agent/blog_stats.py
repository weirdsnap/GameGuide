"""博客访问统计的存储层（方案见 docs/BLOG_ANALYTICS_PLAN.md）。

为什么存在：博客托管在 GitHub Pages，没有服务端日志；而全站 366 篇文章都是
`htmls/blog.html?post=...` 前端现渲染的，HTTP 层只能看到同一个路径。所以「哪篇
文章被读」必须由前端上报，这个模块就是那批上报数据的落点与读取口。

隐私上是刻意做成「结构上无法画像」，不是靠承诺：

- 不存原始 URL、不存完整 referrer、不存完整 IP；
- `visitor` 是 `hmac(salt, f"{day}|{ip}")[:16]`，按天换盐——UV 按天统计本来就够用，
  跨天不可能把同一个人串起来；
- 不写 cookie、不读 localStorage。

只有 SQLite 读写碰 I/O，`device_of` / `normalize_*` / `is_flagged` 都是纯函数。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "blog.db"

# 与 scripts/analysis/report.py 同一口径：固定 +8，不依赖部署机 TZ
TZ = timezone(timedelta(hours=8))

# 上报数据里能接受的短标签：斜杠开头、无空白、≤200 字
PATH_RE = re.compile(r"^/[^\s?#]{0,199}$")
# referrer 只留域名，能接受的形态就是 host[:port] 里的 host 部分
REF_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,98}[a-z0-9])?$", re.I)
BOT_RE = re.compile(
    r"bot|spider|crawl|slurp|preview|monitor|headless|phantom|curl|wget|"
    r"python-requests|go-http-client|axios|okhttp",
    re.I,
)
BLOG_ORIGINS = {
    "https://weirdsnap.github.io",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
}
KINDS = {"home", "list", "post", "hk", "other"}

# ONNX 之外的唯一密钥：只保护「同一天同一 IP 的摘要不可反推」，env 可覆盖
DEFAULT_SALT = "gameguide-blog-v1"


# ====== 纯函数 ======


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d")


def device_of(width) -> str:
    """视口宽度折成三档：不给宽度就当 desktop（宁可少分一档，也不要 null）。"""
    try:
        w = int(width)
    except (TypeError, ValueError):
        return "desktop"
    if w <= 0:
        return "desktop"
    if w < 768:
        return "mobile"
    if w < 1200:
        return "tablet"
    return "desktop"


def visitor_hash(ip: str, day: str, salt: Optional[str] = None) -> str:
    key = (salt or os.environ.get("GAMEGUIDE_BLOG_SALT") or DEFAULT_SALT).encode()
    msg = f"{day}|{ip or 'unknown'}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:16]


def normalize_path(raw) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    p = raw.strip()
    if not p.startswith("/"):
        return None
    p = p.split("#", 1)[0].split("?", 1)[0]
    if not PATH_RE.match(p):
        return None
    return p


def normalize_kind(raw) -> str:
    k = (raw or "").strip().lower()
    return k if k in KINDS else "other"


def normalize_ref(raw) -> Optional[str]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    r = raw.strip().lower()
    r = r.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    return r[:100] if REF_RE.match(r) else None


def is_flagged(user_agent: str, origin: str, referer_host: Optional[str] = None) -> int:
    """1 = 可疑，落库但统计默认剔除（保留数据便于复核误杀）。

    只标记、不拒绝：机器人过滤规则一定会有误杀，直接丢数据就再也没法回看。
    """
    if BOT_RE.search(user_agent or ""):
        return 1
    origin = (origin or "").strip().rstrip("/")
    if origin and origin not in BLOG_ORIGINS:
        return 1
    return 0


# ====== I/O ======


def db_path(override=None) -> Path:
    return Path(override or os.environ.get("GAMEGUIDE_BLOG_DB") or DEFAULT_DB)


SCHEMA = """
CREATE TABLE IF NOT EXISTS blog_hits (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      INTEGER NOT NULL,
  day     TEXT    NOT NULL,
  path    TEXT    NOT NULL,
  kind    TEXT    NOT NULL,
  ref     TEXT,
  device  TEXT,
  visitor TEXT,
  flagged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_hits_day  ON blog_hits(day);
CREATE INDEX IF NOT EXISTS idx_hits_path ON blog_hits(path, day);
"""


def connect(path=None) -> sqlite3.Connection:
    """每次调用新建连接：上报频率低，连接池只带来跨线程麻烦。"""
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=5.0)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    return conn


def record_hit(path=None, *, ts: Optional[float] = None, path_label: str,
               kind: str = "other", ref: Optional[str] = None,
               device: str = "desktop", visitor: Optional[str] = None,
               flagged: int = 0) -> None:
    """写一条访问记录。任何异常交给调用方决定（端点里是「静默丢」）。"""
    ts = float(ts) if ts is not None else datetime.now(TZ).timestamp()
    conn = connect(path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO blog_hits (ts, day, path, kind, ref, device, visitor, flagged)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (int(ts), day_of(ts), path_label, kind, ref, device,
                 visitor or "", 1 if flagged else 0),
            )
    finally:
        conn.close()


class HitRateLimiter:
    """上报限速：同 IP 每分钟最多 per_min 次，超了静默丢弃。

    与提问用的 RateLimiter 分开：博客上报不该占用户每日提问额度，也不需要
    「一天 600 次」那种语义（一个访客一天翻几十篇文章是正常的）。超限回 204
    而不是 429——不给爬虫任何「被识别」的反馈。
    """

    def __init__(self, per_min: int = 60, clock=time.time):
        self._per_min = per_min
        self._clock = clock
        self._hits: dict[str, list] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        now = self._clock()
        cutoff = now - 60
        with self._lock:
            bucket = [t for t in self._hits.get(ip, []) if t > cutoff]
            if len(bucket) >= self._per_min:
                self._hits[ip] = bucket
                return False
            bucket.append(now)
            self._hits[ip] = bucket
            if len(self._hits) > 4096:            # 极端情况下别把内存吃满
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > cutoff}
            return True


def fetch_rows(path=None, days: Optional[int] = None, until: Optional[float] = None) -> list[dict]:
    """取最近 days 天的明细（含当天），按 ts 升序。库不存在返回空列表。"""
    p = db_path(path)
    if not p.is_file():
        return []
    until = float(until) if until is not None else datetime.now(TZ).timestamp()
    since_day = day_of(until - (max(days, 1) - 1) * 86400) if days else None
    sql = "SELECT ts, day, path, kind, ref, device, visitor, flagged FROM blog_hits"
    args: tuple = ()
    if since_day:
        sql += " WHERE day >= ?"
        args = (since_day,)
    sql += " ORDER BY ts ASC"
    conn = sqlite3.connect(p, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()
