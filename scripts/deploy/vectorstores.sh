#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# vectorstores.sh — 将 Mac 本地构建的向量库 scp 到服务器
#
# 用法:
#   bash scripts/deploy/vectorstores.sh [全部|游戏名...]
#
# 例子:
#   bash scripts/deploy/vectorstores.sh                   ← 全部游戏
#   bash scripts/deploy/vectorstores.sh hollow_knight     ← 只传空洞骑士
#   bash scripts/deploy/vectorstores.sh mhw silksong      ← 只传 MHW + 丝之歌
#
# SSH 密码:
#   全程只输一次密码（使用 SSH ControlMaster 复用连接）
#
# 前提:
#   1. 先在本地 git pull 并跑完 ingest:
#        python3 scripts/tool/ingest_game.py --game all
#   2. 确认下面 SERVER 变量正确
#   3. 传完后 SSH 到服务器用 scripts/deploy/api.sh 重启 API
# ─────────────────────────────────────────────────────────────

set -euo pipefail

SERVER="snap@114.132.189.56"
REMOTE_ROOT="/data/learning/agent"

# 从 Python 注册表动态获取游戏列表（单源真理）
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GAMES=($(python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/src')
from rag_agent.game_registry import GAME_KEYS
print(' '.join(GAME_KEYS))
"))

# ── 解析参数 ──
if [ $# -eq 0 ]; then
    TARGETS=("${GAMES[@]}")
else
    TARGETS=("$@")
fi

# ── SSH 控制套接字（复用连接，只输一次密码） ──
SOCKET="/tmp/deploy_vs_${USER}_$$"

# 退出时自动关闭主连接
cleanup() {
    ssh -S "${SOCKET}" -O exit "${SERVER}" 2>/dev/null || true
    rm -f "${SOCKET}"
}
trap cleanup EXIT

echo "============================================"
echo "  部署向量库 → ${SERVER}"
echo "============================================"
echo ""

# ── 建立 SSH 主连接（要输一次密码） ──
echo "🔐 建立 SSH 持久连接（只需输一次密码）..."
ssh -M -S "${SOCKET}" -o ControlPersist=yes "${SERVER}" "echo '  ✅ 连接成功'"

echo ""
echo "📤 开始传输..."
echo ""

for game in "${TARGETS[@]}"; do
    src="games/${game}/vectorstore"
    dst="${SERVER}:${REMOTE_ROOT}/games/${game}/vectorstore"

    if [ ! -d "${src}" ]; then
        echo "⚠️  SKIP: ${src} 不存在，请先运行 ingest"
        continue
    fi

    if [ ! -f "${src}/index.faiss" ]; then
        echo "⚠️  SKIP: ${src} 没有 index.faiss（向量库尚未构建）"
        continue
    fi

    size_kb=$(du -sk "${src}" | cut -f1)
    echo "📤  [${game}] ${size_kb} KB"

    # 复用 SSH 主连接
    ssh -S "${SOCKET}" "${SERVER}" "mkdir -p ${REMOTE_ROOT}/games/${game}"
    scp -o ControlPath="${SOCKET}" -rC "${src}" "${dst%/*}"

    echo "   ✅ ${game} 完成"
    echo ""
done

echo ""
echo "===== 全部完成 ====="
echo ""
echo "下一步 — SSH 到服务器重启 API:"
echo "  ssh ${SERVER}"
echo "  cd ${REMOTE_ROOT}"
echo "  git pull"
echo "  bash scripts/deploy/api.sh"
echo ""
