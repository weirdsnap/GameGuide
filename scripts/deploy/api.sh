#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# api.sh — 服务器端启动 API Server
#
# 用法:
#   bash scripts/deploy/api.sh
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

SCRIPT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-8765}"
LOG_FILE="${LOG_FILE:-${SCRIPT_DIR}/api_server.log}"
PID_FILE="${SCRIPT_DIR}/api_server.pid"
PYTHON="${SCRIPT_DIR}/.venv/bin/python"

# fastembed 默认把 ONNX 模型下到 $TMPDIR/fastembed_cache，重启机器 / 清 /tmp 就没了，
# 之后每次检索都报模型加载失败（2026-11-12 踩过）。固定到仓库内持久目录。
# PYTHONUNBUFFERED：默认 stdout 块缓冲，日志会揣在内存里不落盘。
export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-${SCRIPT_DIR}/.cache/fastembed}"
export PYTHONUNBUFFERED=1

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
# 方式 B: 端口占用兜底（不用 pgrep -f）
# 旧写法 `pgrep -f api_server.py` 会匹配任意命令行里带这串的进程（同事的 tail、
# 我的 exec 包装 shell 都被误杀过），还配 kill -9，破坏面太大。改成只看 8765 端口
# 到底被哪个 pid 占着，只动它。
PORT_PIDS=""
if command -v ss &>/dev/null; then
    PORT_PIDS=$(ss -ltnp 2>/dev/null | awk -v p=":${PORT}" '$4 ~ p"$"' | grep -oP 'pid=\K[0-9]+' | sort -u || true)
fi
for pid in $PORT_PIDS; do
    if [ "$pid" != "$OLD_PID" ] && [ "$pid" != "$$" ]; then
        echo "  ⚙️ 端口 ${PORT} 被 PID=${pid} 占用，关停..."
        kill "$pid" 2>/dev/null || true
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            echo "  ⚙️ 仍未退出，强制终止 PID=${pid}..."
            kill -9 "$pid" 2>/dev/null || true
            sleep 1
        fi
    fi
done

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

# 上一个进程的日志超过 5MB 就留一份 .prev，避免每轮重启前的现场全丢
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    mv -f "$LOG_FILE" "${LOG_FILE}.prev" 2>/dev/null || true
    echo "  📦 旧日志已转为 ${LOG_FILE}.prev"
fi
echo "  Python: ${PYTHON}"
echo "  脚本:   api_server.py"
echo "  端口:   ${PORT}"
echo "  日志:   ${LOG_FILE}"
echo "  模型缓存: ${FASTEMBED_CACHE_PATH}"
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
    echo "  Caddy 反代: https://api.weirdsnap.top"
    echo ""
    echo "  测试:"
    echo "    curl http://localhost:${PORT}/api/status"
    echo "    curl http://localhost:${PORT}/api/quota"
else
    echo "❌ 启动失败！请查看日志:"
    echo "   tail -50 ${LOG_FILE}"
    exit 1
fi
