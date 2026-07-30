"""
game_registry.py — 单源真理

所有游戏配置集中管理。添加新游戏只需在这里加一条记录，
五处引用点（game_router / ingest_game / mac_build / wiki / vectorstores.sh）
自动同步。

游戏 key 统一使用小写英文字母下划线格式。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── 项目根路径 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GAMES_DIR = PROJECT_ROOT / "games"


# ═══════════════════════════════════════════════════════════════
#  主注册表
# ═══════════════════════════════════════════════════════════════

GameEntry = Dict[str, Any]

GAME_REGISTRY: Dict[str, GameEntry] = {}

def _reg(entry: GameEntry) -> None:
    """注册一条游戏记录（方便 IDE 阅读）"""
    GAME_REGISTRY[entry["key"]] = entry


_reg({
    "key": "hollow_knight",
    "name": "Hollow Knight (空洞骑士)",
    "name_short": "Hollow Knight",

    # 路径
    "dir": "hollow_knight",
    "db_file": "hk_data.db",
    "data_file": "wiki_data.md",
    "data_file_zh": "wiki_data_zh.md",

    # Wiki 抓取
    "wiki_api": "https://hollowknight.fandom.com/api.php",
    "wiki_user_agent": "GameGuideBot/2.0 (Hollow Knight wiki fetcher)",
    "wiki_categories": [
        "Bosses", "Charms_(Hollow_Knight)", "Characters",
        "Enemies_(Hollow_Knight)", "Items_(Hollow_Knight)",
    ],
    "wiki_zh_categories": ["Boss", "护符", "技能", "法术", "物品", "敌人"],

    # 路由检测
    "signals": [
        "hollow knight", "空洞骑士", "hallownest", "圣巢",
        "hk",
        "辐光", "radiance", "纯粹容器", "pure vessel",
        "hornet", "大黄蜂", "grimm", "格林",
        "螳螂领主", "deepnest", "pale king", "白王",
        "虚空之心", "灵魂", "梦境", "梦钉",
        "泪水之城", "city of tears", "王国边缘", "王后驿站",
        "遗忘十字路", "十字路", "fungal wastes",
        "骨钉", "复仇之魂", "护符", "perma",
        "苦痛之路", "白宫", "神居", "godhome",
        "空洞骑士", "黑卵", "辐光者",
    ],
    "exact_patterns": [r"\b(?:hollow knight|空洞骑士|hk)\b"],

    # Prompt 补充
    "extra_prompt": "",
})

_reg({
    "key": "oni",
    "name": "Oxygen Not Included (缺氧)",
    "name_short": "Oxygen Not Included",

    "dir": "oni",
    "db_file": "oni_data.db",
    "data_file": "wiki_data.md",
    "data_file_zh": "wiki_data_zh.md",

    "wiki_api": "https://oxygennotincluded.fandom.com/api.php",
    "wiki_user_agent": "GameGuideBot/2.0 (ONI wiki fetcher)",
    "wiki_categories": [
        "Animals", "Buildings", "Critters", "Food",
        "Geysers", "Plants", "Resources", "Technology",
    ],
    "wiki_zh_categories": [
        "小动物", "复制人技能", "可食用物", "房间",
        "功能性植物", "工业性植物", "娱乐建筑",
        "医疗建筑", "传感器", "发电机",
    ],

    "signals": [
        "oxygen not included", "缺氧", "oni",
        "复制人", "duplicant", "drecko", "hatch",
        "氧齿蕨", "净水", "石油", "塑料",
        "精炼", "热", "温度", "冷却",
        "管道", "电", "发电", "电池",
        "火箭", "太空", "星图",
    ],
    "exact_patterns": [r"\b(?:oxygen not included|缺氧|oni)\b"],

    "extra_prompt": "",
})

_reg({
    "key": "terraria",
    "name": "Terraria (泰拉瑞亚)",
    "name_short": "Terraria",

    "dir": "terraria",
    "db_file": "terraria_data.db",
    "data_file": "wiki_data.md",
    "data_file_zh": "wiki_data_zh.md",

    "wiki_api": "https://terraria.fandom.com/api.php",
    "wiki_user_agent": "GameGuideBot/2.0 (Terraria wiki fetcher)",
    "wiki_categories": [
        "Armor_items", "Accessory_items", "Weapon_items",
        "Bosses", "NPCs", "Enemies",
    ],

    "signals": [
        "terraria", "泰拉瑞亚",
        "肉山", "wall of flesh", "月总", "moon lord",
        "克苏鲁", "史莱姆", "泰拉刃", "terra blade",
        "叶绿", "神圣", "血腥", "腐化",
        "恶魔", "向导", "npc",
        "矿车", "钓鱼", "史莱姆女王",
        "日耀", "星旋", "星云",
    ],
    "exact_patterns": [r"\b(?:terraria|泰拉瑞亚|泰拉)\b"],

    "extra_prompt": "",
})

_reg({
    "key": "silksong",
    "name": "Hollow Knight Silksong (丝之歌)",
    "name_short": "Silksong",

    "dir": "silksong",
    "db_file": "silksong_data.db",
    "data_file": "wiki_data.md",
    "data_file_zh": "",

    "wiki_api": "https://hollowknight.fandom.com/api.php",
    "wiki_user_agent": "GameGuideBot/2.0 (Silksong wiki fetcher)",
    "wiki_categories": [
        "Additional_Content_(Silksong)", "Areas_(Silksong)",
        "Bosses_(Silksong)", "Combat_(Silksong)", "Enemies_(Silksong)",
        "Exploration_(Silksong)", "Hollow_Knight:_Silksong",
        "Items_(Silksong)", "NPCs_(Silksong)", "Points_of_Interest_(Silksong)",
    ],
    "wiki_zh_categories": [],

    "signals": [
        "silksong", "丝之歌",
        "丝之鸽",
        "hornet", "黄蜂公主", "pharloom",
        "lace", "编织者",
        "绸缎", "丝线",
    ],
    "exact_patterns": [r"\b(?:silksong|丝之歌)\b"],

    "extra_prompt": "",
})

_reg({
    "key": "cyberpunk2077",
    "name": "Cyberpunk 2077 (赛博朋克2077)",
    "name_short": "Cyberpunk 2077",

    "dir": "cyberpunk2077",
    "db_file": "cyberpunk2077_data.db",
    "data_file": "wiki_data.md",
    "data_file_zh": "",

    "wiki_api": "https://cyberpunk.fandom.com/api.php",
    "wiki_user_agent": "GameGuideBot/2.0 (Cyberpunk 2077 wiki fetcher)",
    "wiki_categories": [
        "Cyberpunk_2077_Characters", "Cyberpunk_2077_Locations",
        "Cyberpunk_2077_Weapons", "Cyberpunk_2077_Cyberware",
        "Cyberpunk_2077_Vehicles", "Cyberpunk_2077_Main_Jobs",
        "Cyberpunk_2077_Side_Jobs", "Cyberpunk_2077_Gigs",
        "Cyberpunk_2077_Enemies", "Cyberpunk_2077_Perks",
        "Cyberpunk_2077_Quickhacks", "Cyberpunk_2077_Quest_Items",
        "Cyberpunk_2077_Consumables", "Cyberpunk_2077_DLC",
        "Cyberpunk_2077_Phantom_Liberty",
    ],
    "wiki_zh_categories": [],

    "signals": [
        "cyberpunk 2077", "赛博朋克2077", "赛博朋克 2077",
        "2077", "cp2077",
        "v", "强尼", "johnny silverhand", "银手",
        "夜之城", "night city", "荒坂", "arasaka",
        "义体", "cyberware", "relic", "圣物",
        "超梦", "braindance", "虎爪帮",
        "大卫", "lucy", "边缘行者", "edgerunners",
        "百灵鸟", "songbird", "所罗门", "reed",
        "狗镇", "dogtown", "phantom liberty",
        "军用科技", "militech", "漩", "maelstrom",
        "黑墙", "blackwall", "黑客", "quickhack",
    ],
    "exact_patterns": [
        r"\b(?:cyberpunk 2077|赛博朋克2077|赛博朋克 2077|2077|cp2077)\b"
    ],

    "extra_prompt": (
        "\nNote: You also cover the Phantom Liberty expansion content."
    ),
})

_reg({
    "key": "va11halla",
    "name": "VA-11 Hall-A (赛博朋克酒保行动)",
    "name_short": "VA-11 Hall-A",

    "dir": "va11halla",
    "db_file": "va11halla_data.db",
    "data_file": "wiki_data.md",
    "data_file_zh": "wiki_data_zh.md",

    "wiki_api": "https://va11halla.fandom.com/api.php",
    "wiki_user_agent": "GameGuideBot/2.0 (VA-11 Hall-A wiki fetcher)",
    "wiki_categories": [
        "Characters", "Drinks", "Ingredients", "Events",
        "Places", "Bars", "Organisations",
    ],
    "wiki_zh_categories": [
        "VA-11 Hall-A 员工", "VA-11 Hall-A 顾客", "饮品", "配料",
        "人类", "动物", "地点", "组织",
    ],

    "signals": [
        "va-11 hall-a", "赛博朋克酒保", "酒保行动",
        "va11halla", "valhalla", "瓦尔哈拉", "va11",
        "jill", "吉尔", "dana", "戴娜",
        "调制", "调酒", "鸡尾酒", "bartender",
        "坏Touch", "brandtini",
        "安娜", "anime",
    ],
    "exact_patterns": [
        r"\b(?:va-11 hall-a|va11halla|赛博朋克酒保|酒保行动|va11)\b"
    ],

    "extra_prompt": "",
})

_reg({
    "key": "mhw",
    "name": "Monster Hunter Wilds (怪物猎人荒野)",
    "name_short": "Monster Hunter Wilds",

    "dir": "mhw",
    "db_file": "mhw_data.db",
    "data_file": "wiki_data.md",
    "data_file_zh": "",

    "wiki_api": "https://monsterhunter.fandom.com/api.php",
    "wiki_user_agent": "GameGuideBot/2.0 (MHW wiki fetcher)",
    "wiki_categories": [
        "Monsters_in_Monster_Hunter_Wilds",
        "Weapons_in_Monster_Hunter_Wilds",
        "Armor_in_Monster_Hunter_Wilds",
        "Skills_in_Monster_Hunter_Wilds",
        "Locations_in_Monster_Hunter_Wilds",
    ],
    "wiki_zh_categories": [],

    "signals": [
        "monster hunter wilds", "怪物猎人荒野", "mh wilds",
        "mhwilds", "mhws", "怪猎荒野",
        "rey dau", "uth duna", "chatacabra", "arkveld",
        "oilwell basin", "windward plains", "ruins of wyveria",
        "煌雷龙", "沼龙", "风铗龙",
        "flying wyvern", "leviathan", "fanged beast",
    ],
    "exact_patterns": [
        r"\b(?:怪物猎人荒野|monster hunter wilds|mh wilds|mhwilds|mhws)\b"
    ],

    "extra_prompt": (
        "\nNote: You specialize in Monster Hunter Wilds (released Feb 2025). "
        "For questions about other Monster Hunter games (World, Rise, etc.), "
        "briefly note you're only equipped for Wilds."
    ),
})

_reg({
    "key": "baldurs_gate3",
    "name": "Baldur's Gate 3 (博德之门3)",
    "name_short": "Baldur's Gate 3",

    "dir": "baldurs_gate3",
    "db_file": "baldurs_gate3_data.db",
    "data_file": "wiki_data.md",
    "data_file_zh": "",

    # BG3 使用独立 wiki (bg3.wiki) 而非 Fandom
    "wiki_api": "https://bg3.wiki/w/api.php",
    "wiki_user_agent": "GameGuideBot/2.0 (BG3 wiki fetcher)",
    "wiki_categories": [
        "Characters", "Spells", "Equipment", "Weapons",
        "Armour", "Items", "Locations", "Quests",
    ],
    "wiki_zh_categories": [],

    "signals": [
        "baldur's gate 3", "博德之门3", "博德之门 3", "bg3",
        "博德之门",
        "dnd", "d&d", "龙与地下城",
        "影心", "shadowheart", "养鸡妹", "laezel",
        "阿斯代伦", "astarion", "盖尔", "gale",
        "威尔", "wyll", "卡拉克", "karlach",
        "塔夫", "tav", "邪念", "dark urge",
        "至上真神", "absolute", "夺心魔", "illithid",
        "费伦", "faerun", "剑湾", "sword coast",
        "第一章", "第二章", "第三章", "月出之塔",
        "博德之门", "下城区", "利文顿", "幽暗地域",
        "蝌蚪", "tadpole", "遗物", "artifact",
        "按等级", "第5版", "5e", "法术", "动作",
    ],
    "exact_patterns": [
        r"\b(?:博德之门3|博德之门 3|baldur'?s? gate 3|bg3)\b"
    ],

    "extra_prompt": (
        "\nNote: Baldur's Gate 3 is a D&D 5e-based CRPG. "
        "Use the correct 5e terminology for spells, actions, classes, and mechanics."
    ),
})


# ═══════════════════════════════════════════════════════════════
#  衍生导出（各消费者各取所需）
# ═══════════════════════════════════════════════════════════════

def _abs_dir(game_key: str) -> Path:
    return GAMES_DIR / GAME_REGISTRY[game_key]["dir"]


def _abs_path(game_key: str, *parts: str) -> str:
    return str(_abs_dir(game_key).joinpath(*parts))


# ── 通用 ──

GAME_KEYS: List[str] = list(GAME_REGISTRY.keys())

# ── game_router.py ──

AVAILABLE_GAMES: Dict[str, Dict[str, Any]] = {}
GAME_SIGNALS: Dict[str, List[str]] = {}
GAME_EXACT_PATTERNS: Dict[str, List[str]] = {}
EXTRA_PROMPT_NOTES: Dict[str, str] = {}

for key, cfg in GAME_REGISTRY.items():
    d = cfg["dir"]
    AVAILABLE_GAMES[key] = {
        "name": cfg["name"],
        "db_path": _abs_path(key, cfg["db_file"]),
        "vectorstore_dir": _abs_path(key, "vectorstore"),
    }
    GAME_SIGNALS[key] = cfg["signals"]
    GAME_EXACT_PATTERNS[key] = cfg["exact_patterns"]
    if cfg.get("extra_prompt"):
        EXTRA_PROMPT_NOTES[key] = cfg["extra_prompt"]


# ── ingest_game.py / mac_build.py ──

GAME_DATA: Dict[str, Dict[str, str]] = {}
GAME_DB_MAPPING: Dict[str, str] = {}

for key, cfg in GAME_REGISTRY.items():
    d = cfg["dir"]
    data_zh = cfg.get("data_file_zh", "")
    GAME_DATA[key] = {
        "name": cfg["name_short"],
        "data_path": _abs_path(key, "data", cfg["data_file"]),
        "data_path_zh": _abs_path(key, "data", data_zh) if data_zh else "",
        "vectorstore_dir": _abs_path(key, "vectorstore"),
    }
    GAME_DB_MAPPING[key] = _abs_path(key, cfg["db_file"])


# ── wiki.py ──

WIKI_CONFIGS: Dict[str, Dict[str, Any]] = {}

for key, cfg in GAME_REGISTRY.items():
    if not cfg.get("wiki_api"):
        continue
    d = cfg["dir"]
    wc: Dict[str, Any] = {
        "api": cfg["wiki_api"],
        "user_agent": cfg["wiki_user_agent"],
        "output": _abs_dir(key) / "data" / cfg["data_file"],
        "categories": cfg["wiki_categories"],
        "zh_categories": cfg.get("wiki_zh_categories", []),
    }
    WIKI_CONFIGS[key] = wc
