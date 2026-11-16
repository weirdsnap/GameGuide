#!/usr/bin/env python3
"""剧透分级（L1/L2/L3）的单元测试。

口径见 docs/SPOILER_SEVERITY.md：L1 地图（地名/道具名/机制存在性）、
L2 攻略（解法/数值/触发条件）、L3 谜底（反转/重大抉择后果/角色命运/结局）。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from api_server import SPOILER_TERMS, spoiler_hits, spoiler_over, turn_record  # noqa: E402


# ── 词表自检：格式与等级都要合法 ──────────────────────────────

def test_term_table_shape():
    for game, terms in SPOILER_TERMS.items():
        for item in terms:
            assert len(item) == 3, f"{game}: 条目应为 (词, act, level)，实际 {item}"
            term, act, level = item
            assert isinstance(term, str) and term, f"{game}: 词不能为空"
            assert int(act) >= 1, f"{game}/{term}: act 必须 >= 1"
            assert level in (1, 2, 3), f"{game}/{term}: level 只能是 1/2/3，实际 {level}"


def test_no_single_char_terms():
    """单字剧透词会把正常用法也判成剧透（例如「主」）。"""
    bad = [(g, t) for g, terms in SPOILER_TERMS.items() for t, _, _ in terms if len(t) == 1]
    assert not bad, f"出现单字剧透词：{bad}"


# ── 分级判定 ─────────────────────────────────────────────────

@pytest.mark.parametrize("answer,term,level", [
    ("它位于第三章下城区（Lower City）", "下城区", 1),
    ("利文顿是个村子", "利文顿", 1),
    ("月出之塔在第二章", "月出之塔", 1),
    ("终局是主脑（Netherbrain）", "主脑", 2),
    ("巴尔的神选", "巴尔", 2),
    ("夜之歌就是那位被囚禁的塞伦捏信徒", "夜之歌", 3),
])
def test_hits_carry_level(answer, term, level):
    hits = spoiler_hits("baldurs_gate3", answer)
    matched = {h["term"]: h for h in hits}
    assert term in matched, f"没命中 {term}，实际 {list(matched)}"
    assert matched[term]["level"] == level


def test_level_is_independent_of_act():
    """同属 act 3，地名是 L1，反派势力是 L2——等级不跟着幕数走。"""
    hits = {h["term"]: h for h in spoiler_hits("baldurs_gate3", "下城区有巴尔")}
    assert hits["下城区"]["act"] == 3 and hits["下城区"]["level"] == 1
    assert hits["巴尔"]["act"] == 3 and hits["巴尔"]["level"] == 2


def test_unknown_game_or_empty_answer():
    assert spoiler_hits("terraria", "月出之塔") == []
    assert spoiler_hits("baldurs_gate3", "") == []
    assert spoiler_hits(None, "下城区") == []


# ── 阈值筛选：默认只放过 L1 ───────────────────────────────────

def test_spoiler_over_default_allows_l1_only():
    hits = spoiler_hits("baldurs_gate3", "下城区、主脑、夜之歌")
    over = spoiler_over(hits, 1)
    assert {h["term"] for h in over} == {"主脑", "夜之歌"}
    assert all(h["level"] > 1 for h in over)


def test_spoiler_over_max_level_3_is_empty():
    hits = spoiler_hits("baldurs_gate3", "下城区、主脑、夜之歌")
    assert spoiler_over(hits, 3) == []


def test_spoiler_over_none_safe():
    assert spoiler_over([], 1) == []


# ── turn_record：用户报过进度时，L3 单独打标，方便日报优先抽检 ──

def _record(answer: str) -> dict:
    return turn_record(ip="1.1.1.1", question="q", elapsed=1.0,
                       meta={"game": "baldurs_gate3", "route": "agent"},
                       answer=answer)


def test_turn_record_marks_worst_level():
    rec = _record("下城区有巴尔，还有夜之歌")
    assert sorted(h["level"] for h in rec["spoiler_hits"]) == [1, 2, 3]
    assert rec["spoiler_worst"] == 3


def test_turn_record_l1_only_has_no_worst_marker():
    rec = _record("它位于第三章下城区")
    assert rec.get("spoiler_hits")
    assert "spoiler_worst" not in rec


def test_turn_record_clean_answer():
    rec = _record("这只怪弱火")
    assert "spoiler_hits" not in rec
