#!/usr/bin/env bash
# 空洞骑士端到端回归的「按改动触发」闸门。
#
# 用途：tests/test_qa.py 的 20 条 e2e 用例真调 LLM（一次约 10 分钟、消耗额度），
# 不适合每次 pytest 都跑，也不该像以前那样永远跳过。规则（见 docs/EVAL_SYSTEM.md §L1.5）：
#   - 上次跑过之后，若 HK 相关文件有改动 → 跑
#   - 没有改动 → 跳过
#
# 用法：
#   scripts/eval/hk_e2e_gate.sh              # 判定 + 需要时跑
#   scripts/eval/hk_e2e_gate.sh --dry-run    # 只判定，不跑
#   scripts/eval/hk_e2e_gate.sh --force      # 无条件跑
#
# 不装 crontab。触发时机：改完 HK 相关文件后手动跑，或看 scripts/deploy/api.sh
# 启动时的提醒（它会调本脚本 --dry-run 判定）。
#
# 退出码：0 = 通过或按规则跳过；1 = 用例失败；2 = 环境不满足（未跑）。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 2

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
STATE_DIR="$ROOT/data/eval"
STATE="$STATE_DIR/hk_e2e.state"          # 第一行 = 上次跑的 commit
mkdir -p "$STATE_DIR"

DRY=0; FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --force)   FORCE=1 ;;
    *) echo "未知参数：$arg"; exit 2 ;;
  esac
done

# HK 改动判定范围：注册表（含 HK 配置与信号词）、共享路由/检索链路、
# HK 数据与库、以及这套用例本身。换 embedding 模型也算（requirements 变更）。
HK_PATHS=(
  src/rag_agent/game_registry.py
  src/rag_agent/game_router.py
  src/rag_agent/graph.py
  src/rag_agent/tools.py
  src/rag_agent/vectorstore.py
  src/rag_agent/multi_agent.py
  games/hollow_knight/
  tests/test_qa.py
  requirements.txt
)

HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || echo none)"
LAST_SHA="$(head -1 "$STATE" 2>/dev/null || true)"

should_run=0
reason=""

if [ "$FORCE" = "1" ]; then
  should_run=1; reason="--force"
elif [ -z "$LAST_SHA" ]; then
  should_run=1; reason="从未跑过（无状态文件）"
elif [ "$LAST_SHA" = "$HEAD_SHA" ]; then
  should_run=0; reason="HEAD 未变（$HEAD_SHA）"
elif ! git cat-file -e "$LAST_SHA^{commit}" 2>/dev/null; then
  should_run=1; reason="上次的 commit 不在本仓库历史里（可能 rebase/换机器）"
else
  changed="$(git diff --name-only "$LAST_SHA".."$HEAD_SHA" 2>/dev/null)"
  hit=""
  for p in "${HK_PATHS[@]}"; do
    if printf '%s\n' "$changed" | grep -q "^$p"; then hit="$p"; break; fi
  done
  if [ -n "$hit" ]; then
    should_run=1; reason="HK 相关文件有改动（$hit）"
  else
    should_run=0; reason="$LAST_SHA..$HEAD_SHA 未触及 HK 相关路径"
  fi
fi

echo "[$(date '+%F %T')] HEAD=$HEAD_SHA last=${LAST_SHA:-none} → $([ "$should_run" = 1 ] && echo 跑 || echo 跳过)：$reason"

if [ "$should_run" = "0" ]; then
  exit 0
fi
if [ "$DRY" = "1" ]; then
  echo "（--dry-run，未执行）"
  exit 0
fi

# 检索必需：不设这个会报 FastEmbedEmbeddings validation error（见 deploy/api.sh）
export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-$ROOT/.cache/fastembed}"
if [ ! -d "$FASTEMBED_CACHE_PATH" ]; then
  echo "缺 $FASTEMBED_CACHE_PATH，跳过（先跑一次在线服务或 mac_build.py 生成缓存）"
  exit 2
fi

echo "开始跑 HK e2e（20 条，真调 LLM）..."
if "$PY" -m pytest tests/test_qa.py --run-e2e -q; then
  echo "$HEAD_SHA" > "$STATE"
  echo "$(date '+%F %T') pass $HEAD_SHA" >> "$STATE_DIR/hk_e2e.history"
  echo "结果：通过，已记录 $HEAD_SHA"
  exit 0
else
  rc=$?
  echo "$(date '+%F %T') FAIL $HEAD_SHA" >> "$STATE_DIR/hk_e2e.history"
  echo "结果：失败（不更新状态文件，下次仍会跑）"
  exit $((rc == 0 ? 1 : rc))
fi
