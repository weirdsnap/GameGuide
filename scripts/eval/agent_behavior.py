#!/usr/bin/env python3
"""模型/Agent 表现级评测（Tier 2-lite）——场景集回放 + 四类判定。

与 tests/test_routing_regression.py（代码层、不跑 LLM）互补：
本脚本把真实的图跑起来，逐轮检查「落库游戏 / 工具游戏参数 / 引用真伪 / 剧透越界」。
场景集见 scripts/eval/behavior_cases.jsonl，事故背景见
docs/ROUTING_MISROUTE_2026-11-15.md，评测体系说明见 docs/EVAL_SYSTEM.md。

用法：
  # 只跑路由（不调 LLM，秒级，可进 CI）
  .venv/bin/python scripts/eval/agent_behavior.py

  # 全量（真实 LLM + 检索，慢、要 API key）
  FASTEMBED_CACHE_PATH=$PWD/.cache/fastembed \\
  .venv/bin/python scripts/eval/agent_behavior.py --mode full --json /tmp/report.json

  # 只跑某几条 / 与基线对比看回归
  .venv/bin/python scripts/eval/agent_behavior.py --mode full --only bg3-amulet-chain
  .venv/bin/python scripts/eval/agent_behavior.py --mode full --baseline /tmp/report.json

判定项（每轮）：
  route      落库游戏 == 期望游戏
  tool_game  每次工具调用的 game 参数 == 本轮落库游戏（全量模式）
  citation   「来源：知识库「X」」里的 X 必须在本轮工具返回里出现过（全量模式）
  spoiler    答案不越过 allowed_act，也不含 forbidden_terms（全量模式）

退出码：有任何判定失败 → 1（--no-fail 可关掉，用于只看报告）。
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

# 检索用的 ONNX 模型缓存在仓库 .cache/fastembed（api.sh 里也是这个路径）。
# 不设的话 fastembed 会去 $TMPDIR/fastembed_cache 找，清过 /tmp 就报
# "Could not find config.json"，全量模式的检索会整体失败。
_CACHE = ROOT / ".cache" / "fastembed"
if _CACHE.is_dir():
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(_CACHE))

DEFAULT_CASES = ROOT / "scripts" / "eval" / "behavior_cases.jsonl"

# 引用抽取：只看显式标注来源的句子，避免把正文里所有「…」都当来源。
_SOURCE_HINT = re.compile(r"来源|知识库|条目")
_QUOTED = re.compile(r"「([^」]{1,80})」")
_PAREN_EN = re.compile(r"[（(]([^）)]{2,80})[）)]")
# 缺陷 B（I-16）：引用判定必须「标记词紧跟引号」才算来源引用。
# 旧实现是「行里有 来源|知识库|条目 就把整行所有「」当引用」，正文里的
# 人名引号（如 法师「洛若坎」）会被误报成伪造来源，真问题被噪音盖住。
_CITATION = re.compile(r"(?:来源[:：]?|知识库|数据库|条目)\s*「([^」]{1,80})」")


# ══════════════════════════════════════════════════════════════
#  场景集
# ══════════════════════════════════════════════════════════════

def load_cases(path: Path, only: list[str] | None = None) -> list[dict]:
    cases = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            case = json.loads(line)
            if only and case["id"] not in only:
                continue
            turns = case["turns"]
            expect = case.get("expect_games") or ["__none__"] * len(turns)
            if len(expect) != len(turns):
                raise SystemExit(f"{case['id']}: expect_games 与 turns 数量不一致")
            case["expect_games"] = expect
            case["context"] = case.get("context") or []
            case["known_issue"] = bool(case.get("known_issue"))
            cases.append(case)
    return cases


# ══════════════════════════════════════════════════════════════
#  判定
# ══════════════════════════════════════════════════════════════

def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).lower()


def check_citation(answer: str, tool_output: str) -> dict:
    """答案里显式标来源的「X」，必须在本轮工具返回文本里能找到。

    X 允许两种写法：纯中文标题，或「中文（English）」；后者用中文和英文任一命中即可。
    没有任何工具返回时，任何「来源：知识库「X」」都算编造。
    """
    quoted: list[str] = _CITATION.findall(answer or "")
    haystack = _norm(tool_output)
    missing = []
    for title in quoted:
        candidates = [title]
        candidates += _PAREN_EN.findall(title)
        candidates.append(_PAREN_EN.sub("", title))
        if not any(_norm(c) and _norm(c) in haystack for c in candidates):
            missing.append(title)
    return {"quoted": quoted, "missing": missing, "ok": not missing}


def check_spoiler(game: str | None, answer: str, max_level, forbidden: list[str]) -> dict:
    """等级判定：命中高于 max_level 的剧透词即失败。

    等级定义与处置矩阵见 docs/SPOILER_SEVERITY.md：
      L1 地图类（地名/区域/道具名/机制存在性）——默认放过，只记录
      L2 攻略类（解法/数值/触发条件/装备搭配）
      L3 谜底类（身份反转/重大抉择后果/角色命运/结局）
    max_level=None 表示该游戏没有词表（或本用例不锚进度），这一项跳过。
    """
    from api_server import spoiler_hits, spoiler_over

    if max_level is None:
        return {"hits": [], "over": [], "forbidden": [], "ok": True, "skipped": True}

    hits = spoiler_hits(game, answer or "")
    over = spoiler_over(hits, int(max_level))
    low = (answer or "").lower()
    lit = [t for t in (forbidden or []) if t.lower() in low]
    return {"hits": hits, "over": over, "forbidden": lit, "ok": not over and not lit}


def eval_turn(want_game: str, got_game: str | None, answer: str | None,
              tool_records: list[dict], case: dict, full: bool) -> dict:
    result = {
        "want_game": want_game,
        "got_game": got_game,
        "route_ok": got_game == want_game,
    }
    if not full:
        return result

    tool_games = [r["args"].get("game") for r in tool_records]
    bad_tools = [g for g in tool_games if g and g != got_game]
    result["tool_games"] = tool_games
    result["tool_game_bad"] = bad_tools
    result["tool_game_ok"] = not bad_tools

    tool_output = "\n".join(r.get("out") or "" for r in tool_records)
    result["citation"] = check_citation(answer or "", tool_output)
    result["max_level"] = case.get("max_level")
    result["spoiler"] = check_spoiler(got_game, answer or "",
                                      case.get("max_level"), case.get("forbidden_terms"))
    result["answer_head"] = " ".join((answer or "").split())[:160]
    return result


def turn_failed(result: dict, full: bool) -> bool:
    if not result["route_ok"]:
        return True
    if not full:
        return False
    return not (result["tool_game_ok"] and result["citation"]["ok"] and result["spoiler"]["ok"])


# ══════════════════════════════════════════════════════════════
#  路由模式（不调 LLM）
# ══════════════════════════════════════════════════════════════

def run_routing(cases: list[dict]) -> list[dict]:
    from rag_agent.graph import detect_game_node
    from rag_agent.multi_agent import _build_initial_state

    report = []
    for case in cases:
        history: list[dict] = []
        turns = []
        # context：复现「会话已经进行了一会儿」的前置轮次，不计分
        for question in case["context"]:
            state = _build_initial_state(question, history or None, None, False)
            detect_game_node(state)
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": "（路由模式不产生答案）"})
        for i, question in enumerate(case["turns"]):
            state = _build_initial_state(question, history or None, None, False)
            out = detect_game_node(state)
            got = out.get("detected_game")
            turns.append({
                "question": question,
                **eval_turn(case["expect_games"][i], got, None, [], case, full=False),
            })
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": "（路由模式不产生答案）"})
        report.append({"id": case["id"], "turns": turns,
                       "known_issue": case["known_issue"],
                       "failed": any(turn_failed(t, False) for t in turns)})
    return report


# ══════════════════════════════════════════════════════════════
#  全量模式（真实 LLM + 检索）
# ══════════════════════════════════════════════════════════════

def _install_tool_recorder() -> list[dict]:
    """给三个全局工具套一层记录器：记下每次调用的入参和返回文本。

    做法是把 StructuredTool 换成一个 func 被包过的副本，再原地替换
    GAME_TOOLS 的列表元素——graph 那边是按引用拿的同一个列表对象，所以立刻生效。
    """
    from rag_agent.tools import GAME_TOOLS

    records: list[dict] = []
    for i, tool in enumerate(list(GAME_TOOLS)):
        if not getattr(tool, "func", None):
            continue

        def make(name, original, sink):
            def wrapper(*args, **kwargs):
                rec = {"tool": name, "args": dict(kwargs), "out": None}
                sink.append(rec)
                out = original(*args, **kwargs)
                rec["out"] = out if isinstance(out, str) else str(out)
                return out
            return wrapper

        GAME_TOOLS[i] = tool.model_copy(update={"func": make(tool.name, tool.func, records)})
    return records


async def _run_turn(graph, question: str, history: list[dict]) -> tuple[str, str | None]:
    """跑一轮图，返回 (答案, 落库游戏)。"""
    from rag_agent.multi_agent import _build_initial_state

    meta: dict = {}
    final_state: dict = {}
    async for namespace, mode, chunk in graph.astream(
        _build_initial_state(question, history or None, None, False),
        stream_mode=["custom", "messages", "values"],
        subgraphs=True,
    ):
        if namespace == () and mode == "custom" and isinstance(chunk, dict):
            if chunk.get("type") == "meta":
                meta.update({k: v for k, v in chunk.items() if k != "type"})
        elif namespace == () and mode == "values":
            final_state = chunk
    return final_state.get("answer") or "", meta.get("game")


def run_full(cases: list[dict]) -> list[dict]:
    from rag_agent.graph import get_graph

    records = _install_tool_recorder()
    graph = get_graph()

    report = []
    for case in cases:
        history: list[dict] = []
        turns = []
        # context：复现「会话已经进行了一会儿」的前置轮次，不计分。
        # 缺陷 A（I-15）修复：full 模式原先漏了这段，依赖前文的用例
        # 在无历史下整条落 menu 被误判路由错（假阴性）。
        for question in case["context"]:
            history.append({"role": "user", "content": question})
            # I-20：占位文本模型看得见，用中性应答降低对内容层判定的干扰
            history.append({"role": "assistant", "content": "好的，明白了。"})
        for i, question in enumerate(case["turns"]):
            before = len(records)
            answer, got = asyncio.run(_run_turn(graph, question, history))
            turn_records = records[before:]
            turns.append({
                "question": question,
                **eval_turn(case["expect_games"][i], got, answer, turn_records, case, full=True),
            })
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": answer})
        report.append({"id": case["id"], "turns": turns,
                       "known_issue": case["known_issue"],
                       "failed": any(turn_failed(t, True) for t in turns)})
    return report


# ══════════════════════════════════════════════════════════════
#  报告
# ══════════════════════════════════════════════════════════════

def print_report(report: list[dict], full: bool) -> None:
    total_turns = sum(len(c["turns"]) for c in report)
    bad_turns = sum(1 for c in report for t in c["turns"] if turn_failed(t, full))
    known = [c for c in report if c["failed"] and c.get("known_issue")]
    failed = [c for c in report if c["failed"] and not c.get("known_issue")]
    print(f"\n{'=' * 62}")
    print(f"模式: {'全量（LLM + 检索）' if full else '路由（不调 LLM）'}"
          f" | 用例 {len(report)} | 轮次 {total_turns} | 失败轮次 {bad_turns}"
          f" | 已知缺陷 {len(known)}")
    print("=" * 62)

    for case in report:
        if case["failed"] and case.get("known_issue"):
            head = "KNOWN"
        elif case["failed"]:
            head = "FAIL "
        else:
            head = "ok   "
        print(f"\n[{head}] {case['id']}")
        for i, t in enumerate(case["turns"], 1):
            flag = "x" if turn_failed(t, full) else "."
            line = f"  {flag} T{i} {t['question'][:30]:32} 期望={t['want_game']} 实际={t['got_game']}"
            print(line)
            if not t["route_ok"]:
                print("      ↳ 路由错了：工具会去检索错游戏的库")
            if full:
                if not t.get("tool_game_ok", True):
                    print(f"      ↳ 工具 game 参数与落库游戏不一致：{t['tool_game_bad']}")
                cit = t.get("citation") or {}
                if cit.get("missing"):
                    print(f"      ↳ 伪造来源（工具返回里找不到）：{cit['missing']}")
                sp = t.get("spoiler") or {}
                if not sp.get("ok", True):
                    over = [(h["term"], f"act{h['act']}", f"L{h.get('level', 1)}")
                            for h in sp.get("over", [])]
                    print(f"      ↳ 剧透超过 L{t.get('max_level')}：{over}"
                          f" 命中禁用词：{sp.get('forbidden')}")

    print(f"\n结果：干净通过 {len(report) - len(failed) - len(known)}/{len(report)}"
          f"，已知缺陷 {len(known)}，失败 {len(failed)}\n")


def compare_baseline(report: list[dict], path: Path) -> list[str]:
    """与基线报告逐用例对比，返回新出现的失败用例 id。"""
    if not path.is_file():
        print(f"[基线] {path} 不存在，本次作为新基线（用 --json 保存）")
        return []
    old = {c["id"]: c["failed"] for c in json.loads(path.read_text(encoding="utf-8"))["cases"]}
    regressed = [c["id"] for c in report if c["failed"] and not old.get(c["id"], False)]
    fixed = [c["id"] for c in report if not c["failed"] and old.get(c["id"], False)]
    if regressed:
        print(f"[基线] 新增失败：{regressed}")
    if fixed:
        print(f"[基线] 已修复：{fixed}")
    if not regressed and not fixed:
        print("[基线] 与基线一致")
    return regressed


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent 表现级评测（场景回放）")
    ap.add_argument("--mode", choices=["routing", "full"], default="routing")
    ap.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    ap.add_argument("--only", nargs="*", help="只跑指定 case id")
    ap.add_argument("--json", type=Path, help="把报告写到这个文件（也用作基线）")
    ap.add_argument("--baseline", type=Path, help="与基线报告对比，只提示回归")
    ap.add_argument("--no-fail", action="store_true", help="有失败也返回 0")
    args = ap.parse_args()

    cases = load_cases(args.cases, args.only)
    if not cases:
        raise SystemExit("没有匹配的用例")

    report = run_routing(cases) if args.mode == "routing" else run_full(cases)
    print_report(report, full=args.mode == "full")

    regressed = compare_baseline(report, args.baseline) if args.baseline else []
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {"mode": args.mode, "cases": report}
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[报告] 已写入 {args.json}")

    failed = any(c["failed"] and not c.get("known_issue") for c in report)
    if args.no_fail:
        return 0
    return 1 if (failed or regressed) else 0


if __name__ == "__main__":
    sys.exit(main())
