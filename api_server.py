"""
多游戏 Agent API Server
提供给博客前端调用的 API 接口（无密码、全局每日额度控制）。
自动识别问题所属游戏，路由到正确的 Agent 和知识库。
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
import sys
import uuid
from pathlib import Path
from collections import defaultdict

from aiohttp import web

# 添加 src 到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))

from rag_agent.multi_agent import ask, ask_stream, _game_from_history
from rag_agent import store

logger = logging.getLogger("gameguide.api")

# ====== 配置 ======
CONFIG_FILE = project_root / "api_config.json"

DEFAULT_CONFIG = {
    "rate_limit_per_min": 20,
    "rate_limit_per_day": 600,
    "daily_quota": 100,          # 全局每日提问上限（所有 IP / 所有会话共享）
    "quota_file": "data/quota.json",
    "trusted_proxies": ["127.0.0.1", "::1"],
    "request_timeout": 120,
    "stream_timeout": 240,      # 流式整体超时：之前没设，挂起会永久占着连接
    "port": 8765,
    "host": "0.0.0.0",
}

# turn 记录的按日 JSONL 目录（追加写，重启不丢；见 log_turn）
# 可用环境变量覆盖：跑测试时指向 tmp 目录，避免本地请求写脏生产统计（I-22）
TURNS_DIR = Path(os.environ.get("GAMEGUIDE_TURNS_DIR") or (project_root / "data" / "turns"))

# 用户反馈（点赞/点踩）的按日 JSONL 目录，同样追加写
FEEDBACK_DIR = Path(os.environ.get("GAMEGUIDE_FEEDBACK_DIR") or (project_root / "data" / "feedback"))

# ====== 会话存储（I-23，规格：docs/SESSION_STORE_IMPL.md）======
# 浏览器只带两个匿名 id：client_id（一个浏览器）/ session_id（一次对话）。
# 服务端用 SQLite 存 sessions/turns/receipts：刷新不丢历史、留存可清理、
# feedback 能 join 到具体一轮。存储故障一律降级回旧路径（前端带 history）。
SESSION_DB = Path(os.environ.get("GAMEGUIDE_SESSION_DB") or (project_root / "data" / "sessions.db"))
# 1 = 上下文由服务端装配（读 SQLite）；0 = 仍用请求体里的 history（迁移期默认）
SERVER_HISTORY = os.environ.get("GAMEGUIDE_SERVER_HISTORY", "0") == "1"
SESSION_MAX_TURNS = int(os.environ.get("GAMEGUIDE_SESSION_MAX_TURNS", "6"))
SESSION_MAX_CHARS = int(os.environ.get("GAMEGUIDE_SESSION_MAX_CHARS", "6000"))
# 留存：答案原文只留 3 天，存根与会话行 90 天（用户口径见 I-23）
RETENTION_ANSWER_DAYS = int(os.environ.get("GAMEGUIDE_RETENTION_ANSWER_DAYS", "3"))
RETENTION_RECEIPT_DAYS = int(os.environ.get("GAMEGUIDE_RETENTION_RECEIPT_DAYS", "90"))
RETENTION_SESSION_DAYS = int(os.environ.get("GAMEGUIDE_RETENTION_SESSION_DAYS", "90"))

# turn_id 由服务端生成：12 位十六进制，一整天不撞，也方便在日志里 grep
TURN_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")

# aiohttp 要求 Application 的额外键用 AppKey，否则每建一个 app 都报 NotAppKeyWarning
CLEANUP_TASK_KEY = web.AppKey("cleanup_task", asyncio.Task)


def new_turn_id() -> str:
    """生成一轮请求的 ID，前端反馈时用它指回具体某一轮。"""
    return uuid.uuid4().hex[:12]


# 额度用完时的统一文案（前端直接当一条回答展示），limit 由配置决定
QUOTA_EXCEEDED_TMPL = "今天的 {limit} 个问题额度已经用完了，明天再来吧"


def quota_exceeded_message(status: dict) -> str:
    """根据当前额度状态生成提示文案（不写死 100，改配置不用改代码）。"""
    return QUOTA_EXCEEDED_TMPL.format(limit=status.get("limit", ""))


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
            return {**DEFAULT_CONFIG, **cfg}
    # 首次运行自动写入默认配置
    with open(CONFIG_FILE, "w") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
    print(f"  📝 已生成默认配置: {CONFIG_FILE}")
    return dict(DEFAULT_CONFIG)


config = load_config()


def _app_version() -> str:
    """当前代码版本：优先 git describe（提交/标签），取不到再退回配置。

    以前 /api/status 里写死 "beta5"，项目早就 beta6 了，健康检查据此判断会误判。
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(project_root), "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return str(config.get("version") or "unknown")


APP_VERSION = _app_version()


def _client_ip(request) -> str:
    """只在已知反向代理之后采信 X-Forwarded-For 的第一个地址。"""
    remote = request.remote or "unknown"
    if remote not in config["trusted_proxies"]:
        return remote
    forwarded = request.headers.get("X-Forwarded-For", "")
    client = forwarded.split(",", 1)[0].strip()
    return client or remote


# ====== 全局每日额度 ======
class DailyQuota:
    """全局每日提问额度，跨 IP / 跨会话共享，文件持久化（重启不清零）。

    - 日期按本地时区切日（凌晨自然重置）
    - 写文件用临时文件 + os.replace 原子替换，避免写一半崩溃留下坏文件
    - threading.Lock 保证同一进程内多协程/多线程下计数不丢
    """

    def __init__(self, path: Path, limit: int = 100, clock=time.time):
        self._path = Path(path)
        self._limit = limit
        self._clock = clock
        self._lock = threading.Lock()
        self._state = self._load()

    @staticmethod
    def _day_key(ts: float) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(ts))

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "date" in data and isinstance(data.get("count"), int):
                return {"date": data["date"], "count": data["count"]}
        except (OSError, ValueError):
            pass
        # 首次运行 / 文件损坏 / 格式不对 → 从头计数，不因坏文件把服务拖挂
        return {"date": self._day_key(self._clock()), "count": 0}

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._path)

    def _roll(self):
        today = self._day_key(self._clock())
        if self._state.get("date") != today:
            self._state = {"date": today, "count": 0}

    def status(self) -> dict:
        with self._lock:
            self._roll()
            return {
                "date": self._state["date"],
                "used": self._state["count"],
                "limit": self._limit,
                "remaining": max(0, self._limit - self._state["count"]),
            }

    def consume(self) -> tuple[bool, dict]:
        """占用一次额度。返回 (是否允许, 最新状态)；超限时不计数。"""
        with self._lock:
            self._roll()
            if self._state["count"] >= self._limit:
                return False, self.status_unlocked()
            self._state["count"] += 1
            self._save()
            return True, self.status_unlocked()

    def refund(self, n: int = 1) -> dict:
        """退回额度。菜单轮 / 未识别游戏 / 失败轮不该计费（这些问题没调 LLM）。"""
        with self._lock:
            self._roll()
            self._state["count"] = max(0, self._state["count"] - n)
            self._save()
            return self.status_unlocked()

    def status_unlocked(self) -> dict:
        """已持锁时的 status（避免重复获取不可重入的 Lock）。"""
        return {
            "date": self._state["date"],
            "used": self._state["count"],
            "limit": self._limit,
            "remaining": max(0, self._limit - self._state["count"]),
        }


# 这些路由没调 LLM（菜单、未识别游戏、出错），不该占用户额度
NON_LLM_ROUTES = {"menu", "unknown", "error"}

quota = DailyQuota(project_root / config["quota_file"], limit=config["daily_quota"])


# ====== 速率限制 ======
class RateLimiter:
    def __init__(self):
        self.minute_buckets: dict[str, list[float]] = defaultdict(list)
        self.day_counts: dict[str, int] = defaultdict(int)
        self.day_reset = time.time()

    def check(self, ip: str) -> tuple[bool, str]:
        now = time.time()
        if now - self.day_reset > 86400:
            self.day_counts.clear()
            self.day_reset = now

        bucket = self.minute_buckets.get(ip, [])
        cutoff = now - 60
        self.minute_buckets[ip] = [t for t in bucket if t > cutoff]

        if len(self.minute_buckets[ip]) >= config["rate_limit_per_min"]:
            return False, f"请求过于频繁，每分钟限 {config['rate_limit_per_min']} 次"

        if self.day_counts[ip] >= config["rate_limit_per_day"]:
            return False, f"已达每日请求上限 ({config['rate_limit_per_day']})"

        return True, ""

    def record(self, ip: str):
        self.minute_buckets[ip].append(time.time())
        self.day_counts[ip] += 1


rate_limiter = RateLimiter()


# ====== CORS 中间件 ======
async def cors_middleware(app, handler):
    async def middleware_handler(request):
        if request.method == "OPTIONS":
            return web.Response(headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
                "Access-Control-Max-Age": "86400",
            })
        response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
    return middleware_handler


# (向量库按需懒加载，由 multi_agent 内部管理)


# ====== 观测：每轮一行的结构化记录（第 0 步，只记录不改行为）======
# ══════ 剧透审计词表（只审计、不拦截）══════
# 每轮答案里命中的后期内容记进 turn 日志的 spoiler_hits，用来从真实流量里
# 揪出「答越界了」的样本，做回归集。真拦截在提示词层与检索层，见
# docs/SPOILER_CONTROL_PLAN.md。
#
# 条目 = (词, 最早出现的幕, 伤害等级)：
#   act   对照用户进度用（进度轴，回答「该不该说」）
#   level 伤害等级 1/2/3（回答「说了有多糟」），定义与处置矩阵见 docs/SPOILER_SEVERITY.md
#   记忆法：L1 是地图（地名/道具名/机制存在性）、L2 是攻略（解法/数值/触发条件）、
#           L3 是谜底（身份反转/重大抉择后果/角色命运/结局）
SPOILER_TERMS: dict[str, list[tuple[str, int, int]]] = {
    "baldurs_gate3": [
        # act 2：地名与组织，知道也不损体验
        ("Mason's Guild", 2, 1), ("石匠工会", 2, 1),
        ("Grand Mausoleum", 2, 1), ("大陵墓", 2, 1),
        ("Gauntlet of Shar", 2, 1), ("莎尔的试炼", 2, 1),
        ("Moonrise Towers", 2, 1), ("月出之塔", 2, 1),
        # act 2 但属身份反转核心
        ("Nightsong", 2, 3), ("夜之歌", 2, 3),
        # act 3：地点/区域名
        ("Lower City", 3, 1), ("下城区", 3, 1),
        ("Rivington", 3, 1), ("利文顿", 3, 1),
        # act 3：拉斐尔线地名（2026-11-22 铁手套两轮点踩时词表全漏，事后统计抓不到）
        ("House of Hope", 3, 1), ("希望之邸", 3, 1),
        ("Ramazith's Tower", 3, 1), ("拉玛吉斯之塔", 3, 1),
        ("Sorcerous Sundries", 3, 1), ("法术杂货铺", 3, 1),
        # act 3：终局设定与反派势力，知道名字等于提前拆一层
        ("Netherbrain", 3, 2), ("主脑", 3, 2),
        ("Bhaal", 3, 2), ("巴尔", 3, 2),
    ],
}


def spoiler_hits(game: str | None, answer: str) -> list[dict]:
    """返回答案里命中的后期内容（term + act + level），没命中就空列表。纯函数，可单测。"""
    terms = SPOILER_TERMS.get(game or "", ())
    if not terms or not answer:
        return []
    low = answer.lower()
    return [{"term": t, "act": a, "level": lv}
            for t, a, lv in terms if t.lower() in low]


def spoiler_over(hits: list[dict], max_level: int = 1) -> list[dict]:
    """从命中里筛出超过本轮允许等级的部分（默认只放过 L1）。纯函数，可单测。"""
    return [h for h in hits or [] if int(h.get("level", 1)) > int(max_level)]


def turn_record(*, ip: str, question: str, elapsed: float, meta: dict,
                streamed: bool = False, chunks: int | None = None,
                answer: str | None = None,
                history_msgs: int = 0, hist_game: str | None = None,
                turn_id: str | None = None,
                session_id: str | None = None,
                client_id: str | None = None) -> dict:
    """把一轮请求的关键指标集中成 dict，方便 grep / 后续落库。

    纯函数，不碰 I/O，便于单测。字段名与后续 SQLite turns 表对齐。
    answer 只存前 200 字 + 剧透命中项：够用来复盘越界，不至于把整段答案写进日志。
    """
    meta = meta or {}
    record = {
        "ts": int(time.time()),
        "turn_id": turn_id,
        "ip": ip,
        "stream": streamed,
        "question": question[:120],
        "elapsed_ms": int(elapsed * 1000),
        "route": meta.get("route"),
        "game": meta.get("game"),
        "tokens_in": int(meta.get("tokens_in") or 0),
        "tokens_out": int(meta.get("tokens_out") or 0),
        "tool_calls": int(meta.get("tool_calls") or 0),
    }
    if chunks is not None:
        record["stream_chunks"] = chunks
    # 路由诊断：菜单/兜底误判靠这两列定位（history 缺失还是中断）
    record["history_msgs"] = history_msgs
    record["hist_game"] = hist_game
    # 会话标识（I-23）：jsonl 与 SQLite turns 靠 turn_id 对齐，靠这两列回查会话
    if session_id:
        record["session_id"] = session_id
    if client_id:
        record["client_id"] = client_id
    # 上下文裁剪观测（CONTEXT_BUDGET_PLAN §3.3）：判 I-19 max_turns 要不要放宽的唯一依据。
    # meta 里没有就不写，保持旧调用方的记录形状不变。
    for key in ("history_raw_msgs", "history_used_msgs", "history_trim_chars"):
        val = meta.get(key)
        if val is not None:
            record[key] = int(val)
    # 进度与条目 act（I-14 / I-10 C）：由 graph 侧填 meta，这里只做透传
    if meta.get("progress_level") is not None:
        record["progress_level"] = int(meta["progress_level"])
        record["progress_source"] = meta.get("progress_source")
    entry_acts = meta.get("entry_acts")
    if entry_acts:
        record["entry_acts"] = sorted({int(a) for a in entry_acts})[:10]
    if answer:
        record["answer_head"] = " ".join(answer.split())[:200]
        hits = spoiler_hits(meta.get("game"), answer)
        if hits:
            record["spoiler_hits"] = hits
            worst = max(int(h.get("level", 1)) for h in hits)
            if worst >= 3:
                record["spoiler_worst"] = worst   # 日报按这个列优先抽检
    if meta.get("error"):
        record["error"] = str(meta["error"])[:200]
    return record


def log_turn(record: dict):
    """一轮记录双重落盘：logger（便于 tail/排查）+ 按日 JSONL（重启不丢、可聚合）。

    历史上日志是 nohup `>` 覆盖写的，每次重启就清空，导致多日用量/剧透审计无从回溯。
    JSONL 追加写是权威副本，api_server.log 只当现场输出。
    """
    logger.info("turn %s", json.dumps(record, ensure_ascii=False))
    try:
        TURNS_DIR.mkdir(parents=True, exist_ok=True)
        ts = record.get("ts") or time.time()
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        with open(TURNS_DIR / f"{day}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("turn 落盘失败: %s", e)


# ====== 用户反馈（点赞/点踩）======
# 前端在每条回答下提交 rating：1=赞，-1=踩。落盘只存 turn_id 与少量上下文，
# 便于事后 join 到 data/turns 里那一轮完整记录（问题、路由、工具调用次数）。

def feedback_record(*, turn_id: str, rating: int, ip: str,
                    question: str | None = None, game: str | None = None,
                    route: str | None = None, comment: str | None = None) -> dict:
    """组装一条反馈记录。纯函数，便于单测。"""
    return {
        "ts": int(time.time()),
        "turn_id": turn_id,
        "rating": int(rating),
        "ip": ip,
        "question": (question or "")[:120],
        "game": game,
        "route": route,
        "comment": (comment or "")[:300],
    }


def log_feedback(record: dict):
    """反馈追加到 data/feedback/YYYY-MM-DD.jsonl（重启不丢，可聚合）。"""
    logger.info("feedback %s", json.dumps(record, ensure_ascii=False))
    try:
        FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        ts = record.get("ts") or time.time()
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        with open(FEEDBACK_DIR / f"{day}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("feedback 落盘失败: %s", e)


def _load_feedback(days: int) -> list[dict]:
    """读最近 N 天的反馈记录（按文件日期切，不做解析失败阻断）。"""
    if not FEEDBACK_DIR.exists():
        return []
    records = []
    for f in sorted(FEEDBACK_DIR.glob("*.jsonl"))[-days:]:
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    return records


def feedback_summary(records: list[dict]) -> dict:
    """把一个 turn 的多次提交压成最后一次：用户改主意或重试都不重复计数。"""
    latest: dict[str, dict] = {}
    for r in records:
        tid = r.get("turn_id")
        if tid:
            latest[tid] = r
    deduped = list(latest.values())
    up = sum(1 for r in deduped if r.get("rating") == 1)
    down = sum(1 for r in deduped if r.get("rating") == -1)
    by_game: dict[str, dict] = defaultdict(lambda: {"up": 0, "down": 0})
    by_route: dict[str, dict] = defaultdict(lambda: {"up": 0, "down": 0})
    for r in deduped:
        bucket = "up" if r.get("rating") == 1 else "down"
        by_game[r.get("game") or "unknown"][bucket] += 1
        by_route[r.get("route") or "none"][bucket] += 1
    return {
        "total": len(deduped),
        "up": up,
        "down": down,
        "by_game": dict(sorted(by_game.items(),
                              key=lambda kv: -(kv[1]["up"] + kv[1]["down"]))),
        "by_route": dict(by_route),
        "recent_down": [r for r in deduped if r.get("rating") == -1][-20:],
    }


# ====== API 端点 ======

# ====== 会话（I-23）======

def init_session_store() -> bool:
    """建表并指向当前库。失败只告警：存储不能用也要能回答（legacy 路径）。"""
    try:
        store.set_db_path(SESSION_DB)
        store.init_db()
        return True
    except store.StoreError:
        logger.warning("会话库初始化失败，本轮起走 legacy（前端 history）", exc_info=True)
        return False


def resolve_session(body: dict) -> tuple[str | None, str | None]:
    """解析本轮身份，返回 (client_id, session_id)。

    规则（docs/SESSION_STORE_IMPL.md §4.1）：
      1. session_id 存在且 client 对得上 → 直接用（刷新续聊）
      2. session_id 过期/对不上 → 新建，响应里回传新 id（前端无感）
      3. 没有 session_id → 新建并挂在这个 client 下
      4. 两个都没有 → 服务端生成 client_id
      5. 存储不可用 → (None, None)，调用方回退到旧行为
    """
    raw_cid = str(body.get("client_id") or "").strip()
    raw_sid = str(body.get("session_id") or "").strip()
    cid, sid = raw_cid or None, raw_sid or None
    try:
        if not cid:
            cid = uuid.uuid4().hex
        if sid:
            row = store.get_session(sid)
            if row is not None and (not raw_cid or row["client_id"] == cid):
                store.touch_session(sid)
                return cid, sid
            if row is not None:
                logger.warning("session %s 属于另一个 client，拒绝复用", sid[:8])
        sid = uuid.uuid4().hex
        store.ensure_session(session_id=sid, client_id=cid)
        return cid, sid
    except store.StoreError:
        logger.warning("会话存储不可用，本轮走 legacy", exc_info=True)
        return None, None


def assemble_history(session_id: str | None, fallback):
    """SERVER_HISTORY=1 时用服务端历史覆盖请求体 history。

    装配失败（库坏了/会话被清）退回 fallback，保证问答不因存储故障挂掉。
    """
    if not SERVER_HISTORY or not session_id:
        return fallback or []
    try:
        return store.session_turns(session_id, limit_turns=SESSION_MAX_TURNS,
                                   max_chars=SESSION_MAX_CHARS)
    except store.StoreError:
        logger.warning("装配服务端历史失败，退回前端 history", exc_info=True)
        return fallback or []


def save_turn(session_id: str | None, turn_id: str, question: str,
              answer: str | None, meta: dict, elapsed_ms: int,
              partial: bool = False) -> None:
    """把一轮落进 SQLite。菜单/未识别轮由调用方跳过（不污染历史）。"""
    if not session_id:
        return
    meta = meta or {}
    try:
        store.append_turn(
            session_id=session_id, turn_id=turn_id, question=question, answer=answer,
            route=meta.get("route"), game=meta.get("game"),
            progress_level=meta.get("progress_level"),
            progress_source=meta.get("progress_source"),
            tokens_in=int(meta.get("tokens_in") or 0),
            tokens_out=int(meta.get("tokens_out") or 0),
            tool_calls=int(meta.get("tool_calls") or 0),
            elapsed_ms=elapsed_ms, answer_partial=partial,
        )
    except store.StoreError:
        logger.warning("turn 落库失败（不影响回答）: turn_id=%s", turn_id, exc_info=True)


async def cleanup_loop():
    """每小时跑一次留存清理（答案 3 天 / 存根·会话 90 天，见 env）。"""
    while True:
        try:
            stats = await asyncio.to_thread(
                store.purge,
                answer_days=RETENTION_ANSWER_DAYS,
                receipt_days=RETENTION_RECEIPT_DAYS,
                session_days=RETENTION_SESSION_DAYS,
            )
            if any(stats.values()):
                logger.info("session purge %s", stats)
        except store.StoreError:
            logger.warning("session purge 失败", exc_info=True)
        await asyncio.sleep(3600)


async def handle_sessions(request):
    """GET /api/sessions?client_id=xxx&limit=10 — 最近会话列表（前端恢复入口）"""
    cid = (request.query.get("client_id") or "").strip()
    if not cid:
        return web.json_response({"sessions": []})
    try:
        limit = min(max(int(request.query.get("limit", 10)), 1), 50)
    except ValueError:
        limit = 10
    try:
        rows = store.list_sessions(cid, limit=limit)
    except store.StoreError:
        return web.json_response({"sessions": [], "degraded": True})
    return web.json_response({"sessions": rows})


async def handle_session_detail(request):
    """GET /api/sessions/{session_id}?limit=50 — 轮次列表（刷新后渲染历史）"""
    sid = request.match_info["session_id"]
    try:
        limit = min(max(int(request.query.get("limit", 50)), 1), 200)
    except ValueError:
        limit = 50
    try:
        row = store.get_session(sid)
        if row is None:
            return web.json_response({"error": "session not found"}, status=404)
        turns = store.session_rows(sid, limit=limit)
    except store.StoreError:
        return web.json_response({"error": "session store unavailable"}, status=503)
    return web.json_response({
        "session_id": sid,
        "game": row.get("game"),
        "progress": row.get("progress"),
        "n_turns": row.get("n_turns"),
        "last_active": row.get("last_active"),
        "turns": turns,
    })


async def handle_session_reset(request):
    """POST /api/sessions/{session_id}/reset — 用户明说重开，进度归零"""
    sid = request.match_info["session_id"]
    try:
        if store.get_session(sid) is None:
            return web.json_response({"error": "session not found"}, status=404)
        store.reset_progress(sid)
    except store.StoreError:
        return web.json_response({"error": "session store unavailable"}, status=503)
    return web.json_response({"reset": True})


async def handle_session_delete(request):
    """DELETE /api/sessions/{session_id} — 用户主动删掉一段对话"""
    sid = request.match_info["session_id"]
    try:
        deleted = store.delete_session(sid)
    except store.StoreError:
        return web.json_response({"error": "session store unavailable"}, status=503)
    return web.json_response({"deleted": deleted})


async def handle_login(request):
    """POST /api/login — 已废弃：不再验证密码。

    保留这个端点只是为了让浏览器缓存里的旧版页面不报错：依旧返回一个 token，
    但后续请求不再校验它。新页面已不再调用本接口。
    """
    return web.json_response({"token": "public", "expires_in": 86400})


async def handle_quota(request):
    """GET /api/quota — 今日全局额度状态（前端可用来显示剩余次数）"""
    return web.json_response(quota.status())


async def handle_ask(request):
    """POST /api/ask — 向游戏助手提问（自动识别游戏）"""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "无效的 JSON 格式"}, status=400)

    question = body.get("question", "").strip()
    # 身份与历史：先解析 session，再由服务端装配（SERVER_HISTORY=1）或沿用请求体 history
    client_id, session_id = resolve_session(body)
    history = assemble_history(session_id, body.get("history", []))
    # 只做观测：记录路由器原本能从 history 推出哪款游戏（诊断菜单/兜底误判）
    try:
        hist_game = _game_from_history(history) if history else None
    except Exception:
        hist_game = None

    if not question:
        return web.json_response({"error": "问题不能为空"}, status=400)

    ip = _client_ip(request)
    ok, msg = rate_limiter.check(ip)
    if not ok:
        return web.json_response({"error": msg}, status=429)

    allowed, quota_status = quota.consume()
    if not allowed:
        print(f"  [{time.strftime('%H:%M:%S')}] 🚧 每日额度用尽，拒绝: {ip}")
        return web.json_response({"error": quota_exceeded_message(quota_status), "quota": quota_status}, status=429)

    rate_limiter.record(ip)

    t0 = time.time()
    meta: dict = {}
    turn_id = new_turn_id()
    try:
        answer = await asyncio.wait_for(
            asyncio.to_thread(
                ask, question, history=history if history else None,
                verbose=True, meta=meta,
            ),
            timeout=config["request_timeout"],
        )
        elapsed = time.time() - t0
        # 菜单 / 未识别 / 失败轮没调 LLM，不该占用户额度
        route = meta.get("route") or ""
        if route in NON_LLM_ROUTES:
            quota_status = quota.refund()
        else:
            # 只有真答了才落库：菜单轮进历史只会干扰后续装配
            save_turn(session_id, turn_id, question, answer, meta, int(elapsed * 1000))
        print(f"  [{time.strftime('%H:%M:%S')}] {ip[:15]:15s} | {question[:50]:50s} | {elapsed:.1f}s")
        log_turn(turn_record(ip=ip, question=question, elapsed=elapsed, meta=meta,
                             answer=answer, history_msgs=len(history or []),
                             hist_game=hist_game, turn_id=turn_id,
                             session_id=session_id, client_id=client_id))
        return web.json_response({"answer": answer, "elapsed": round(elapsed, 1),
                                  "quota": quota_status, "turn_id": turn_id,
                                  "session_id": session_id, "client_id": client_id})
    except asyncio.TimeoutError:
        elapsed = time.time() - t0
        quota_status = quota.refund()
        meta.update({"route": "error", "error": "请求处理超时"})
        logger.warning(f"非流式请求超时: {ip}")
        log_turn(turn_record(ip=ip, question=question, elapsed=elapsed, meta=meta,
                             history_msgs=len(history or []), hist_game=hist_game,
                             turn_id=turn_id, session_id=session_id,
                             client_id=client_id))
        return web.json_response(
            {"error": "请求处理超时，请稍后重试", "quota": quota_status},
            status=504,
        )
    except Exception as e:
        quota_status = quota.refund()
        logger.exception("处理失败")
        print(f"  ❌ 处理失败: {e}")
        return web.json_response({"error": f"处理请求时出错"}, status=500)


async def handle_ask_stream(request):
    """POST /api/ask/stream — 流式提问（SSE）"""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "无效的 JSON 格式"}, status=400)

    question = body.get("question", "").strip()
    # 身份与历史：先解析 session，再由服务端装配（SERVER_HISTORY=1）或沿用请求体 history
    client_id, session_id = resolve_session(body)
    history = assemble_history(session_id, body.get("history", []))
    # 只做观测：记录路由器原本能从 history 推出哪款游戏（诊断菜单/兜底误判）
    try:
        hist_game = _game_from_history(history) if history else None
    except Exception:
        hist_game = None

    if not question:
        return web.json_response({"error": "问题不能为空"}, status=400)

    ip = _client_ip(request)
    ok, msg = rate_limiter.check(ip)
    if not ok:
        return web.json_response({"error": msg}, status=429)

    allowed, quota_status = quota.consume()
    if not allowed:
        # 流式接口同样用 429 + JSON：额度用尽时不开 SSE，前端当普通提示渲染
        print(f"  [{time.strftime('%H:%M:%S')}] 🚧 每日额度用尽，拒绝(流式): {ip}")
        return web.json_response({"error": quota_exceeded_message(quota_status), "quota": quota_status}, status=429)

    rate_limiter.record(ip)

    # SSE 响应头
    resp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

    allow_origin = os.environ.get("CORS_ORIGIN", "*")
    resp.headers["Access-Control-Allow-Origin"] = allow_origin
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"

    await resp.prepare(request)

    t0 = time.time()
    turn_id = new_turn_id()
    token_count = 0
    answer_parts: list[str] = []
    meta: dict = {}
    stream_failed = False      # 超时/断连/异常：已产出的半截答案标 partial 落库

    try:
        # 流式以前完全没超时：LLM 卡住会永久占住连接和线程（曾出现 275s 零 chunk）。
        # 整体给一个上限，超了推 error 事件收尾、退回额度。
        async with asyncio.timeout(float(config.get("stream_timeout", 240))):
            async for event_type, data in ask_stream(question, history=history if history else None, verbose=True, meta=meta):
                if event_type == "token":
                    payload = json.dumps({"type": "token", "content": data}, ensure_ascii=False)
                    await resp.write(f"data: {payload}\n\n".encode("utf-8"))
                    token_count += 1
                    answer_parts.append(str(data))
                elif event_type == "meta":
                    payload = json.dumps({"type": "meta", **data}, ensure_ascii=False)
                    await resp.write(f"data: {payload}\n\n".encode("utf-8"))
                elif event_type == "error":
                    payload = json.dumps({"type": "error", "content": data}, ensure_ascii=False)
                    await resp.write(f"data: {payload}\n\n".encode("utf-8"))
                    break

        # 菜单 / 未识别 / 失败轮不计费
        if (meta.get("route") or "") in NON_LLM_ROUTES:
            quota_status = quota.refund()

        # 结束标志
        elapsed = round(time.time() - t0, 1)
        await resp.write(
            f"data: {json.dumps({'type': 'done', 'elapsed': elapsed, 'quota': quota_status, 'turn_id': turn_id, 'session_id': session_id, 'client_id': client_id}, ensure_ascii=False)}\n\n".encode("utf-8")
        )

    except TimeoutError:
        stream_failed = True
        quota_status = quota.refund()
        meta.update({"route": "error", "error": "流式响应超时"})
        logger.warning(f"流式响应超时（>{config.get('stream_timeout')}s）: {ip}")
        try:
            await resp.write(
                f"data: {json.dumps({'type': 'error', 'content': '响应超时，请重试', 'quota': quota_status}, ensure_ascii=False)}\n\n".encode("utf-8")
            )
        except Exception:
            pass
    except (ConnectionResetError, ConnectionAbortedError):
        stream_failed = True
        logger.warning(f"客户端断开连接: {ip}")
    except Exception as e:
        stream_failed = True
        logger.error(f"流式处理失败: {e}")
        try:
            await resp.write(f"data: {json.dumps({'type': 'error', 'content': str(e)[:200]})}\n\n".encode("utf-8"))
        except Exception:
            pass

    elapsed = time.time() - t0
    print(f"  [{time.strftime('%H:%M:%S')}] {ip[:15]:15s} 💨 {question[:40]:40s} | {elapsed:.1f}s | {token_count}t")
    # 落库放在公共出口：正常完成也走这里，超时/断连则标 answer_partial
    answer_text = "".join(answer_parts)
    route = meta.get("route") or ""
    if route not in ("menu", "unknown") and answer_text:
        save_turn(session_id, turn_id, question, answer_text, meta,
                  int(elapsed * 1000), partial=stream_failed)
    log_turn(turn_record(ip=ip, question=question, elapsed=elapsed, meta=meta,
                         streamed=True, chunks=token_count,
                         answer=answer_text or None,
                         history_msgs=len(history or []), hist_game=hist_game,
                         turn_id=turn_id, session_id=session_id,
                         client_id=client_id))
    return resp


async def handle_status(request):
    """GET /api/status — 健康检查"""
    return web.json_response({
        "status": "ok",
        "version": APP_VERSION,
        "uptime": round(time.time() - start_time, 1),
        "quota": quota.status(),
    })


def _load_turns(days: int) -> list[dict]:
    """读 data/turns 里最近 N 天的记录（轻量聚合，不碰模型）。"""
    turns_dir = TURNS_DIR
    files = sorted(turns_dir.glob("*.jsonl"))[-days:]
    records = []
    for f in files:
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return records


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 1)


async def handle_stats(request):
    """GET /api/stats?days=7 — 用量聚合（源：data/turns/*.jsonl）。"""
    try:
        days = max(1, min(90, int(request.query.get("days", "7"))))
    except ValueError:
        days = 7
    records = _load_turns(days)
    by_day: dict[str, int] = {}
    games: dict[str, int] = {}
    routes: dict[str, int] = {}
    elapsed: list[float] = []
    tokens_in = tokens_out = tool_calls = 0
    spoiler = 0
    for r in records:
        day = time.strftime("%Y-%m-%d", time.localtime(r.get("ts") or 0))
        by_day[day] = by_day.get(day, 0) + 1
        games[r.get("game") or "unknown"] = games.get(r.get("game") or "unknown", 0) + 1
        routes[r.get("route") or "none"] = routes.get(r.get("route") or "none", 0) + 1
        elapsed.append((r.get("elapsed_ms") or 0) / 1000)
        tokens_in += int(r.get("tokens_in") or 0)
        tokens_out += int(r.get("tokens_out") or 0)
        tool_calls += int(r.get("tool_calls") or 0)
        if r.get("spoiler_hits"):
            spoiler += 1
    return web.json_response({
        "days": days,
        "turns": len(records),
        "by_day": dict(sorted(by_day.items())),
        "games": dict(sorted(games.items(), key=lambda kv: -kv[1])),
        "routes": dict(sorted(routes.items(), key=lambda kv: -kv[1])),
        "elapsed_s": {"avg": _pct(elapsed, 0.5) if elapsed else 0,
                      "p90": _pct(elapsed, 0.9)},
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tool_calls": tool_calls,
        "spoiler_hit_turns": spoiler,
    })


async def handle_feedback(request):
    """POST /api/feedback — 对某一轮回答点赞（rating=1）或点踩（rating=-1）。

    不校验额度、不计次数，也没有频率限制：这是用户主动标注问题答案的手段，
    拦掉它反而让坏例拿不到。重复提交不报错，统计时按 turn_id 取最后一条。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "无效的 JSON 格式"}, status=400)

    turn_id = str(body.get("turn_id") or "").strip()
    if not TURN_ID_RE.match(turn_id):
        return web.json_response({"error": "turn_id 无效"}, status=400)

    try:
        rating = int(body.get("rating"))
    except (TypeError, ValueError):
        rating = 0
    if rating not in (1, -1):
        return web.json_response({"error": "rating 必须是 1 或 -1"}, status=400)

    record = feedback_record(
        turn_id=turn_id,
        rating=rating,
        ip=_client_ip(request),
        question=body.get("question"),
        game=body.get("game"),
        route=body.get("route"),
        comment=body.get("comment"),
    )
    log_feedback(record)
    print(f"  [{time.strftime('%H:%M:%S')}] {'👍' if rating == 1 else '👎'} {turn_id} | {(record['question'] or '')[:40]}")
    return web.json_response({"ok": True, "turn_id": turn_id, "rating": rating})


async def handle_feedback_stats(request):
    """GET /api/feedback/stats?days=7 — 反馈聚合（赞/踩总数、按游戏、最近负面样本）。"""
    try:
        days = max(1, min(90, int(request.query.get("days", "7"))))
    except ValueError:
        days = 7
    summary = feedback_summary(_load_feedback(days))
    summary["days"] = days
    return web.json_response(summary)


# ====== 应用工厂 ======
def create_app() -> web.Application:
    """创建并配置 web 应用（仅提供 API，不提供页面）。"""
    init_session_store()
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_post("/api/login", handle_login)
    app.router.add_post("/api/ask", handle_ask)
    app.router.add_post("/api/ask/stream", handle_ask_stream)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/quota", handle_quota)
    app.router.add_get("/api/stats", handle_stats)
    app.router.add_post("/api/feedback", handle_feedback)
    app.router.add_get("/api/feedback/stats", handle_feedback_stats)
    # 会话（I-23）：刷新后恢复历史 / 最近对话 / 重开进度 / 删对话
    app.router.add_get("/api/sessions", handle_sessions)
    app.router.add_get("/api/sessions/{session_id}", handle_session_detail)
    app.router.add_post("/api/sessions/{session_id}/reset", handle_session_reset)
    app.router.add_delete("/api/sessions/{session_id}", handle_session_delete)

    async def _start_cleanup(app):
        # 留存清理：起一份后台任务，每小时跑一次（首轮立即跑）
        app[CLEANUP_TASK_KEY] = asyncio.create_task(cleanup_loop())

    async def _stop_cleanup(app):
        task = app.get(CLEANUP_TASK_KEY)
        if task is not None:
            task.cancel()

    app.on_startup.append(_start_cleanup)
    app.on_cleanup.append(_stop_cleanup)
    return app


# ====== 启动 ======
start_time = time.time()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(f"")
    print(f"  ╔══════════════════════════════════╗")
    print(f"  ║  🐈  多游戏 Agent API            ║")
    print(f"  ╚══════════════════════════════════╝")
    print(f"")
    print(f"  🔑 无密码访问，全局每日额度 {config['daily_quota']} 问")
    print(f"  🚦 速率限制: {config['rate_limit_per_min']}/分钟, {config['rate_limit_per_day']}/天")
    print(f"  🌐 监听: http://{config['host']}:{config['port']}")
    print(f"")

    app = create_app()
    web.run_app(app, host=config["host"], port=config["port"])
