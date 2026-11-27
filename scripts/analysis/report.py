"""turn / feedback 日志的统计层：一份口径，两个消费方。

- CLI：`scripts/analysis/turns_report.py`（终端看数）
- API：`api_server.py` 的 `/api/stats` 与 `/api/dashboard/*`（看板）

设计动机见 `docs/DASHBOARD_PLAN.md` §4：统计口径以前分散在 CLI 与 handler 两处，
出现过「都叫 avg 但一个算 p50、一个算 p90」的分歧。现在全部收在这里。

只有 `load_turns` / `load_feedback` 碰 I/O，其余都是纯函数，可直接造数据单测。
"""

from __future__ import annotations

import json
import os
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TURNS_DIR = PROJECT_ROOT / "data" / "turns"
DEFAULT_FEEDBACK_DIR = PROJECT_ROOT / "data" / "feedback"

# Asia/Shanghai 是固定 +8，没有夏令时；不依赖服务器 TZ，避免部署机改成 UTC 后日期分组漂移
TZ_OFFSET_HOURS = 8
# 本机 pytest / 联调请求的特征 IP（I-22）：默认剔除，否则均值被稀释
LOCAL_IPS = {"127.0.0.1", "::1", "localhost", ""}
# 上下文预算验收线（docs/CONTEXT_BUDGET_PLAN.md / I-09）
BUDGET_P90_TOKENS = 40000
# 告警/筛选里「慢轮」的默认阈值
SLOW_MS = 30000


# ====== I/O ======


def turns_dir(override=None) -> Path:
    return Path(override or os.environ.get("GAMEGUIDE_TURNS_DIR") or DEFAULT_TURNS_DIR)


def feedback_dir(override=None) -> Path:
    return Path(override or os.environ.get("GAMEGUIDE_FEEDBACK_DIR") or DEFAULT_FEEDBACK_DIR)


def _day_cutoff(days: Optional[int]) -> Optional[str]:
    """返回最早应包含的日期字符串（含当天往前 days-1 天）。"""
    if not days:
        return None
    return (datetime.now() - timedelta(days=int(days) - 1)).strftime("%Y-%m-%d")


def _read_jsonl(directory: Path, days: Optional[int]) -> List[dict]:
    """按文件名（YYYY-MM-DD）筛日期后逐行解析；坏行跳过（进程被 kill 时可能留半行）。"""
    cutoff = _day_cutoff(days)
    files = sorted(directory.glob("*.jsonl"))
    if cutoff:
        files = [f for f in files if f.stem >= cutoff]     # 文件名即日期，字典序可直接比
    records: List[dict] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def load_turns(days: Optional[int] = None, *, game: Optional[str] = None,
               include_local: bool = False, directory=None) -> List[dict]:
    """读 turn 日志。days=None 表示全部。默认剔除本机测试请求。"""
    records = _read_jsonl(turns_dir(directory), days)
    if not include_local:
        records = [r for r in records if _ip(r) not in LOCAL_IPS]
    if game:
        records = [r for r in records if r.get("game") == game]
    return records


def load_feedback(days: Optional[int] = None, *, directory=None) -> List[dict]:
    return _read_jsonl(feedback_dir(directory), days)


# ====== 基础工具（纯函数）======


def _ip(record: dict) -> str:
    return (record.get("ip") or "").strip()


def day_of(ts, tz_offset: int = TZ_OFFSET_HOURS) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts or 0) + tz_offset * 3600))


def clock_of(ts, tz_offset: int = TZ_OFFSET_HOURS) -> str:
    return time.strftime("%m-%d %H:%M", time.gmtime(int(ts or 0) + tz_offset * 3600))


def redact_ip(ip: str) -> str:
    """只留前两段：页面只需要区分「是不是自己」，不需要定位到人。"""
    if not ip:
        return ""
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.x.x"
    return "ipv6"


def percentiles(values: Iterable[float]) -> Dict[str, float]:
    """最近秩分位数（与旧 `_pct` 同算法），空输入全 0，便于前端不做空值判断。"""
    vals = sorted(float(v or 0) for v in values)
    if not vals:
        return {"avg": 0.0, "p50": 0.0, "p90": 0.0, "p99": 0.0, "max": 0.0}

    def q(p: float) -> float:
        return round(vals[min(len(vals) - 1, int(round(p * (len(vals) - 1))))], 1)

    return {
        "avg": round(statistics.mean(vals), 1),
        "p50": q(0.5),
        "p90": q(0.9),
        "p99": q(0.99),
        "max": round(vals[-1], 1),
    }


def _is_failed(record: dict) -> bool:
    return bool(record.get("error")) or record.get("route") == "error"


def _spoiler_worst(record: dict) -> int:
    hits = record.get("spoiler_hits") or []
    if not hits:
        return int(record.get("spoiler_worst") or 0)
    return max(int(h.get("level", 1)) for h in hits)


def _num(record: dict, key: str) -> float:
    return float(record.get(key) or 0)


# ====== 聚合 ======


def summarize(records: List[dict]) -> dict:
    """总量口径。字段名与旧 `/api/stats` 兼容，新增的都是纯增量。"""
    elapsed = [_num(r, "elapsed_ms") / 1000 for r in records]
    tokens_in = [_num(r, "tokens_in") for r in records]
    tokens_out = [_num(r, "tokens_out") for r in records]
    tools = [_num(r, "tool_calls") for r in records]
    history = [_num(r, "history_msgs") for r in records]

    by_day: Counter = Counter(day_of(r.get("ts")) for r in records)
    games: Counter = Counter(r.get("game") or "unknown" for r in records)
    routes: Counter = Counter(r.get("route") or "none" for r in records)
    failed = sum(1 for r in records if _is_failed(r))
    spoiler = sum(1 for r in records if r.get("spoiler_hits"))
    l3 = sum(1 for r in records if _spoiler_worst(r) >= 3)

    e, ti = percentiles(elapsed), percentiles(tokens_in)
    return {
        "turns": len(records),
        "failed": failed,
        "fail_rate": round(failed / len(records), 4) if records else 0.0,
        "by_day": dict(sorted(by_day.items())),
        "games": dict(games.most_common()),
        "routes": dict(routes.most_common()),
        "elapsed_s": {"avg": e["avg"], "p50": e["p50"], "p90": e["p90"],
                      "p99": e["p99"], "max": e["max"]},
        "tokens_in_stats": ti,
        "tokens_out_stats": percentiles(tokens_out),
        "tool_calls_stats": percentiles(tools),
        "history_msgs_stats": percentiles(history),
        "tokens_in": int(sum(tokens_in)),
        "tokens_out": int(sum(tokens_out)),
        "tool_calls": int(sum(tools)),
        "spoiler_hit_turns": spoiler,
        "spoiler_l3_turns": l3,
    }


def daily_series(records: List[dict], feedback: Optional[List[dict]] = None,
                 *, days: Optional[int] = None, now: Optional[float] = None) -> List[dict]:
    """按日一行的序列，日期升序。喂给折线/柱状图。

    传 `days` 时返回**连续**的 days 天（尾巴是今天，无数据的天补 0）：
    看板的日期轴不能因为某天没人问就断，否则 7 天窗口里只有 6 天有数据时
    图上会忽然少一根柱、日期也不对齐，看起来像「只有今天有数据」。
    """
    buckets: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        buckets[day_of(r.get("ts"))].append(r)

    fb_bad: Counter = Counter()
    fb_good: Counter = Counter()
    for item in feedback or []:
        day = day_of(item.get("ts"))
        if int(item.get("rating") or 0) < 0:
            fb_bad[day] += 1
        elif int(item.get("rating") or 0) > 0:
            fb_good[day] += 1

    if days:
        now = float(now) if now is not None else time.time()
        day_list = [day_of(now - i * 86400) for i in range(max(int(days), 1) - 1, -1, -1)]
    else:
        day_list = sorted(buckets)

    series = []
    for day in day_list:
        rows = buckets.get(day, [])
        elapsed = percentiles([_num(r, "elapsed_ms") / 1000 for r in rows])
        tokens_in = percentiles([_num(r, "tokens_in") for r in rows])
        series.append({
            "date": day,
            "turns": len(rows),
            "failed": sum(1 for r in rows if _is_failed(r)),
            "elapsed_p90": elapsed["p90"],
            "tokens_in_p90": tokens_in["p90"],
            "tokens_in_max": tokens_in["max"],
            "history_msgs_avg": percentiles([_num(r, "history_msgs") for r in rows])["avg"],
            "spoiler": sum(1 for r in rows if r.get("spoiler_hits")),
            "l3": sum(1 for r in rows if _spoiler_worst(r) >= 3),
            "feedback_bad": fb_bad[day],
            "feedback_good": fb_good[day],
        })
    return series


def budget_status(records: List[dict], limit: int = BUDGET_P90_TOKENS) -> dict:
    p90 = percentiles([_num(r, "tokens_in") for r in records])["p90"]
    return {
        "limit": int(limit),
        "p90": p90,
        "ok": p90 <= limit,
        "over_by": max(0, int(p90 - limit)),
    }


def feedback_counts(feedback: List[dict]) -> dict:
    """按 turn_id 去重后统计（同一轮改主意只算最后一次，与 /api/feedback/stats 一致）。"""
    latest: Dict[str, dict] = {}
    for item in feedback:
        tid = item.get("turn_id")
        if not tid:
            continue
        prev = latest.get(tid)
        if prev is None or int(item.get("ts") or 0) >= int(prev.get("ts") or 0):
            latest[tid] = item
    good = sum(1 for v in latest.values() if int(v.get("rating") or 0) > 0)
    bad = sum(1 for v in latest.values() if int(v.get("rating") or 0) < 0)
    return {"total": len(latest), "good": good, "bad": bad,
            "bad_rate": round(bad / len(latest), 4) if latest else 0.0}


def link_feedback(turns: List[dict], feedback: List[dict], *, limit: int = 50) -> List[dict]:
    """反馈关联轮次详情：被点踩的是哪个问题、当时多慢、烧了多少 token。"""
    index = {r.get("turn_id"): r for r in turns if r.get("turn_id")}
    latest: Dict[str, dict] = {}
    for item in feedback:
        tid = item.get("turn_id")
        if not tid:
            continue
        prev = latest.get(tid)
        if prev is None or int(item.get("ts") or 0) >= int(prev.get("ts") or 0):
            latest[tid] = item

    items = []
    for tid, item in latest.items():
        turn = index.get(tid) or {}
        items.append({
            "turn_id": tid,
            "ts": int(item.get("ts") or 0),
            "time": clock_of(item.get("ts")),
            "rating": int(item.get("rating") or 0),
            "question": turn.get("question") or item.get("question") or "",
            "game": turn.get("game") or item.get("game"),
            "route": turn.get("route"),
            "elapsed_ms": int(turn.get("elapsed_ms") or 0),
            "tokens_in": int(turn.get("tokens_in") or 0),
            "answer_head": turn.get("answer_head") or "",
            "matched": bool(turn),      # False = 轮次日志已过期/被清，只剩反馈本身
        })
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items[: int(limit)]


# ====== 轮次列表（看板 ③）======


def turn_row(record: dict) -> dict:
    """单轮的展示形态。question/answer_head 在写入时已截断（120/200 字）。"""
    hits = record.get("spoiler_hits") or []
    return {
        "ts": int(record.get("ts") or 0),
        "time": clock_of(record.get("ts")),
        "turn_id": record.get("turn_id"),
        "session_id": record.get("session_id"),
        "game": record.get("game"),
        "route": record.get("route"),
        "elapsed_ms": int(record.get("elapsed_ms") or 0),
        "tokens_in": int(record.get("tokens_in") or 0),
        "tokens_out": int(record.get("tokens_out") or 0),
        "tool_calls": int(record.get("tool_calls") or 0),
        "history_msgs": int(record.get("history_msgs") or 0),
        "stream": bool(record.get("stream")),
        "progress_level": record.get("progress_level"),
        "spoiler_level": _spoiler_worst(record),
        "spoiler_terms": [h.get("term") for h in hits][:5],
        "question": record.get("question") or "",
        "answer_head": record.get("answer_head") or "",
        "error": record.get("error"),
        "ip": redact_ip(_ip(record)),
    }


def filter_turns(records: List[dict], *, date: Optional[str] = None,
                 game: Optional[str] = None, bad_turn_ids: Optional[set] = None,
                 only_bad: bool = False, only_l3: bool = False,
                 only_slow: bool = False, slow_ms: int = SLOW_MS,
                 before: Optional[int] = None, before_id: Optional[str] = None,
                 limit: int = 50,
                 tz_offset: int = TZ_OFFSET_HOURS) -> dict:
    """筛选 + 游标分页。游标是上一页最后一条的 `(ts, turn_id)`，取严格更早的。

    只用 ts 做游标会在同一秒多条时丢数据（评测/脚本连发就会撞），所以带上
    turn_id 组成复合游标；排序也用同一元组，保证两次请求顺序稳定。
    """
    rows = [r for r in records if r.get("ts")]
    if date:
        rows = [r for r in rows if day_of(r.get("ts"), tz_offset) == date]
    if game:
        rows = [r for r in rows if r.get("game") == game]
    if only_bad:
        bad = bad_turn_ids or set()
        rows = [r for r in rows if r.get("turn_id") in bad]
    if only_l3:
        rows = [r for r in rows if _spoiler_worst(r) >= 3]
    if only_slow:
        rows = [r for r in rows if int(r.get("elapsed_ms") or 0) >= slow_ms]
    if before is not None:
        cursor = (int(before), before_id or "")
        rows = [r for r in rows
                if (int(r.get("ts") or 0), r.get("turn_id") or "") < cursor]

    def _key(r):
        return (int(r.get("ts") or 0), r.get("turn_id") or "")

    rows.sort(key=_key, reverse=True)
    page, seen = [], set()
    for r in rows:
        tid = r.get("turn_id")
        if tid and tid in seen:
            continue
        if len(page) >= limit:
            break
        page.append(turn_row(r))
        if tid:
            seen.add(tid)

    full_page = len(page) >= limit
    return {
        "turns": page,
        "matched": len(rows),
        "next_before": page[-1]["ts"] if full_page else None,
        "next_before_id": page[-1]["turn_id"] if full_page else None,
    }


# ====== 博客访问（docs/BLOG_ANALYTICS_PLAN.md）======


def blog_series(rows: List[dict], days: int = 7, *, now: Optional[float] = None) -> List[dict]:
    """连续 days 天的 PV/UV 序列，缺的日子补 0（折线不能因为没数据就断）。"""
    now = float(now) if now is not None else time.time()
    buckets: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        buckets[r.get("day") or day_of(r.get("ts"))].append(r)
    out = []
    for i in range(max(int(days), 1) - 1, -1, -1):
        day = day_of(now - i * 86400)
        bucket = buckets.get(day, [])
        out.append({
            "day": day,
            "pv": len(bucket),
            "uv": len({r.get("visitor") for r in bucket if r.get("visitor")}),
        })
    return out


# 文章标题来自博客仓库的 posts/index.json（GitHub Pages 前端读的同一份），
# 看板只借它把 /post/cpp/ch02/02.md 这种 id 翻成「标题 + id」。
BLOG_INDEX_ENV = "GAMEGUIDE_BLOG_INDEX"
DEFAULT_BLOG_INDEX = Path("/data/learning/weirdsnap.github.io/posts/index.json")

_TITLE_CACHE: Dict[str, object] = {"key": None, "titles": {}}


def blog_index_path(override=None) -> Path:
    """index.json 位置：显式参数 > 环境变量 > 本机博客仓库默认路径。"""
    if override:
        return Path(override)
    env = os.environ.get(BLOG_INDEX_ENV)
    return Path(env) if env else DEFAULT_BLOG_INDEX


def path_label_key(raw) -> Optional[str]:
    """把落库的 path 标签与 index.json 的 path 归一到同一把 key。

    '/post/cpp/ch02/02.md' / 'posts/cpp/ch02/02.md' / 'cpp/ch02/02.md' → 'cpp/ch02/02'。
    两边都要过这一层，否则前缀（/post/、posts/）与后缀（.md）不同就永远对不上。
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    prefixes = ("/post/", "posts/", "/posts/")
    for _ in range(3):
        for prefix in prefixes:
            if s.startswith(prefix):
                s = s[len(prefix):]
                break
        else:
            break
    s = s.lstrip("/")
    if s.endswith(".md"):
        s = s[:-3]
    return s or None


def _collect_titles(node, out: Dict[str, str]) -> None:
    """递归收 {path: title}：index.json 有 category/subs 嵌套，也兼容平铺文章列表。"""
    if isinstance(node, dict):
        path, title = node.get("path"), node.get("title")
        if isinstance(path, str) and isinstance(title, str) and title.strip():
            key = path_label_key(path)
            if key:
                out.setdefault(key, title.strip())
        for value in node.values():
            _collect_titles(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_titles(item, out)


def load_post_titles(path=None) -> Dict[str, str]:
    """读 posts/index.json 拿「id → 标题」。

    按 (路径, mtime, size) 缓存，博客仓库 git pull 换掉 index.json 后自动失效；
    文件缺失/坏了就返回空表，看板退回只显示 id（不因为一个可选装饰字段抛错）。
    """
    p = blog_index_path(path)
    try:
        st = p.stat()
    except OSError:
        _TITLE_CACHE["key"] = None
        _TITLE_CACHE["titles"] = {}
        return {}
    key = (str(p), int(st.st_mtime), int(st.st_size))
    if _TITLE_CACHE["key"] == key:
        return dict(_TITLE_CACHE["titles"])
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    titles: Dict[str, str] = {}
    _collect_titles(data, titles)
    _TITLE_CACHE["key"] = key
    _TITLE_CACHE["titles"] = titles
    return dict(titles)


def blog_summary(rows: List[dict], days: int = 7, *, now: Optional[float] = None,
                 include_flagged: bool = False,
                 titles: Optional[Dict[str, str]] = None) -> dict:
    """博客访问聚合：总量 + 按日序列 + top 文章（带标题） + 来源域名 + 设备分布。

    rows 由 `rag_agent.blog_stats.fetch_rows` 从 SQLite 取出（明细）。
    flagged=1（疑似机器人/陌生 Origin）默认剔除，只把条数报出来——过滤规则一定
    会误杀，数据留在库里可以复核，统计口径先干净。
    titles 传 None 表示现读 posts/index.json（带 mtime 缓存），传 {} 则只给 id。
    """
    if titles is None:
        titles = load_post_titles()
    rows = list(rows or [])
    if include_flagged:
        valid, hidden = rows, 0
    else:
        valid = [r for r in rows if not int(r.get("flagged") or 0)]
        hidden = len(rows) - len(valid)

    def _uv(items: List[dict]) -> int:
        return len({r.get("visitor") for r in items if r.get("visitor")})

    posts: Dict[str, List[dict]] = defaultdict(list)
    refs: Counter = Counter()
    devices: Counter = Counter()
    for r in valid:
        if r.get("kind") == "post":
            posts[r.get("path") or "?"].append(r)
        if r.get("ref"):
            refs[r["ref"]] += 1
        devices[r.get("device") or "unknown"] += 1

    top_posts = []
    for path, items in posts.items():
        key = path_label_key(path)
        top_posts.append({
            "path": path,
            "title": titles.get(key) if key else None,
            "pv": len(items),
            "uv": _uv(items),
        })
    top_posts.sort(key=lambda x: (-x["pv"], x["path"]))
    total = len(valid) or 1

    return {
        "days": int(days),
        "pv": len(valid),
        "uv": _uv(valid),
        "series": blog_series(valid, days, now=now),
        "top_posts": top_posts[:10],
        "refs": [{"ref": ref, "pv": n} for ref, n in refs.most_common(10)],
        "devices": [
            {"device": d, "pv": n, "share": round(n / total, 3)}
            for d, n in devices.most_common()
        ],
        "flagged": hidden,
    }
