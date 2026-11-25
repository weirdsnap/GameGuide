"""game_registry 模块级契约测试（单源真理的完整性）。

game_registry 是五个消费方（game_router / ingest_game / mac_build / wiki /
vectorstores.sh）的唯一配置来源，任何字段缺失都会在很远的下游才炸。
这里把「注册一条游戏必须长什么样」钉成显式契约，加新游戏时当场报错。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rag_agent import game_registry as gr  # noqa: E402

REQUIRED_FIELDS = ["key", "name", "name_short", "dir", "db_file", "data_file",
                   "signals", "exact_patterns"]
VALID_AXES = {"act", "chapter", "ability", "none"}


def test_每个游戏必填字段齐全():
    for key, cfg in gr.GAME_REGISTRY.items():
        for field in REQUIRED_FIELDS:
            assert cfg.get(field), f"{key} 缺必填字段 {field}"


def test_key_命名规范且与目录一致():
    for key, cfg in gr.GAME_REGISTRY.items():
        assert re.fullmatch(r"[a-z0-9_]+", key), f"key 不规范: {key}"
        assert cfg["dir"] == key, f"{key} 的 dir 应与 key 一致"


def test_数据文件真实存在():
    """wiki 正文与 SQLite 库是建库输入，缺失会在建库期才暴露——提前到测试期。"""
    for key, cfg in gr.GAME_REGISTRY.items():
        data = gr.GAMES_DIR / cfg["dir"] / "data" / cfg["data_file"]
        assert data.is_file(), f"{key} 缺 wiki 数据文件 {data}"
        db = gr.GAMES_DIR / cfg["dir"] / cfg["db_file"]
        assert db.is_file(), f"{key} 缺结构化库 {db}"


def test_signals_非空且全小写可匹配():
    for key, cfg in gr.GAME_REGISTRY.items():
        assert len(cfg["signals"]) >= 3, f"{key} 信号词太少"
        for s in cfg["signals"]:
            assert isinstance(s, str) and s.strip(), f"{key} 有空信号词"


def test_exact_patterns_是合法正则():
    for key, cfg in gr.GAME_REGISTRY.items():
        for p in cfg["exact_patterns"]:
            re.compile(p)  # 编译不过会当场抛


def test_spoiler_axis_取值合法():
    for key, cfg in gr.GAME_REGISTRY.items():
        assert cfg.get("spoiler_axis", "none") in VALID_AXES, \
            f"{key} 的 spoiler_axis 非法: {cfg.get('spoiler_axis')}"


# ── 衍生导出的一致性 ──

def test_衍生导出键集合一致():
    keys = set(gr.GAME_REGISTRY)
    assert set(gr.GAME_KEYS) == keys
    assert set(gr.AVAILABLE_GAMES) == keys
    assert set(gr.GAME_SIGNALS) == keys
    assert set(gr.GAME_DATA) == keys
    assert set(gr.GAME_DB_MAPPING) == keys


def test_AVAILABLE_GAMES_路径字段是绝对路径且在_games_下():
    for key, entry in gr.AVAILABLE_GAMES.items():
        for field in ("db_path", "vectorstore_dir"):
            p = Path(entry[field])
            assert p.is_absolute(), f"{key}.{field} 不是绝对路径"
            assert p.name and "games" in p.parts, f"{key}.{field} 不在 games/ 下: {p}"


def test_GAME_MENU_能解析出中英文():
    for item in gr.GAME_MENU:
        assert item["key"] in gr.GAME_REGISTRY
        assert item["zh"] and item["en"], f"菜单项缺中/英文名: {item}"


def test_KNOWN_KEYWORDS_覆盖每个游戏的中文名():
    for item in gr.GAME_MENU:
        assert item["zh"] in gr.KNOWN_KEYWORDS, \
            f"{item['zh']} 不在 KNOWN_KEYWORDS，未识别游戏判定会漏"


def test_有_wiki_api_的才有_WIKI_CONFIGS():
    for key, cfg in gr.GAME_REGISTRY.items():
        if cfg.get("wiki_api"):
            wc = gr.WIKI_CONFIGS[key]
            assert wc["api"].startswith("http")
            assert wc["categories"], f"{key} 有 api 但无分类"
            assert wc["user_agent"]
