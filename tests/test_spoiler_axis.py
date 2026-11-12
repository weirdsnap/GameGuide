"""防剧透 P0 契约测试（离线）。

锁定 2026-11-12 防剧透改造（docs/SPOILER_CONTROL_PLAN.md）的提示词层约定：
  - 每个游戏按 spoiler_axis 决定追问方式（act/chapter/ability/none）
  - 「进度即硬上界」「别剧透先复述边界」等核心规则必须存在
  - BG3 进度信号词（最后之光旅店/塞伦涅/塞伦捏）必须能路由到 baldurs_gate3
    （线上实测：用户报「卡在最后之光旅店」时 detect_game 返回 None，弹了菜单）
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rag_agent.game_router import detect_game, build_common_rules
from rag_agent.game_registry import GAME_REGISTRY


def test_所有游戏都标注了spoiler_axis():
    for key, cfg in GAME_REGISTRY.items():
        assert cfg.get("spoiler_axis") in ("act", "chapter", "ability", "none"), key


@pytest.mark.parametrize("game,expect", [
    ("baldurs_gate3", "第几章/哪个区域"),
    ("cyberpunk2077", "第几天/第几章"),
    ("va11halla", "第几天/第几章"),
    ("hollow_knight", "已获得的能力"),
    ("silksong", "已获得的能力"),
    ("terraria", "已获得的能力"),
])
def test_进度敏感游戏有对应追问(game, expect):
    assert expect in build_common_rules(game)


@pytest.mark.parametrize("game", ["oni", "mhw"])
def test_进度不敏感游戏不追问(game):
    assert "先追问" not in build_common_rules(game)


def test_核心剧透规则与游戏无关():
    rules = build_common_rules("baldurs_gate3")
    assert "硬上界" in rules          # 报过进度后不得越过，也不重复追问
    assert "复述边界" in rules         # 用户说别剧透 → 先复述边界再答
    assert "占位提示" in rules         # 越界内容只给占位，不给名字不给结果


def test_无游戏时通用规则不含追问():
    assert "先追问" not in build_common_rules()


@pytest.mark.parametrize("query", [
    "我卡在最后之光旅店，这里有什么任务",
    "塞伦捏的秘密任务怎么做",
    "塞伦涅是谁",
])
def test_BG3进度信号词路由(query):
    game, conf = detect_game(query)
    assert game == "baldurs_gate3", f"{query} → {game} ({conf})"
