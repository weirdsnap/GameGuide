#!/usr/bin/env python3
"""路由缺口回归集（L0，代码层）——2026-11-16 扫线上 turns 后新增。

来源：`docs/ROUTING_GAPS_2026-11-16.md`（分析 + 建议方案）
与 `test_routing_regression.py` 分开的原因：那个文件是 P0 切换门槛的实施挂点，
Mac 端要改它摘 xfail；本文件只登记**独立缺口**，避免双端改同一文件冲突。

约定同仓库既有：
  - 已知缺陷挂 `xfail(strict=True)`：现在预期失败；修好后变 XPASS 直接报 failure，
    逼着把标记摘掉。
  - 本文件不跑 LLM、不查库，只调纯决策函数（`detect_game` / `detect_game_node` /
    `turn_record`），因此秒级可跑、可进 CI。

用例 → 缺口对应表：
  test_bare_bg_alias                     → G1 简写不识别
  test_minthara_alias_*                  → G2 专名两种译法都不在信号表
  test_fallback_prompt_guides_user       → G3 兜底静默，不引导用户给游戏名
  test_followup_after_fallback_no_menu   → G3 回归保护（有 history 时不得弹菜单）
  test_mixed_concrete_and_generic        → G5 专有词与通用词打平就判不出
  test_turn_record_carries_sources       → G4 来源不可事后审计
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))          # api_server 在仓库根

from rag_agent.game_registry import AVAILABLE_GAMES          # noqa: E402
from rag_agent.game_router import detect_game                # noqa: E402
from rag_agent import graph as graph_mod                     # noqa: E402
from rag_agent.graph import detect_game_node, _FALLBACKPT    # noqa: E402


_USED_GAMES = ("baldurs_gate3", "hollow_knight")


def _data_ready(game: str) -> bool:
    cfg = AVAILABLE_GAMES[game]
    return os.path.isdir(cfg["vectorstore_dir"]) or os.path.isfile(cfg["db_path"])


pytestmark = pytest.mark.skipif(
    not all(_data_ready(g) for g in _USED_GAMES),
    reason="缺少 baldurs_gate3 / hollow_knight 的本地库，路由会走「知识库未就绪」分支",
)


def _state(question, last_game=None, confirmed=False, history=None):
    return {
        "question": question,
        "last_game": last_game,
        "last_game_confirmed": confirmed,
        "history": [{"role": "user", "content": h} for h in (history or [])] or None,
    }


# ══════════════════════════════════════════════════════════════
#  G1：「BG」这类简写
# ══════════════════════════════════════════════════════════════

def test_bare_bg_alias(request):
    """线上 11-14 11:39 用户只打了「BG」，被弹菜单。

    修法：exact_patterns 增加整句式 `^\\s*bg\\s*$`（不要 `\\bbg\\b`，
    中文与字母同属 \\w，句中边界不成立）。
    """
    request.node.add_marker(pytest.mark.xfail(
        strict=True, reason="G1：exact_patterns 只有 bg3，整句「BG」无信号（见 ROUTING_GAPS §1）"))
    game, _ = detect_game("BG")
    assert game == "baldurs_gate3"


def test_bare_bg_alias_switches_mid_session(request):
    """会话中段打「BG」是用户显式纠正，必须能切回来（线上 11-14 12:10 就是这么恢复的）。"""
    request.node.add_marker(pytest.mark.xfail(
        strict=True, reason="G1：整句简写既不定游戏也不触发切换（见 ROUTING_GAPS §1）"))
    out = detect_game_node(_state("BG", last_game="hollow_knight", confirmed=True,
                                  history=["HK 的追问"]))
    assert out.get("detected_game") == "baldurs_gate3"


# ══════════════════════════════════════════════════════════════
#  G2：同一个 NPC 的两种中文写法
# ══════════════════════════════════════════════════════════════

# (提问, 说明) —— 第一项是模型自己的译名，第二项是用户的写法
MINTHARA_CASES = [
    ("明莎拉这个职业怎么说", "模型译名（线上 11-15 00:17 原文）"),
    ("明萨拉用什么武器", "用户写法（线上 11-16 00:14 原文）"),
    ("minthara 怎么招募", "英文原名"),
]


@pytest.mark.parametrize("q,why", MINTHARA_CASES, ids=["mingshala", "mingsala", "minthara"])
def test_minthara_alias_recognized(q, why, request):
    """整轮 fallback / 误判都是从「信号表里没有这个名字」开始的。

    修法：BG3 signals 并列收 `明萨拉` / `明莎拉` / `minthara`，
    同类高风险专名（贾希拉/哈尔辛/明斯克…）一并补。
    """
    request.node.add_marker(pytest.mark.xfail(
        strict=True, reason=f"G2：BG3 signals 缺 {why}（见 ROUTING_GAPS §2）"))
    game, _ = detect_game(q)
    assert game == "baldurs_gate3"


# ══════════════════════════════════════════════════════════════
#  G3：兜底路径静默
# ══════════════════════════════════════════════════════════════

def test_fallback_prompt_guides_user(request):
    """兜底提示词要留一条恢复路径：让模型告诉用户「说出游戏名我就能查库」。

    线上三处证据（11-14 12:09、11-14 12:19、11-15 00:17）里，用户都是自己
    重新打了「BG3」才恢复——说明这段引导只存在于菜单文案，不存在于兜底文案。
    """
    request.node.add_marker(pytest.mark.xfail(
        strict=True, reason="G3：_FALLBACKPT 没有任何「告诉我游戏名」的引导（见 ROUTING_GAPS §3）"))
    assert "游戏名" in _FALLBACKPT or "游戏名称" in _FALLBACKPT


def test_followup_after_fallback_no_menu():
    """回归保护：有 history 的追问不能掉回菜单（现状已满足，防止改坏）。

    注意线上 11-15 00:19 掉菜单的真实原因是**该轮 history 为空**
    （history_msgs 未落库，无法确认是前端未传还是刷新），
    服务端只能在「有 history」时保证不弹菜单。
    """
    out = detect_game_node(_state("我选的好像是单手武器+盾牌",
                                  history=["明莎拉这个职业怎么说", "她默认什么誓约"]))
    assert out.get("menu_text") is None
    assert out.get("detected_game") is not None


# ══════════════════════════════════════════════════════════════
#  G5：专有词 + 通用词混句
# ══════════════════════════════════════════════════════════════

def test_mixed_concrete_and_generic(request):
    """真实写法「我在玩bg3，问个护符」现在判不出：BG3 的 bg3 记 1 分、
    HK 的「护符」记 1 分，打平后 conf 折半 0.25 < 0.3 → None。

    P0 加权（专有 1.0 / 通用 0.25）后应变成 BG3 0.8——这条是 P0-2 的实证用例。
    """
    request.node.add_marker(pytest.mark.xfail(
        strict=True, reason="G5：专有词与通用词等权，打平即判不出（P0-2 会修）"))
    game, _ = detect_game("我在玩bg3，问个护符")
    assert game == "baldurs_gate3"


# ══════════════════════════════════════════════════════════════
#  G4：一轮记录要带来源
# ══════════════════════════════════════════════════════════════

def test_turn_record_carries_sources(request):
    """`turn_record` 要能落工具返回的来源标题，否则伪引用无法批量审计。

    现状只有 tool_calls 计数：事故里「编造三条不存在的来源」只能靠读回答反推，
    P1 的引用校验也就没有样本来源。
    """
    request.node.add_marker(pytest.mark.xfail(
        strict=True, reason="G4：turn_record 无 sources 字段（见 ROUTING_GAPS §4）"))
    import api_server

    rec = api_server.turn_record(
        ip="127.0.0.1", question="测试问题", elapsed=1.0,
        meta={"route": "agent", "game": "baldurs_gate3", "tool_calls": 1,
              "sources": ["Sentient Amulet", "Perception"]},
    )
    assert "sources" in rec, "turn_record 没把工具返回的来源落进记录"
    assert rec["sources"] == ["Sentient Amulet", "Perception"]
