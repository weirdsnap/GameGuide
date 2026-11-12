"""mac_build 建库契约测试（离线，不加载 fastembed / FAISS）。

背景：tools.search_knowledge_base 用 filter={"language": "en"/"zh"} 检索，
FAISS 会直接过滤掉缺 language 字段的文档。历史 bug：mac_build 建出的库
没有 language 元数据，线上检索恒为空，而裸 similarity_search 自检发现不了。

覆盖：
  - load_wiki_documents 是否给每篇文档打 language
  - 三种分页格式（# 文档：/ ## 标题 / # 标题）是否都能解析
  - collect_documents 是否合并中文 wiki + 结构化数据并统一打标
  - 缺 language 的文档是否会被守卫拦下
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_mac_build():
    spec = importlib.util.spec_from_file_location(
        "mac_build_for_test", ROOT / "scripts" / "tool" / "mac_build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mac_build = _load_mac_build()


@pytest.fixture
def fake_game(tmp_path, monkeypatch):
    """把 GAME_DATA 指向临时 wiki 文件，避免读真实 30MB 语料。"""
    en = tmp_path / "wiki_data.md"
    en.write_text("## Mantis Lords\n\nMantis Lords 正文\n", encoding="utf-8")
    zh = tmp_path / "wiki_data_zh.md"
    zh.write_text("## 螳螂领主\n\n中文正文\n", encoding="utf-8")
    monkeypatch.setitem(mac_build.GAME_DATA, "fake_game", {
        "name": "Fake Game",
        "data_path": str(en),
        "data_path_zh": str(zh),
        "vectorstore_dir": str(tmp_path / "vectorstore"),
    })
    return "fake_game"


def test_load_wiki_documents_tags_language(tmp_path):
    p = tmp_path / "wiki.md"
    p.write_text("## Alpha\n\ncontent\n", encoding="utf-8")

    en_docs = mac_build.load_wiki_documents(str(p))
    zh_docs = mac_build.load_wiki_documents(str(p), language="zh")

    assert en_docs and zh_docs
    assert all(d["metadata"]["language"] == "en" for d in en_docs)
    assert all(d["metadata"]["language"] == "zh" for d in zh_docs)


def test_load_wiki_documents_h1_format(tmp_path):
    # silksong / mhw 用 "# 标题" 分页；第一个 chunk 是文件级头部，要跳过
    p = tmp_path / "wiki.md"
    p.write_text(
        "# Silksong Wiki Data\n\n# Act 1\n\n正文一\n\n# Boss\n\n正文二\n",
        encoding="utf-8",
    )

    docs = mac_build.load_wiki_documents(str(p))

    assert [d["metadata"]["title"] for d in docs] == ["Act 1", "Boss"]
    assert all(d["metadata"]["language"] == "en" for d in docs)


def test_unrecognized_format_falls_back_to_single_doc(tmp_path):
    p = tmp_path / "wiki.md"
    p.write_text("没有任何标题的一段正文", encoding="utf-8")

    docs = mac_build.load_wiki_documents(str(p))

    assert len(docs) == 1
    assert docs[0]["content"] == "没有任何标题的一段正文"


def test_collect_documents_merges_zh_and_structured(fake_game, monkeypatch):
    monkeypatch.setattr(mac_build, "load_structured_data", lambda key: [
        {"content": "Boss\nHP: 100",
         "metadata": {"title": "Boss", "category": "struct/bosses"}},
    ])

    docs = mac_build.collect_documents(fake_game)
    langs = [d["metadata"]["language"] for d in docs]

    assert langs.count("en") == 2      # 英文 wiki + 结构化数据
    assert langs.count("zh") == 1      # 中文 wiki
    assert all(lang in ("en", "zh") for lang in langs)


def test_collect_documents_rejects_missing_language(fake_game, monkeypatch):
    # 模拟未来新增的数据源忘记打 language 的情况
    monkeypatch.setattr(
        mac_build, "load_wiki_documents",
        lambda path, language="en": [
            {"content": "no lang", "metadata": {"title": "Oops", "category": ""}},
        ],
    )
    monkeypatch.setattr(mac_build, "load_structured_data", lambda key: [])

    with pytest.raises(RuntimeError, match="language"):
        mac_build.collect_documents(fake_game)
