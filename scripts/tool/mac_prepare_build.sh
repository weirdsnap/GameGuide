#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# mac_prepare_build.sh — Mac 上一键：拉代码 → 环境自检 → 建向量库 → 本地检索自检 → 给部署命令
#
# 为什么要在 Mac 上跑：服务器 2 核 / 3.6GB，跑不动 fastembed + FAISS。
#
# 用法（在仓库任意位置都行，脚本自己找根目录）：
#   bash scripts/tool/mac_prepare_build.sh                    ← 默认建 BG3
#   bash scripts/tool/mac_prepare_build.sh --game silksong     ← 建别的游戏
#   bash scripts/tool/mac_prepare_build.sh --no-pull           ← 不 git pull（离线/已最新）
#   bash scripts/tool/mac_prepare_build.sh --no-verify         ← 跳过建完后的检索自检
#
# 建完会直接打印上传 + 重启服务器 API 的命令。
# ─────────────────────────────────────────────────────────────

set -euo pipefail

GAME="baldurs_gate3"
DO_PULL=1
DO_VERIFY=1

usage() {
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --game|-g) GAME="${2:?--game 需要参数}"; shift 2 ;;
        --no-pull) DO_PULL=0; shift ;;
        --no-verify) DO_VERIFY=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "❌ 未知参数：$1"; echo; usage; exit 1 ;;
    esac
done

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  ✅ %s\n' "$*"; }
warn(){ printf '  ⚠️  %s\n' "$*"; }
die() { printf '  ❌ %s\n' "$*" >&2; exit 1; }

say "1/5 仓库同步"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 git 仓库：$ROOT"
OLD_SHA="$(git rev-parse --short HEAD)"
if [ -n "$(git status --porcelain)" ]; then
    warn "工作区有未提交改动，pull --ff-only 可能失败（先看一眼：git status --short）"
fi
if [ "$DO_PULL" -eq 1 ]; then
    git fetch --prune >/dev/null
    git pull --ff-only || die "拉取失败（本地有分叉或脏改动），先手动处理再跑"
    ok "SHA ${OLD_SHA} → $(git rev-parse --short HEAD)"
else
    ok "跳过 pull，当前 SHA ${OLD_SHA}"
fi

say "2/5 环境自检"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
ok "HF_ENDPOINT=${HF_ENDPOINT}  HF_HUB_DISABLE_XET=${HF_HUB_DISABLE_XET}"
[ -z "${HF_TOKEN:-}" ] || warn "HF_TOKEN 已设置，镜像站可能被绕过；国内网络下建议 unset HF_TOKEN"

if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
    warn "没有 .venv，用系统 python3（虚拟环境更稳：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt）"
else
    die "找不到 python3"
fi
ok "解释器：$PY（$($PY -V 2>&1)）"

DEPS_OK=1
for m in fastembed faiss langchain_text_splitters langchain_community langchain_core; do
    if ! $PY -c "import $m" >/dev/null 2>&1; then
        warn "缺依赖：$m"
        DEPS_OK=0
    fi
done
if [ "$DEPS_OK" -eq 0 ]; then
    echo
    echo "  先装依赖再跑："
    echo "    $PY -m pip install -r requirements.txt"
    echo "  （FASTEMBED_MODEL 必须是 sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2，换模型要全量重建所有游戏）"
    exit 1
fi
ok "依赖齐全（fastembed / faiss / langchain 系列）"

say "3/5 数据与磁盘检查"
$PY - "$GAME" <<'PY'
import sys
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root / "src"))
from rag_agent.game_registry import GAME_REGISTRY, GAMES_DIR

game = sys.argv[1]
if game not in GAME_REGISTRY:
    sys.exit(f"  ❌ 未知游戏：{game}（可选：{', '.join(GAME_REGISTRY)}）")
cfg = GAME_REGISTRY[game]
base = GAMES_DIR / cfg["dir"]

wiki = base / "data" / cfg["data_file"]
wiki_zh = base / "data" / cfg["data_file_zh"] if cfg.get("data_file_zh") else None
db = GAMES_DIR / cfg["dir"] / cfg["db_file"]

problems = []
for label, p in (("wiki", wiki), ("中文 wiki", wiki_zh), ("结构化库", db)):
    if p is None:
        continue
    if not p.is_file():
        problems.append(f"{label} 缺失：{p}")
    elif p.stat().st_size == 0:
        problems.append(f"{label} 是 0 字节：{p}")
    else:
        print(f"  ✅ {label}：{p.relative_to(root)}  {p.stat().st_size/1024/1024:.1f} MB")

if problems:
    for msg in problems:
        print(f"  ❌ {msg}")
    sys.exit(1)

import shutil
free_gb = shutil.disk_usage(root).free / 1024**3
print(f"  ✅ 磁盘剩余 {free_gb:.1f} GB")
if free_gb < 1.0:
    sys.exit("  ❌ 磁盘剩余不足 1 GB，向量库写不下")
print(f"  → 输出目录：{base / 'vectorstore'}")
PY

say "4/5 建向量库（首次会下载 embedding 模型；BG3 约 6.7 万块 / ~100 MB，预计 5–15 分钟）"
START=$(date +%s)
$PY scripts/tool/mac_build.py --game "$GAME"
ELAPSED=$(( $(date +%s) - START ))
ok "构建耗时 $((ELAPSED / 60)) 分 $((ELAPSED % 60)) 秒"

VS_DIR="games/${GAME}/vectorstore"
[ -f "$VS_DIR/index.faiss" ] || die "没生成 $VS_DIR/index.faiss，构建没成功"
ok "产物：$VS_DIR（$(du -sh "$VS_DIR" | cut -f1)）"

if [ "$DO_VERIFY" -eq 1 ]; then
    say "5/5 本地检索自检（走 tools.search_knowledge_base 的真实 language filter 路径）"
    $PY - "$GAME" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "src"))
from rag_agent import glossary
from rag_agent.tools import search_knowledge_base

game = sys.argv[1]
queries = ["影心是谁", "月出之塔怎么走", "钢铁卫士铸造厂在哪", "How does Divine Smite work"]
bad = []
for q in queries:
    expanded, hits = glossary.expand_query(q, game)
    tag = "，".join(f"{zh}→{en}" for zh, en, _c, _f in hits) or "无扩展"
    # 用真实工具而不是裸 similarity_search：工具带 language filter，
    # 缺 language 元数据的文档在这里会暴露成 0 命中。
    out = search_knowledge_base.invoke({"query": expanded, "game": game, "k": 3})
    print(f"\n  问：{q}   [{tag}]")
    for line in out.splitlines()[:6]:
        print(f"    {line[:110]}")
    if out.startswith(("[知识库暂时不可用]", "[知识库检索出错]", "(知识库未找到")):
        bad.append(q)

if bad:
    print(f"\n  ❌ {len(bad)}/{len(queries)} 条走真实检索路径失败：")
    for q in bad:
        print(f"     - {q}")
    print("  常见原因：文档缺 language 元数据（en/zh）被 filter 过滤、向量库没建全")
    sys.exit(1)
print("\n  ✅ 自检通过：真实检索路径有命中，可以上传")
PY
else
    say "5/5 跳过检索自检"
fi

say "完成 — 接下来两步（在 Mac 上执行）"
cat <<EOF
  1) 传回服务器（会要一次 SSH 密码）：
       bash scripts/deploy/vectorstores.sh ${GAME}

  2) 服务器上重启 API 生效（我这边也能帮你跑）：
       ssh snap@114.132.189.56
       cd /data/learning/agent && git pull && bash scripts/deploy/api.sh

  线上抽查：中文问一句「影心是谁」、英文问一句「Divine Smite 怎么用」，
  答案里不应再出现「知识库暂时不可用」。
EOF
