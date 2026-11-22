"""refetch_wiki.py 解析器契约测试。

对应 docs/SERVER_SIDE_TASKS.md 的 P0-2：fandom 新版页面有两个盲区
（portable-infobox 未被剥离、跨页共享模板的幽灵文本残留），不修就不能整站抓。
服务器侧连不上 fandom，只能靠合成 HTML 片段锁定剥离行为。
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "refetch_wiki", ROOT / "scripts" / "fetch" / "refetch_wiki.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rw = _load()


def wrap(body: str) -> str:
    return f'<div class="mw-parser-output">{body}</div>'


# ── 盲区 1：Fandom 便携信息框 ──
def test_portable_infobox_stripped():
    html = wrap(
        '<aside class="portable-infobox pi-background pi-theme-boss">'
        '<h2 class="pi-item pi-title">Hornet</h2>'
        '<div class="pi-item pi-data"><div class="pi-data-label">Health</div>'
        '<div class="pi-data-value">INFONBOX-LEAK-900</div></div>'
        "</aside>"
        "<p>Hornet guards the way out of Greenpath.</p>"
    )
    out = rw.parse_html(html)
    assert "INFONBOX-LEAK-900" not in out
    assert "Health" not in out
    assert "Hornet guards the way out of Greenpath." in out


def test_pi_prefix_div_stripped():
    html = wrap(
        '<div class="pi-section pi-section-datagroup">PI-SECTION-LEAK</div>'
        '<div class="pi-group pi-collapse">PI-GROUP-LEAK</div>'
        "<p>Real body text stays here.</p>"
    )
    out = rw.parse_html(html)
    assert "PI-SECTION-LEAK" not in out
    assert "PI-GROUP-LEAK" not in out
    assert "Real body text stays here." in out


def test_plain_aside_stripped():
    html = wrap(
        "<aside>ASIDE-LEAK side card</aside>"
        "<p>Body paragraph that should survive.</p>"
    )
    out = rw.parse_html(html)
    assert "ASIDE-LEAK" not in out
    assert "Body paragraph that should survive." in out


# ── 老盲区：navbox 表格变体名 ──
def test_navbox_variant_class_stripped():
    html = wrap(
        '<table class="navbox-small">'
        "<tr><td>NAVBOX-LEAK bosses of the region</td></tr></table>"
        "<p>Surviving line.</p>"
    )
    out = rw.parse_html(html)
    assert "NAVBOX-LEAK" not in out
    assert "Surviving line." in out


def test_wikitable_kept():
    html = wrap(
        '<table class="wikitable"><tr><th>Charm</th><th>Cost</th></tr>'
        "<tr><td>Wayward Compass</td><td>1</td></tr></table>"
        "<p>Table intro.</p>"
    )
    out = rw.parse_html(html)
    assert "Wayward Compass" in out
    assert "Table intro." in out


# ── 盲区 2：跨页共享模板（幽灵文本） ──
def _pages_with_shared(shared_n=4, total=8):
    shared = ("This navigation box lists every boss, area and item in the "
              "region, use it to jump between pages quickly.")
    pages = {}
    for i in range(total):
        own = f"Page {i} unique body paragraph about topic number {i}."
        body = f"{own}\n\n{shared}" if i < shared_n else own
        pages[f"Page {i}"] = body
    return pages, shared


def test_strip_shared_paragraphs():
    pages, shared = _pages_with_shared()
    out, stats = rw.strip_shared_paragraphs(pages)
    assert stats["dropped_shared"] == 4
    for body in out.values():
        assert shared not in body
    assert "Page 0 unique body paragraph" in out["Page 0"]
    assert out["Page 7"].strip()


def test_short_shared_text_not_stripped():
    """短句重复（如分类行）不在清理范围，避免误删正文。"""
    pages = {f"p{i}": "Short.\n\nUnique paragraph number %d here." % i for i in range(6)}
    out, stats = rw.strip_shared_paragraphs(pages)
    assert stats["dropped_shared"] == 0
    assert out["p0"].startswith("Short.")


def test_no_strip_switch_keeps_content():
    pages, shared = _pages_with_shared()
    out, stats = rw.strip_shared_paragraphs(pages, navlines=False, min_pages=99)
    assert stats["dropped_shared"] == 0
    assert shared in out["Page 0"]


# ── 导航表识别 ──
def test_navline_table_detected():
    para = "\n".join(f"| Boss {c}" for c in "ABCDEFGHIJKL")
    assert rw.looks_like_nav_table(para)


def test_stat_table_not_misdetected():
    """带数值的表格是正文数据，不能当导航删。"""
    para = "\n".join(f"| Charm {c} | 120 geo | 3 slots" for c in "ABCDEFGHIJKL")
    assert not rw.looks_like_nav_table(para)


def test_short_para_not_navline():
    assert not rw.looks_like_nav_table("| Boss A\n| Boss B")


# ── 闸门 ──
def test_shared_para_rate_after_strip():
    pages, _ = _pages_with_shared()
    assert rw.shared_para_rate(pages) > 0
    out, _ = rw.strip_shared_paragraphs(pages)
    assert rw.shared_para_rate(out) == 0


def _on_topic_pages(n=10):
    pages = {}
    for i in range(n):
        title = f"Boss Number{i}"
        pages[title] = (f"Number{i} is a boss found in area {i}. "
                        f"The fight with Number{i} has three phases.")
    return pages


def test_audit_title_in_body_rate_pass():
    report = rw.audit(_on_topic_pages(), zh=False)
    assert report["checks"]["title_in_body_rate"][0] == 1.0
    assert report["checks"]["title_in_body_rate"][1] is True


def test_audit_flags_offtopic_body():
    """正文串页（讲的是别的东西）必须被闸门抓住。"""
    pages = {}
    for i in range(10):
        pages[f"Boss Number{i}"] = (
            "The game was announced in 2019 and released on multiple platforms "
            "after a long delay, with several patches following the launch.")
    report = rw.audit(pages, zh=False)
    assert report["checks"]["title_in_body_rate"][0] == 0.0
    assert report["checks"]["title_in_body_rate"][1] is False
    assert report["ok"] is False
