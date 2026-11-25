"""act_index 单元测试（防剧透 P1 检索侧兜底：标题 → act 映射）。

全部离线：monkeypatch 一个指向临时 SQLite 的游戏配置，
DB/表/字段缺失时一律退化为空映射（永不抛错）是核心契约。

注意：三个入口函数都带 lru_cache，每个用例前后必须清缓存，
否则测试间互相污染。
"""

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rag_agent import act_index  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_caches():
    for fn in (act_index.act_by_title, act_index._long_names, act_index.title_act):
        fn.cache_clear()
    yield
    for fn in (act_index.act_by_title, act_index._long_names, act_index.title_act):
        fn.cache_clear()


def _mk_db(tmp_path, locations=(), quests=(), name="test_data.db"):
    db = tmp_path / name
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE locations (name TEXT, act INTEGER)")
    conn.execute("CREATE TABLE quests (name TEXT, act INTEGER)")
    conn.executemany("INSERT INTO locations VALUES (?, ?)", locations)
    conn.executemany("INSERT INTO quests VALUES (?, ?)", quests)
    conn.commit()
    conn.close()
    return db


def _patch_game(monkeypatch, db):
    monkeypatch.setitem(act_index.AVAILABLE_GAMES, "test_game",
                        {"db_path": str(db)})


# ── 退化路径：永不抛错 ──

def test_未知游戏返回空映射():
    assert act_index.act_by_title("no_such_game") == {}
    assert act_index.title_act("no_such_game", "Anything") is None


def test_db_文件不存在返回空映射(monkeypatch, tmp_path):
    monkeypatch.setitem(act_index.AVAILABLE_GAMES, "test_game",
                        {"db_path": str(tmp_path / "missing.db")})
    assert act_index.act_by_title("test_game") == {}


def test_表不存在时跳过(monkeypatch, tmp_path):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (x TEXT)")
    conn.commit()
    conn.close()
    _patch_game(monkeypatch, db)
    assert act_index.act_by_title("test_game") == {}


# ── 映射构建 ──

def test_locations_和_quests_都进映射(monkeypatch, tmp_path):
    _patch_game(monkeypatch, _mk_db(
        tmp_path,
        locations=[("Last Light Inn", 2)],
        quests=[("Find the Nightsong", 2)],
    ))
    mapping = act_index.act_by_title("test_game")
    assert mapping == {"last light inn": 2, "find the nightsong": 2}


def test_act_为空的行被跳过(monkeypatch, tmp_path):
    _patch_game(monkeypatch, _mk_db(
        tmp_path, locations=[("有Act", 1), ("没Act", None), ("", 2)]))
    mapping = act_index.act_by_title("test_game")
    assert "没act" not in mapping and "" not in mapping
    assert mapping == {"有act": 1}


# ── title_act 查询语义 ──

def test_精确匹配忽略大小写(monkeypatch, tmp_path):
    _patch_game(monkeypatch, _mk_db(tmp_path, locations=[("Last Light Inn", 2)]))
    assert act_index.title_act("test_game", "last light inn") == 2
    assert act_index.title_act("test_game", "  LAST LIGHT INN ") == 2


def test_标题为空返回None():
    assert act_index.title_act("test_game", None) is None
    assert act_index.title_act("test_game", "") is None


def test_长名称包含匹配_复合标题(monkeypatch, tmp_path):
    """Lower City 在 DB 里，「Lower City and Upper City」这种复合标题也能命中。"""
    _patch_game(monkeypatch, _mk_db(tmp_path, locations=[("Lower City", 3)]))
    assert act_index.title_act("test_game", "Lower City and Upper City") == 3
    assert act_index.title_act("test_game", "Civilian (Lower City)") == 3


def test_短词不做包含匹配防误伤(monkeypatch, tmp_path):
    """英文 <6 字符的名称太通用（如 Water），只允许精确匹配。"""
    _patch_game(monkeypatch, _mk_db(tmp_path, locations=[("Water", 1)]))
    assert act_index.title_act("test_game", "Water") == 1            # 精确可以
    assert act_index.title_act("test_game", "Water Temple") is None  # 包含不行


def test_中文3字可做包含匹配(monkeypatch, tmp_path):
    _patch_game(monkeypatch, _mk_db(tmp_path, locations=[("下城区", 3)]))
    assert act_index.title_act("test_game", "下城区入口") == 3
    # 2 字中文太短，不做包含匹配
    _patch_game(monkeypatch, _mk_db(tmp_path, locations=[("王城", 1)],
                                    name="test_data2.db"))
    act_index.act_by_title.cache_clear()  # 换 DB 后重算
    act_index._long_names.cache_clear()
    assert act_index.title_act("test_game", "王城下水道") is None
    assert act_index.title_act("test_game", "王城") == 1


def test_未命中返回None(monkeypatch, tmp_path):
    _patch_game(monkeypatch, _mk_db(tmp_path, locations=[("Last Light Inn", 2)]))
    assert act_index.title_act("test_game", "Moonrise Towers") is None
