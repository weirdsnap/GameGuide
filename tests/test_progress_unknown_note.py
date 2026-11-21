#!/usr/bin/env python3
"""进度推断的默认值契约（ISSUES_2026-11-22 I-14「模型总像默认我在第二章」）。

两个成因：
1. 进度未知时 `progress_prompt_note` 返回空串，提示词对章节零约束，模型按先验往中期猜；
2. 进度单调取 max + 历史全量，历史里说过「第二章」就永久钉在 2。
   （第 2 条是防剧透的基座，不改；只要求第 1 条补一句防护。）
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from rag_agent.progress import infer_progress, progress_prompt_note  # noqa: E402


QUESTIONS = [
    "山丘巨人的铁手套是什么",
    "拉玛吉斯之塔怎么进去",
    "这游戏有多少个伙伴",
]


# ── 现状行为：必须固化的部分（现在就应通过）───────────────────

@pytest.mark.parametrize("q", QUESTIONS)
def test_unknown_progress_is_none(q):
    """问题里不含章/幕说法与地名锚点时不推断进度（不得默认成 2）。"""
    progress = infer_progress("baldurs_gate3", q)
    assert progress.level is None, f"「{q}」被推断成第 {progress.level} 章"


def test_history_pins_progress_monotonically():
    """历史里明说第二章就锁定 2，明说第三章取更大的 3（单调不减）。"""
    history = [{"role": "user", "content": "我现在在第二章了"}]
    assert infer_progress("baldurs_gate3", "这件装备在哪", history).level == 2

    history.append({"role": "user", "content": "我已经到下城区了"})
    assert infer_progress("baldurs_gate3", "这件装备在哪", history).level == 3


# ── 期望行为：补齐前 xfail（ISSUES_2026-11-22 I-14 方案）────────

_NOTE_EMPTY = not progress_prompt_note(
    infer_progress("baldurs_gate3", "山丘巨人的铁手套是什么")
).strip()


@pytest.mark.xfail(condition=_NOTE_EMPTY, strict=False,
                   reason="未知进度时提示词为空串，模型自由猜章节（I-14）")
def test_unknown_progress_note_forbids_guess():
    """未知进度时必须注入一句「不要替用户假设章节」，而不是什么都不说。"""
    note = progress_prompt_note(infer_progress("baldurs_gate3", "山丘巨人的铁手套是什么"))
    assert note.strip(), "未知进度时提示词为空，没有任何约束"
    assert ("不要" in note or "不得" in note), f"未知进度提示句没约束猜测：{note!r}"
