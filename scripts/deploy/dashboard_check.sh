#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# dashboard_check.sh — 看板端点一条命令体检（服务器上跑）
#
# 用法:
#   bash scripts/deploy/dashboard_check.sh
#
# 检查：页面可开、系统资源端点、summary 带 system 块、代理头被挡。
# ─────────────────────────────────────────────────────────────

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$SCRIPT_DIR"
BASE="${BASE:-http://127.0.0.1:8765}"
PY="${SCRIPT_DIR}/.venv/bin/python"
TMP="$(mktemp)"

echo "1) GET /dashboard（期望 200，页面含「系统资源」）"
curl -sS -o "$TMP" -w '   HTTP %{http_code}\n' "$BASE/dashboard"
if grep -q '系统资源' "$TMP"; then echo "   页面含「系统资源」块 ✅"; else echo "   ⚠️  页面里没找到「系统资源」"; fi

echo "2) GET /api/dashboard/system?hours=24"
curl -sS "$BASE/api/dashboard/system?hours=24" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
n = d["now"]
print("   采样点 %d 个 · 间隔 %ds" % (d["points"], d["interval_s"]))
print("   内存可用 %sM / 共 %sM · swap 已用 %sM / 共 %sM"
      % (n["mem_avail_mb"], n["mem_total_mb"], n["swap_used_mb"], n["swap_total_mb"]))
print("   load %s/%s/%s · cpu %s%% · 磁盘可用 %sM · RSS %sM · 句柄 %s"
      % (n["load1"], n["load5"], n["load15"], n["cpu_pct"],
         n["disk_free_mb"], n["rss_mb"], n["fds"]))
print("   判定:", d["health"])
'

echo "3) GET /api/dashboard/summary?days=7（应带 system 块）"
curl -sS "$BASE/api/dashboard/summary?days=7" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
s = d["summary"]
print("   轮次 %s · 失败率 %.1f%% · 耗时 p90 %ss"
      % (s["turns"], s["fail_rate"] * 100, s["elapsed_s"]["p90"]))
print("   system 块:", "有" if d.get("system") else "缺")
'

echo "4) 带 X-Forwarded-For 的 system 请求（期望 404）"
curl -sS -o /dev/null -w '   HTTP %{http_code}\n' \
    -H 'X-Forwarded-For: 1.2.3.4' "$BASE/api/dashboard/system?hours=24"

rm -f "$TMP"
