#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# blog_check.sh — 博客上报端点的一次性验收（服务器上跑）
#
# 用法:
#   bash scripts/deploy/blog_check.sh
#
# 四步：上报 → 看库明细 → 看聚合 → 验证代理头被挡。
# 只写一条 path=/post/test 的测试记录（UA 是 curl，会被标 flagged=1，
# 聚合里默认剔除，所以 pv 应该是 0、flagged 应该是 1）。
# ─────────────────────────────────────────────────────────────

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$SCRIPT_DIR"
BASE="${BASE:-http://127.0.0.1:8765}"

echo "1) POST /api/hit（期望 204）"
curl -sS -o /dev/null -w '   HTTP %{http_code}\n' -X POST \
    -H 'Content-Type: text/plain' \
    -d '{"p":"/post/test","k":"post","w":390}' "$BASE/api/hit"

echo "2) data/blog.db 明细（期望 /post/test post mobile 1）"
.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
p = Path("data/blog.db")
if not p.is_file():
    print("   ❌ data/blog.db 不存在：上报没写进去")
else:
    rows = sqlite3.connect(p).execute(
        "select day, path, kind, device, flagged from blog_hits order by id desc limit 5"
    ).fetchall()
    for r in rows:
        print("   ", " | ".join(str(x) for x in r))
PY

echo "3) GET /api/dashboard/blog?days=7（期望 pv=0、flagged≥1、series 七格）"
curl -sS "$BASE/api/dashboard/blog?days=7"
echo

echo "4) 带 X-Forwarded-For 的同一请求（期望 404）"
curl -sS -o /dev/null -w '   HTTP %{http_code}\n' \
    -H 'X-Forwarded-For: 1.2.3.4' "$BASE/api/dashboard/blog?days=7"
