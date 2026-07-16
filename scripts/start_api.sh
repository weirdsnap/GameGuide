#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# start_api.sh — 服务器端启动 API Server
#
# 用法:
#   bash scripts/start_api.sh
#
# 功能:
#   1. 关停旧的 api_server 进程（如果有）
#   2. 等待端口释放
#   3. 启动新的 API Server（后台运行）
#   4. 检查启动状态
#
# 注意:
#   在 Mac 上传完向量库后，SSH 到服务器运行此脚本
# ─────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-8765}"
LOG_FILE="${LOG_FILE:-${SCRIPT_DIR}/api_server.log}"
PID_FILE="${SCRIPT_DIR}/api_server.pid"
PYTHON="${SCRIPT_DIR}/.venv/bin/python"

echo "============================================"
echo "  🐈 Agent API Server 启动脚本"
echo "============================================"

# ── 1. 关停旧进程 ──
echo ""
echo "🔍 检查旧进程..."

# 方式 A: 通过 PID 文件
OLD_PID=""
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "  ⚙️ 发现旧进程 PID=${OLD_PID}，正在关停..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
        # 如果还没死，强制 kill
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "  ⚙️ 强制终止..."
            kill -9 "$OLD_PID" 2>/dev/null || true
        fi
    fi
    rm -f "$PID_FILE"
fi

# 方式 B: 搜索 api_server.py 进程
OLD_PIDS=$(pgrep -f "api_server.py" || true)
if [ -n "$OLD_PIDS" ] && [ "$OLD_PIDS" != "$OLD_PID" ]; then
    echo "  ⚙️ 发现额外进程: ${OLD_PIDS}，正在关停..."
    for pid in $OLD_PIDS; do
        # 排除 grep 和当前的 shell
        if [ "$pid" != "$OLD_PID" ] && [ "$pid" != "$$" ]; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    sleep 1
    # 补刀
    REMAINING=$(pgrep -f "api_server.py" || true)
    if [ -n "$REMAINING" ]; then
        echo "  ⚙️ 强制终止残留进程: ${REMAINING}..."
        kill -9 $REMAINING 2>/dev/null || true
        sleep 1
    fi
fi

# ── 2. 检查端口 ──
echo ""
echo "🔍 检查端口 ${PORT}..."
if command -v ss &>/dev/null; then
    if ss -tlnp | grep -q ":${PORT} "; then
        echo "  ⚠️  端口 ${PORT} 仍被占用，等待释放..."
        for i in 1 2 3 4 5; do
            sleep 1
            if ! ss -tlnp | grep -q ":${PORT} "; then
                echo "  ✅ 端口已释放"
                break
            fi
            if [ "$i" -eq 5 ]; then
                echo "  ❌ 端口 ${PORT} 无法释放！"
                echo "  请手动检查: fuser -k ${PORT}/tcp"
                exit 1
            fi
        done
    else
        echo "  ✅ 端口 ${PORT} 空闲"
    fi
elif command -v fuser &>/dev/null; then
    if fuser "${PORT}/tcp" &>/dev/null; then
        echo "  ⚠️  端口 ${PORT} 被占用，正在释放..."
        fuser -k "${PORT}/tcp" 2>/dev/null || true
        sleep 1
    else
        echo "  ✅ 端口 ${PORT} 空闲"
    fi
fi

# ── 3. 启动新进程 ──
echo ""
echo "🚀 启动 API Server..."
echo "  Python: ${PYTHON}"
echo "  脚本:   api_server.py"
echo "  端口:   ${PORT}"
echo "  日志:   ${LOG_FILE}"
echo ""

nohup "${PYTHON}" api_server.py > "${LOG_FILE}" 2>&1 &

NEW_PID=$!
echo "${NEW_PID}" > "${PID_FILE}"

# ── 4. 检查启动状态 ──
sleep 3
if kill -0 "${NEW_PID}" 2>/dev/null; then
    echo "✅ 启动成功！PID = ${NEW_PID}"
    echo ""
    echo "  状态: $(ps -o pid,etime,%mem,%cpu -p ${NEW_PID} --no-headers 2>/dev/null || echo '运行中')"
    echo "  日志: tail -f ${LOG_FILE}"
    echo ""
    echo "  Caddy 反代: https://api.weirdsnap.top:8443"
    echo ""
    echo "  测试:"
    echo "    curl -X POST http://localhost:${PORT}/api/login \\"
    echo "      -H 'Content-Type: application/json' \\"
    echo "      -d '{\"password\": \"$(jq -r '.password' api_config.json 2>/dev/null || echo 'xxx')\"}'"
else
    echo "❌ 启动失败！请查看日志:"
    echo "   tail -50 ${LOG_FILE}"
    exit 1
fi
