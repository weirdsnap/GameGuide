"""游戏工具契约测试（离线：mock 向量库 + 临时 SQLite，不依赖 LLM / FAISS）。

覆盖：
  - _resolve_game_key 游戏名解析
  - 三个 @tool 的参数校验与核心分支
  - game_registry 注册表数据卫生（signals 重复 / 撞词 lint）
"""

import re
import sqlite3

import pytest
from langchain_core.documents import Document

from rag_agent.game_registry import GAME_REGISTRY
from rag_agent.game_router import AVAILABLE_GAMES
from rag_agent.tools import (
    _resolve_game_key,
    query_structured_data,
    search_knowledge_base,
    show_database_schema,
)


# ══════════════════════════════════════════════
#  _resolve_game_key
# ══════════════════════════════════════════════

def test_resolve_内部键原样命中():
    assert _resolve_game_key("hollow_knight") == "hollow_knight"


def test_resolve_显示名命中且容错大小写空格():
    assert _resolve_game_key("  Hollow Knight ") == "hollow_knight"
    assert _resolve_game_key("oxygen not included") == "oni"
    assert _resolve_game_key("泰拉瑞亚") == "terraria"


def test_resolve_未知游戏返回None():
    assert _resolve_game_key("塞尔达传说") is None
    assert _resolve_game_key("") is None


# ══════════════════════════════════════════════
#  公共 fixture：假游戏 + 临时 SQLite
# ══════════════════════════════════════════════

@pytest.fixture
def fake_game(tmp_path, monkeypatch):
    """注册一个指向临时 SQLite 的假游戏 test_game，返回 db 路径。"""
    db_path = tmp_path / "test_game.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE bosses (name TEXT, hp INTEGER, description TEXT)")
    conn.execute(
        "INSERT INTO bosses VALUES ('螳螂领主', 500, '镇守真菌荒地的领袖')")
    conn.execute(
        "INSERT INTO bosses VALUES ('辐光', 1000, '被遗忘的光')")
    conn.commit()
    conn.close()

    monkeypatch.setitem(AVAILABLE_GAMES, "test_game", {
        "name": "Test Game (测试游戏)",
        "db_path": str(db_path),
        "vectorstore_dir": str(tmp_path / "vs_不存在"),
    })
    return str(db_path)


# ══════════════════════════════════════════════
#  search_knowledge_base
# ══════════════════════════════════════════════

class _FakeVS:
    """模拟向量库：按 language filter 返回预置文档（严格模式——缺 language 的文档必然不中）。"""

    def __init__(self, docs):
        self._docs = docs
        self.queries = []          # 记录实际收到的 query，供断言术语扩展生效

    def similarity_search(self, query, k=8, filter=None, fetch_k=None):
        self.queries.append(query)
        if callable(filter):
            return [d for d in self._docs if filter(d.metadata)][:k]
        lang = (filter or {}).get("language")
        return [d for d in self._docs
                if d.metadata.get("language") == lang][:k]


def _patch_vs(monkeypatch, docs):
    monkeypatch.setitem(AVAILABLE_GAMES, "test_game", {
        "name": "Test Game (测试游戏)",
        "db_path": ":memory:",
        "vectorstore_dir": "/nonexistent/vs",  # 走 load_vectorstore 分支前会被 patch
    })
    # 让 vs_ok 检查通过 + load_vectorstore 返回假库
    monkeypatch.setattr("rag_agent.tools.os.path.isdir", lambda p: True)
    monkeypatch.setattr("rag_agent.tools.os.path.isfile", lambda p: True)
    vs = _FakeVS(docs)
    monkeypatch.setattr(
        "rag_agent.tools.load_vectorstore", lambda save_dir: vs)
    return vs


def test_search_无效游戏返回参数错误():
    out = search_knowledge_base.invoke({"query": "螳螂", "game": "塞尔达"})
    assert out.startswith("[参数错误]")


def test_search_向量库缺失返回不可用(fake_game):
    out = search_knowledge_base.invoke({"query": "螳螂", "game": "test_game"})
    assert "[知识库暂时不可用]" in out


def test_search_合并双语结果并按内容去重(monkeypatch):
    dup = "Mantis Lords guard the Fungal Wastes."
    docs = [
        Document(page_content=dup, metadata={"language": "en", "source": "wiki/bosses"}),
        Document(page_content=dup, metadata={"language": "zh", "source": "wiki/首领"}),
        Document(page_content="螳螂领主镇守真菌荒地。", metadata={"language": "zh"}),
    ]
    _patch_vs(monkeypatch, docs)

    out = search_knowledge_base.invoke({"query": "mantis", "game": "test_game"})

    assert "【参考 1】" in out and "【参考 2】" in out
    assert "【参考 3】" not in out          # 同内容 en/zh 已去重
    assert "[EN] wiki/bosses" in out        # 语言标签 + source 格式


def test_search_空结果给出明确提示(monkeypatch):
    _patch_vs(monkeypatch, [])
    out = search_knowledge_base.invoke({"query": "不存在的东西", "game": "test_game"})
    assert "知识库未找到" in out


def test_search_严格模式_缺language文档必然不中(monkeypatch):
    """锁住契约：读端无兜底，缺 language 的文档等于不存在（2026-11-12 定）。"""
    docs = [
        Document(page_content="No language tag at all.", metadata={"source": "old_lib"}),
    ]
    _patch_vs(monkeypatch, docs)
    out = search_knowledge_base.invoke({"query": "language", "game": "test_game"})
    assert "知识库未找到" in out


def test_search_术语扩展后query进检索(monkeypatch):
    """命中术语表时，扩展后的 query（追加英文原名）才是真正发给向量库的。"""
    docs = [Document(page_content="Shadowheart is a half-elf cleric.",
                     metadata={"language": "en", "source": "wiki/Shadowheart"})]
    vs = _patch_vs(monkeypatch, docs)
    monkeypatch.setattr("rag_agent.tools.expand_query",
                        lambda q, g: (f"{q} (Shadowheart)", [("影心", "Shadowheart", 0.95, 800)]))

    out = search_knowledge_base.invoke({"query": "影心是谁", "game": "test_game"})

    assert vs.queries and all("Shadowheart" in q for q in vs.queries)
    assert "Shadowheart" in out


def test_search_术语表异常静默回退原query(monkeypatch):
    """术语表坏了（KeyError/JSON 错误等）不能拖垮检索，静默用原 query。"""
    docs = [Document(page_content="mantis", metadata={"language": "en"})]
    vs = _patch_vs(monkeypatch, docs)

    def _boom(q, g):
        raise KeyError("dir")

    monkeypatch.setattr("rag_agent.tools.expand_query", _boom)
    out = search_knowledge_base.invoke({"query": "mantis", "game": "test_game"})
    assert vs.queries == ["mantis", "mantis"]   # en/zh 两路都用原 query
    assert "【参考 1】" in out


def test_search_max_act过滤越界内容(monkeypatch):
    """防剧透：max_act=1 时 act=2/3 的文档被过滤，缺 act 的文档保留。"""
    docs = [
        Document(page_content="Act 1 area.", metadata={"language": "en", "act": 1}),
        Document(page_content="Act 2 spoiler.", metadata={"language": "en", "act": 2}),
        Document(page_content="Act 3 spoiler.", metadata={"language": "en", "act": 3}),
        Document(page_content="设定类词条，无幕归属。", metadata={"language": "en"}),
    ]
    _patch_vs(monkeypatch, docs)
    out = search_knowledge_base.invoke(
        {"query": "area", "game": "test_game", "max_act": 1})
    assert "Act 1 area" in out
    assert "设定类词条" in out            # 缺 act 的放行
    assert "Act 2 spoiler" not in out
    assert "Act 3 spoiler" not in out


# ══════════════════════════════════════════════
#  query_structured_data
# ══════════════════════════════════════════════

def test_query_无效游戏返回参数错误():
    out = query_structured_data.invoke({"query": "查询 X", "game": "塞尔达"})
    assert out.startswith("[参数错误]")


def test_query_查询前缀命中实体(fake_game):
    out = query_structured_data.invoke({"query": "查询 螳螂领主", "game": "test_game"})
    assert "未找到" not in out
    assert "[bosses] 螳螂领主" in out


def test_query_所有列出整表(fake_game):
    out = query_structured_data.invoke({"query": "所有 bosses", "game": "test_game"})
    assert "螳螂领主" in out and "辐光" in out


def test_query_数值筛选分支(fake_game):
    out = query_structured_data.invoke({"query": "hp 800", "game": "test_game"})
    assert "筛选 ≥800" in out
    assert "辐光" in out            # hp=1000 ≥ 800
    assert "螳螂领主" not in out    # hp=500 < 800


def test_query_未命中返回明确提示(fake_game):
    out = query_structured_data.invoke(
        {"query": "查询 完全不存在的玩意", "game": "test_game"})
    assert "未找到" in out


def test_query_BG3中文名映射为英文(tmp_path, monkeypatch):
    """博德之门3 的「查询 阿斯代伦」应映射为 Astarion 再查库。"""
    db_path = tmp_path / "bg3.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE characters (name TEXT, description TEXT)")
    conn.execute("INSERT INTO characters VALUES ('Astarion', '吸血鬼衍体')")
    conn.commit()
    conn.close()

    monkeypatch.setitem(AVAILABLE_GAMES, "baldurs_gate3", {
        **AVAILABLE_GAMES["baldurs_gate3"], "db_path": str(db_path)})

    out = query_structured_data.invoke(
        {"query": "查询 阿斯代伦", "game": "baldurs_gate3"})
    assert "未找到" not in out
    assert "Astarion" in out


# ══════════════════════════════════════════════
#  show_database_schema
# ══════════════════════════════════════════════

def test_schema_无效游戏返回参数错误():
    out = show_database_schema.invoke({"game": "塞尔达"})
    assert out.startswith("[参数错误]")


def test_schema_列出表名列名行数(fake_game):
    out = show_database_schema.invoke({"game": "test_game"})
    assert "bosses" in out
    assert "2 rows" in out
    assert "Columns:" in out and "hp" in out


# ══════════════════════════════════════════════
#  注册表 lint（新增游戏时的数据卫生防线）
# ══════════════════════════════════════════════

# 有意的跨游戏共享信号词：黄蜂（Hornet）是空洞骑士与丝之歌两作共有角色。
# 新增撞词不在此列即 fail——加游戏时请检查是否有意共享。
KNOWN_SHARED_SIGNALS = {"hornet"}


def test_registry_游戏内signals无重复():
    for key, cfg in GAME_REGISTRY.items():
        seen, dups = set(), set()
        for s in cfg["signals"]:
            sl = s.lower()
            if sl in seen:
                dups.add(s)
            seen.add(sl)
        assert not dups, f"{key} 的 signals 有重复项：{sorted(dups)}"


def test_registry_跨游戏signals无意外撞词():
    owners = {}
    for key, cfg in GAME_REGISTRY.items():
        for s in cfg["signals"]:
            owners.setdefault(s.lower(), set()).add(key)
    collisions = {
        sig: keys for sig, keys in owners.items()
        if len(keys) > 1 and sig not in KNOWN_SHARED_SIGNALS
    }
    assert not collisions, (
        f"跨游戏撞词（若属有意共享请加入 KNOWN_SHARED_SIGNALS）：{collisions}")


def test_registry_exact_patterns均可编译():
    for key, cfg in GAME_REGISTRY.items():
        for pat in cfg["exact_patterns"]:
            re.compile(pat)  # 不抛异常即通过


def test_registry_必备字段齐全():
    for key in GAME_REGISTRY:
        entry = AVAILABLE_GAMES[key]
        for field in ("name", "db_path", "vectorstore_dir"):
            assert entry.get(field), f"{key} 缺少 AVAILABLE_GAMES 字段 {field}"


def test_search_标题词面命中置顶(monkeypatch):
    """向量排序对实体名不稳（带撇号的 Lae'zel 实测被压到 rank 4），
    title 出现在 query 里的块必须置顶；组内保持原语义顺序。"""
    docs = [
        Document(page_content="语义更近的别的角色", metadata={"language": "en", "title": "Lorroakan"}),
        Document(page_content="目标条目", metadata={"language": "en", "title": "Lae'zel"}),
    ]
    _patch_vs(monkeypatch, docs)
    monkeypatch.setattr("rag_agent.tools.expand_query",
                        lambda q, g: ("Lae'zel 养鸡妹好用吗", []))
    out = search_knowledge_base.invoke({"query": "养鸡妹好用吗", "game": "test_game"})
    assert out.index("Lae'zel") < out.index("Lorroakan")


def test_search_术语命中标题直通置顶(monkeypatch):
    """术语扩展命中的英文名在库里有同标题文档时，即使语义检索没捞到也直通置顶。"""
    target = Document(page_content="Lae'zel 条目正文", metadata={"language": "en", "title": "Lae'zel"})
    other = Document(page_content="无关条目", metadata={"language": "en", "title": "Lorroakan"})
    vs = _patch_vs(monkeypatch, [other])       # 语义检索只返回无关条目
    vs.docstore = type("DS", (), {"_dict": {"1": target, "2": other}})()
    monkeypatch.setattr("rag_agent.tools.expand_query",
                        lambda q, g: ("Lae'zel 养鸡妹", [("养鸡妹", "Lae'zel", 0.98, 1003)]))

    out = search_knowledge_base.invoke({"query": "养鸡妹好用吗", "game": "test_game"})
    assert "【参考 1】[EN] Lae'zel" in out
