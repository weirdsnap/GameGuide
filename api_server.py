"""
Hollow Knight RAG Agent — API Server
提供密码保护的 API 接口，供博客前端调用。
"""

import json
import os
import time
import sys
from pathlib import Path
from collections import defaultdict

from aiohttp import web

# 添加 src 到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))

from rag_agent.agent import ask
from rag_agent.vectorstore import load_vectorstore

# ====== 配置 ======
CONFIG_FILE = project_root / "api_config.json"

DEFAULT_CONFIG = {
    "password": "hollowknight2024",
    "rate_limit_per_min": 20,
    "rate_limit_per_day": 200,
    "port": 8765,
    "host": "0.0.0.0"
}


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
            return {**DEFAULT_CONFIG, **cfg}
    # 首次运行自动写入默认配置
    with open(CONFIG_FILE, "w") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
    print(f"  📝 已生成默认配置: {CONFIG_FILE}")
    print(f"  ⚠️  请修改 password 字段！")
    return dict(DEFAULT_CONFIG)


config = load_config()

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


# ====== 预加载 ======
print("  ⏳ 正在预加载向量库...")
try:
    t0 = time.time()
    load_vectorstore()
    print(f"  ✅ 向量库加载完成 ({time.time()-t0:.1f}s)")
except Exception as e:
    print(f"  ⚠️  向量库预加载失败: {e}")
    print(f"     服务仍可启动，但首次请求会较慢")


# ====== API 端点 ======
async def handle_ask(request):
    """POST /api/ask — 向空洞骑士助手提问"""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "无效的 JSON 格式"}, status=400)

    question = body.get("question", "").strip()
    password = body.get("password", "")
    history = body.get("history", [])

    if not question:
        return web.json_response({"error": "问题不能为空"}, status=400)

    if password != config["password"]:
        return web.json_response({"error": "密码错误"}, status=403)

    ip = request.remote or "unknown"
    ok, msg = rate_limiter.check(ip)
    if not ok:
        return web.json_response({"error": msg}, status=429)

    rate_limiter.record(ip)

    try:
        t0 = time.time()
        answer = ask(question, history=history if history else None, verbose=False)
        elapsed = time.time() - t0
        print(f"  [{time.strftime('%H:%M:%S')}] {ip[:15]:15s} | {question[:50]:50s} | {elapsed:.1f}s")
        return web.json_response({"answer": answer, "elapsed": round(elapsed, 1)})
    except Exception as e:
        print(f"  ❌ 处理失败: {e}")
        return web.json_response({"error": f"处理请求时出错"}, status=500)


async def handle_status(request):
    """GET /api/status — 健康检查"""
    return web.json_response({
        "status": "ok",
        "version": "beta3",
        "uptime": round(time.time() - start_time, 1),
    })


async def handle_chat(request):
    """GET /chat — 聊天页面"""
    chat_html_path = Path("/data/learning/weirdsnap.github.io/htmls/hollow_knight.html")
    if not chat_html_path.exists():
        return web.Response(text="页面未找到", status=404)
    html = chat_html_path.read_text(encoding="utf-8")
    # 将 API 地址替换为当前服务器地址
    html = html.replace("const API_URL = 'https://snap.api.weirdsnap.com/api';",
                        f"const API_URL = '/api';")
    return web.Response(text=html, content_type="text/html", charset="utf-8")


async def handle_redirect(request):
    """GET / — 重定向到聊天页面"""
    raise web.HTTPFound("/chat")


# ====== 应用工厂 ======
def create_app() -> web.Application:
    """创建并配置 web 应用。"""
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_post("/api/ask", handle_ask)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/chat", handle_chat)
    app.router.add_get("/", handle_redirect)
    return app


# ====== 启动 ======
start_time = time.time()

if __name__ == "__main__":
    print(f"")
    print(f"  ╔══════════════════════════════════╗")
    print(f"  ║  🐈  Hollow Knight RAG Agent API ║")
    print(f"  ╚══════════════════════════════════╝")
    print(f"")
    print(f"  🔑 密码验证已启用")
    print(f"  🚦 速率限制: {config['rate_limit_per_min']}/分钟, {config['rate_limit_per_day']}/天")
    print(f"  🌐 监听: http://{config['host']}:{config['port']}")
    print(f"")

    app = create_app()
    web.run_app(app, host=config["host"], port=config["port"])
