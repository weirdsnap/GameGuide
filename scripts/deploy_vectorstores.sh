#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# deploy_vectorstores.sh — 将 Mac 本地构建的向量库 scp 到服务器
#
# 用法:
#   bash scripts/deploy_vectorstores.sh [全部|游戏名...]
#
# 例子:
#   bash scripts/deploy_vectorstores.sh                   ← 全部 7 个游戏
#   bash scripts/deploy_vectorstores.sh hollow_knight     ← 只传空洞骑士
#   bash scripts/deploy_vectorstores.sh mhw silksong      ← 只传 MHW + 丝之歌
#
# 前提:
#   1. 先在 Mac 上 git pull 并跑完 ingest:
#        python3 scripts/ingest_game.py --game all
#   2. 确认下面 SERVER 变量正确
#   3. 传完后 SSH 到服务器重启 API
# ─────────────────────────────────────────────────────────────

set -euo pipefail

SERVER="snap@114.132.189.56"
REMOTE_ROOT="/data/learning/agent"

GAMES=(
    hollow_knight
    oni
    terraria
    cyberpunk2077
    va11halla
    mhw
    silksong
)

# ── 解析参数 ──
if [ $# -eq 0 ]; then
    TARGETS=("${GAMES[@]}")
else
    TARGETS=("$@")
fi

echo "============================================"
echo "  部署向量库 → ${SERVER}"
echo "============================================"
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

    # 先确保远程目录存在，再 scp
    ssh "${SERVER}" "mkdir -p ${REMOTE_ROOT}/games/${game}"
    scp -rC "${src}" "${dst%/*}"

    echo "   ✅ ${game} 完成"
    echo ""
done

echo ""
echo "===== 全部完成 ====="
echo "请 SSH 到服务器重启 API:"
echo "  ssh ${SERVER}"
echo "  cd ${REMOTE_ROOT}"
echo "  # 先 kill 旧进程，再启动新 API"
echo "  pkill -f api_server.py || true"
echo "  python3 src/rag_agent/api_server.py &"
echo ""
