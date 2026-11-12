"""术语表 query 扩展的离线单测（monkeypatch 数据，不读真实 glossary 文件）。

覆盖 glossary.expand_query 的匹配策略：
  - 最长优先 + 区间遮挡（奥利弗 不被 奥利 截断）
  - 英文原名已在问句里时不重复追加
  - 多个中文名映射同一英文时只追加一次
  - 高/中置信收录条件与 dropped_terms 诊断
  - 单字中文、空问句等边界
"""

import pytest

from rag_agent import glossary

ROWS = (
    {"name_en": "Oliver", "name_zh": "奥利弗", "conf": 0.90, "wiki_freq": 100},
    {"name_en": "Ollie", "name_zh": "奥利", "conf": 0.90, "wiki_freq": 50},
    {"name_en": "Shadowheart", "name_zh": "影心", "conf": 0.98, "wiki_freq": 1000},
    {"name_en": "Shadowheart", "name_zh": "暗心", "conf": 0.80, "wiki_freq": 10},
    {"name_en": "Rare Thing", "name_zh": "稀有物", "conf": 0.40, "wiki_freq": 9999},
    {"name_en": "Freq Thing", "name_zh": "高频物", "conf": 0.55, "wiki_freq": 600},
    {"name_en": "X", "name_zh": "甲", "conf": 0.99, "wiki_freq": 100},
)


@pytest.fixture(autouse=True)
def fake_glossary(monkeypatch):
    monkeypatch.setattr(glossary, "load_glossary", lambda game: ROWS)
    glossary._zh_index.cache_clear()
    yield
    glossary._zh_index.cache_clear()


def test_longest_match_wins():
    expanded, hits = glossary.expand_query("怎么打奥利弗", "fake_game")

    assert [h[1] for h in hits] == ["Oliver"]
    assert expanded == "怎么打奥利弗 (Oliver)"


def test_masking_allows_second_real_hit():
    expanded, hits = glossary.expand_query("奥利弗和奥利", "fake_game")

    assert [h[0] for h in hits] == ["奥利弗", "奥利"]
    assert expanded.endswith("(Oliver Ollie)")


def test_english_name_already_present_is_not_duplicated():
    expanded, hits = glossary.expand_query(
        "Shadowheart 喜欢什么，影心", "fake_game"
    )

    assert hits == []
    assert expanded == "Shadowheart 喜欢什么，影心"


def test_same_english_added_only_once():
    _expanded, hits = glossary.expand_query("影心和暗心", "fake_game")

    assert [h[0] for h in hits] == ["影心"]
    assert [h[1] for h in hits] == ["Shadowheart"]


def test_low_conf_blocked_and_reported():
    expanded, hits = glossary.expand_query("稀有物在哪", "fake_game")

    assert hits == []
    assert expanded == "稀有物在哪"
    dropped = glossary.dropped_terms("稀有物在哪", "fake_game")
    assert [d[0] for d in dropped] == ["稀有物"]


def test_mid_conf_passes_with_wiki_freq():
    expanded, hits = glossary.expand_query("高频物", "fake_game")

    assert [h[1] for h in hits] == ["Freq Thing"]
    assert expanded == "高频物 (Freq Thing)"


def test_single_char_zh_is_ignored():
    _expanded, hits = glossary.expand_query("甲", "fake_game")
    assert hits == []


def test_empty_query_unchanged():
    assert glossary.expand_query("", "fake_game") == ("", [])
