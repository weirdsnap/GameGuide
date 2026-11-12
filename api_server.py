"""
多游戏 Agent API Server
提供给博客前端调用的 API 接口（无密码、全局每日额度控制）。
自动识别问题所属游戏，路由到正确的 Agent 和知识库。
"""

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
import sys
from pathlib import Path
from collections import defaultdict

from aiohttp import web

# 添加 src 到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))

from rag_agent.multi_agent import ask, ask_stream

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
TURNS_DIR = project_root / "data" / "turns"


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
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
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
# docs/SPOILER_CONTROL_PLAN.md。条目里的 act = 该内容最早出现的幕数（对照用户进度用）。
SPOILER_TERMS: dict[str, list[tuple[str, int]]] = {
    "baldurs_gate3": [
        ("Mason's Guild", 2), ("石匠工会", 2),
        ("Grand Mausoleum", 2), ("大陵墓", 2),
        ("Gauntlet of Shar", 2), ("莎尔的试炼", 2),
        ("Moonrise Towers", 2), ("月出之塔", 2),
        ("Nightsong", 2), ("夜之歌", 2),
        ("Lower City", 3), ("下城区", 3),
        ("Rivington", 3), ("利文顿", 3),
        ("Netherbrain", 3), ("主脑", 3),
        ("Bhaal", 3), ("巴尔", 3),
    ],
}


def spoiler_hits(game: str | None, answer: str) -> list[dict]:
    """返回答案里命中的后期内容（term + act），没命中就空列表。纯函数，可单测。"""
    terms = SPOILER_TERMS.get(game or "", ())
    if not terms or not answer:
        return []
    low = answer.lower()
    return [{"term": t, "act": a} for t, a in terms if t.lower() in low]


def turn_record(*, ip: str, question: str, elapsed: float, meta: dict,
                streamed: bool = False, chunks: int | None = None,
                answer: str | None = None) -> dict:
    """把一轮请求的关键指标集中成 dict，方便 grep / 后续落库。

    纯函数，不碰 I/O，便于单测。字段名与后续 SQLite turns 表对齐。
    answer 只存前 200 字 + 剧透命中项：够用来复盘越界，不至于把整段答案写进日志。
    """
    meta = meta or {}
    record = {
        "ts": int(time.time()),
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
    if answer:
        record["answer_head"] = " ".join(answer.split())[:200]
        hits = spoiler_hits(meta.get("game"), answer)
        if hits:
            record["spoiler_hits"] = hits
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


# ====== API 端点 ======

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
    history = body.get("history", [])

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
        if (meta.get("route") or "") in NON_LLM_ROUTES:
            quota_status = quota.refund()
        print(f"  [{time.strftime('%H:%M:%S')}] {ip[:15]:15s} | {question[:50]:50s} | {elapsed:.1f}s")
        log_turn(turn_record(ip=ip, question=question, elapsed=elapsed, meta=meta,
                             answer=answer))
        return web.json_response({"answer": answer, "elapsed": round(elapsed, 1),
                                  "quota": quota_status})
    except asyncio.TimeoutError:
        elapsed = time.time() - t0
        quota_status = quota.refund()
        meta.update({"route": "error", "error": "请求处理超时"})
        logger.warning(f"非流式请求超时: {ip}")
        log_turn(turn_record(ip=ip, question=question, elapsed=elapsed, meta=meta))
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
    history = body.get("history", [])

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
    token_count = 0
    answer_parts: list[str] = []
    meta: dict = {}

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
            f"data: {json.dumps({'type': 'done', 'elapsed': elapsed, 'quota': quota_status}, ensure_ascii=False)}\n\n".encode("utf-8")
        )

    except TimeoutError:
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
        logger.warning(f"客户端断开连接: {ip}")
    except Exception as e:
        logger.error(f"流式处理失败: {e}")
        try:
            await resp.write(f"data: {json.dumps({'type': 'error', 'content': str(e)[:200]})}\n\n".encode("utf-8"))
        except Exception:
            pass

    elapsed = time.time() - t0
    print(f"  [{time.strftime('%H:%M:%S')}] {ip[:15]:15s} 💨 {question[:40]:40s} | {elapsed:.1f}s | {token_count}t")
    log_turn(turn_record(ip=ip, question=question, elapsed=elapsed, meta=meta,
                         streamed=True, chunks=token_count,
                         answer="".join(answer_parts) if answer_parts else None))
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


# ====== 应用工厂 ======
def create_app() -> web.Application:
    """创建并配置 web 应用（仅提供 API，不提供页面）。"""
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_post("/api/login", handle_login)
    app.router.add_post("/api/ask", handle_ask)
    app.router.add_post("/api/ask/stream", handle_ask_stream)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/quota", handle_quota)
    app.router.add_get("/api/stats", handle_stats)
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
