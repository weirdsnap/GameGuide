#!/usr/bin/env python3
"""路由回归集（代码层）——线上误判轮次固化为用例。

来源：docs/ROUTING_MISROUTE_2026-11-15.md
每个用例 id 带线上时间戳，出问题能直接定位到 turns 记录。

记号说明：
  已知缺陷用例挂 xfail(strict=True)：当前预期失败；修好之后会变 XPASS，
  严格模式直接报 failure，逼着把标记摘掉——这是故意的，不是漏写。

本文件不跑 LLM、不查库：只跑 graph.detect_game_node 这一个纯路由决策节点。
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rag_agent.game_registry import AVAILABLE_GAMES, GAME_SIGNALS  # noqa: E402
from rag_agent.graph import detect_game_node  # noqa: E402


# ── 前置：路由决策会检查库是否就绪，缺数据时整组跳过 ──────────────

_USED_GAMES = ("baldurs_gate3", "hollow_knight", "oni")


def _data_ready(game: str) -> bool:
    cfg = AVAILABLE_GAMES[game]
    return os.path.isdir(cfg["vectorstore_dir"]) or os.path.isfile(cfg["db_path"])


pytestmark = pytest.mark.skipif(
    not all(_data_ready(g) for g in _USED_GAMES),
    reason="缺少 baldurs_gate3 / hollow_knight / oni 的本地库，路由会走「知识库未就绪」分支",
)


def route(question, last_game=None, confirmed=False, history=None):
    """跑一次真实的 detect_game_node，返回它的输出 dict。

    历史用字符串列表模拟：真实请求里 history 是 [{role, content}]，
    detect_game_node 只用它判断「有没有历史」，内容不参与决策。
    """
    state = {
        "question": question,
        "last_game": last_game,
        "last_game_confirmed": confirmed,
        "history": [{"role": "user", "content": h} for h in (history or [])] or None,
    }
    return detect_game_node(state)


# ══════════════════════════════════════════════════════════════
#  1. 线上误判轮次（BG3 会话被别的游戏抢走）
# ══════════════════════════════════════════════════════════════

# (case_id, 提问, 上下文已确认游戏, 期望游戏, 是否已知缺陷)
ONLINE_CASES = [
    ("online-2026-11-15#2316", "水电工怎么玩",
     "baldurs_gate3", "baldurs_gate3", False),
    ("online-2026-11-15#2346", "护符里的幽灵那个任务的",
     "baldurs_gate3", "baldurs_gate3", True),
    ("online-2026-11-15#2347",
     "帮助他到最后，他问我要不要继承莎尔的诅咒，学会狂笑术，但是有代价",
     "baldurs_gate3", "baldurs_gate3", False),
    ("online-2026-11-16#0014",
     "明萨拉用压迫灵魂之剑还是静止者？我选的是单手武器+盾牌的职业",
     "baldurs_gate3", "baldurs_gate3", True),
]

# 已知缺陷的理由（按 case_id）。修好后把这些条目和 known_bad 一起删掉
_XFAIL_REASON = {
    "online-2026-11-15#2346":
        "HK signals 含通用词「护符」，单信号 conf=1.0 直接切走 BG3（待 P0 切换门槛）",
    "online-2026-11-16#0014":
        "HK signals 含通用词「灵魂」，同样是单信号切换（待 P0 切换门槛）",
}


@pytest.mark.parametrize("case_id,q,ctx_game,expected,known_bad", ONLINE_CASES,
                         ids=[c[0] for c in ONLINE_CASES])
def test_online_misroute_cases(case_id, q, ctx_game, expected, known_bad, request):
    """线上会话中段：已确认游戏不该被一个通用词抢走。"""
    if known_bad:
        request.node.add_marker(
            pytest.mark.xfail(strict=True, reason=_XFAIL_REASON[case_id]))
    out = route(q, last_game=ctx_game, confirmed=True, history=["上一轮的同游戏追问"])
    assert out.get("detected_game") == expected, (
        f"{case_id}: 期望 {expected}，实际 {out.get('detected_game')}"
        f"（switched={out.get('game_switched')}）"
    )


# ══════════════════════════════════════════════════════════════
#  2. 切换门槛契约：一个通用词不足以切换已确认的会话
# ══════════════════════════════════════════════════════════════

# (提问, 上下文已确认游戏, 通用词来源, 用例 id)
GENERIC_SIGNAL_CASES = [
    ("这个护符怎么样？", "baldurs_gate3", "hollow_knight 的「护符」", "charm"),
    ("灵魂是干什么的？", "baldurs_gate3", "hollow_knight 的「灵魂」", "soul"),
    ("这个法术怎么放？", "hollow_knight", "baldurs_gate3 的「法术」", "spell"),
]


@pytest.mark.parametrize("q,ctx_game,why", [c[:3] for c in GENERIC_SIGNAL_CASES],
                         ids=[c[3] for c in GENERIC_SIGNAL_CASES])
def test_generic_signal_alone_does_not_switch(q, ctx_game, why, request):
    """P0 契约：通用词只能做弱证据，不能单独推翻已确认的游戏。

    修好后的期望行为：沿用 ctx_game；跨游戏切换要么靠专有词，
    要么靠 exact_patterns / 显式游戏名。
    """
    request.node.add_marker(pytest.mark.xfail(
        strict=True, reason=f"单信号 {why} 直接把会话切走了（待 P0 切换门槛）"))
    out = route(q, last_game=ctx_game, confirmed=True, history=["同游戏追问"])
    assert out.get("detected_game") == ctx_game, (
        f"「{q}」只因 {why} 就把会话从 {ctx_game} 切走了"
    )


# ══════════════════════════════════════════════════════════════
#  3. 不能让 P0 把正当切换一起掐掉（对照组）
# ══════════════════════════════════════════════════════════════

def test_explicit_game_name_switches():
    """用户手打游戏名（线上 11-16 00:14 后就是这么纠正的），必须切。"""
    out = route("BG3", last_game="hollow_knight", confirmed=True,
                history=["上一轮被误判成 HK 的追问"])
    assert out.get("detected_game") == "baldurs_gate3"


def test_proper_noun_switches_across_games():
    """专有名词（螳螂爪只属于空洞骑士）也必须能切。"""
    out = route("空洞骑士螳螂爪怎么拿", last_game="baldurs_gate3", confirmed=True,
                history=["BG3 的追问"])
    assert out.get("detected_game") == "hollow_knight"


def test_new_session_without_history_can_use_generic_word():
    """没有历史的开场，通用词依然是允许的定向线索（否则「这个护符怎么拿」无解）。"""
    out = route("这个护符怎么拿")
    assert out.get("detected_game") == "hollow_knight"


# ══════════════════════════════════════════════════════════════
#  4. 会话连续性（c9f3f90 的既有契约，回归保护）
# ══════════════════════════════════════════════════════════════

def test_no_menu_mid_session():
    """会话中段推不出游戏时沿用历史，绝不弹菜单。"""
    out = route("那后面呢", last_game="baldurs_gate3", confirmed=True,
                history=["BG3 的追问"])
    assert out.get("detected_game") == "baldurs_gate3"
    assert out.get("menu_text") is None


def test_external_game_clears_context():
    """点名不在库里的游戏 → 清空上下文走 LLM 兜底（原神等）。"""
    out = route("原神的深渊怎么打", last_game="baldurs_gate3", confirmed=True,
                history=["BG3 的追问"])
    assert out.get("detected_game") == "__llm_fallback__"
    assert out.get("last_game") is None


# ══════════════════════════════════════════════════════════════
#  5. 信号词表数据契约（防止再把单字/通用词写回去）
# ══════════════════════════════════════════════════════════════

# 已知债务白名单（不在这里的名单里出现就说明债务已清，顺手把下面那条 xfail 摘掉）
_SINGLE_CHAR_DEBT = {("cyberpunk2077", "漩")}


def test_no_single_char_cjk_signals():
    """单字中文信号一律不允许：子串匹配下任何含该字的句子都会命中，等于没有门槛。

    纯拉丁单字（如 cyberpunk2077 的 "v"）不在此列：_match_signal 对它做了
    前后字母边界检查，不会匹配到别的单词内部。
    """
    offenders = [(g, s) for g, sigs in GAME_SIGNALS.items()
                 for s in sigs if len(s) == 1 and not s.isascii()
                 and (g, s) not in _SINGLE_CHAR_DEBT]
    assert not offenders, f"出现单字中文信号词：{offenders}"


def test_cyberpunk_truncated_signal_is_known_debt(request):
    """写本回归集时顺手发现的：cyberpunk2077 的「漩」是「漩涡帮」被截断的产物。

    它会把任何含「漩」的句子拽去赛博朋克（例如在别的游戏里说「漩涡」）。
    属于待清理项，不是本次事故的根因，故单独挂 xfail 记录。
    """
    request.node.add_marker(pytest.mark.xfail(
        strict=True, reason="cyberpunk2077 signals 含被截断的单字「漩」（待清理）"))
    assert "漩" not in set(GAME_SIGNALS["cyberpunk2077"])


def test_oni_no_single_char_heat_electric():
    """c9f3f90 的具体修复点：oni 不再用「电」「热」单字。"""
    oni = set(GAME_SIGNALS["oni"])
    assert "电" not in oni and "热" not in oni
    assert "温度管理" in oni  # 换成了明确短语
