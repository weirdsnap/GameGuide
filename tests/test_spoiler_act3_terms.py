#!/usr/bin/env python3
"""BG3 act3 地名的词表契约。

背景（docs/ISSUES_2026-11-22.md I-10）：11-22 01:06 那轮答案写了「希望之邸」，
但 `SPOILER_TERMS` 里没有这个词，`spoiler_hits` 是空的，事后统计完全看不见这次泄露。
词表是审计层的唯一入口，漏一个地名就等于漏一整类越界。

等级口径：这些全是 L1（地名/区域名），知道也不损体验，但必须被记录。
词表补齐前本文件全部 xfail，补齐后自动变真守卫。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from api_server import SPOILER_TERMS, spoiler_hits  # noqa: E402


# (词, 期望 act, 期望 level)
REQUIRED = [
    ("House of Hope", 3, 1),
    ("希望之邸", 3, 1),
    ("Ramazith's Tower", 3, 1),
    ("拉玛吉斯之塔", 3, 1),
    ("Sorcerous Sundries", 3, 1),
    ("法术杂货铺", 3, 1),
]


def _present(term: str) -> bool:
    return any(t.lower() == term.lower()
               for t, _a, _lv in SPOILER_TERMS.get("baldurs_gate3", []))


pytestmark = pytest.mark.xfail(
    condition=not all(_present(t) for t, _, _ in REQUIRED),
    reason="SPOILER_TERMS 缺 act3 地名（ISSUES_2026-11-22 I-10 C 方案）",
    strict=False,
)


@pytest.mark.parametrize("term,act,level", REQUIRED)
def test_act3_place_in_table(term, act, level):
    """词表里要有，且 act/level 要对。"""
    entry = [e for e in SPOILER_TERMS["baldurs_gate3"] if e[0].lower() == term.lower()]
    assert entry, f"词表没有 {term}"
    _t, got_act, got_level = entry[0]
    assert (got_act, got_level) == (act, level), f"{term} 应为 act{act}/L{level}，实际 act{got_act}/L{got_level}"


@pytest.mark.parametrize("term,act,level", REQUIRED)
def test_act3_place_is_detected(term, act, level):
    """真实句子里能被 spoiler_hits 抽到（不是只躺在表里）。"""
    hits = {h["term"]: h for h in spoiler_hits("baldurs_gate3", f"这件装备就在{term}里面")}
    assert term in hits, f"没识别出 {term}"
    assert hits[term]["act"] == act and hits[term]["level"] == level
