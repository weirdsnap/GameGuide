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
from typing import Any, Dict, List

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
    "emoji": "🐈",
    "aliases": ["空洞", "hk"],
    "spoiler_axis": "ability",
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
        "黑卵", "辐光者",
    ],
    "exact_patterns": [r"\b(?:hollow knight|空洞骑士|hk)\b"],

    # Prompt 补充
    "extra_prompt": "",
})

_reg({
    "key": "oni",
    "emoji": "💨",
    "aliases": ["oni"],
    "spoiler_axis": "none",
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
        "精炼", "散热", "温度管理", "冷却",
        "管道", "发电", "电池",
        "火箭", "太空", "星图",
    ],
    "exact_patterns": [r"\b(?:oxygen not included|缺氧|oni)\b"],

    "extra_prompt": "",
})

_reg({
    "key": "terraria",
    "emoji": "🪨",
    "aliases": [],
    "spoiler_axis": "ability",
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
    # wiki.gg 中文站分类（2026-11-22 探测，3031 页候选）；fandom zh 源已判死
    "wiki_zh_categories": [
        "武器物品", "盔甲物品", "配饰物品", "工具物品",
        "物块物品", "家具物品", "制作材料物品", "宠物召唤物品",
        "坐骑召唤物品", "弹药物品", "Boss NPC", "敌怪 NPC", "NPC",
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
    "emoji": "🪱",
    "aliases": [],
    # 丝之歌按 Act 1/2/3 推进，章节归属比能力轴更贴合（与 BG3 同理）。
    # 注意：max_act 过滤要等 wiki 重采集后给 chunk 打 act 元数据才生效，
    # 目前只影响进度追问的 prompt 文案。
    "spoiler_axis": "act",
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
    "emoji": "🤖",
    "aliases": ["赛博朋克", "cyberpunk"],
    "spoiler_axis": "chapter",
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
        "强尼", "johnny silverhand", "银手",
        "夜之城", "night city", "荒坂", "arasaka",
        "义体", "cyberware", "relic", "圣物",
        "超梦", "braindance", "虎爪帮",
        "大卫", "lucy", "边缘行者", "edgerunners",
        "百灵鸟", "songbird", "所罗门", "reed",
        "狗镇", "dogtown", "phantom liberty",
        "军用科技", "militech", "漩涡帮", "maelstrom",
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
    "emoji": "🍸",
    "aliases": ["酒保", "va11", "hall-a", "va-11", "valhalla"],
    "spoiler_axis": "chapter",
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
    "emoji": "🐉",
    "aliases": ["怪猎", "怪物猎人", "mh wilds", "mhw"],
    "spoiler_axis": "none",
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
    "emoji": "🧙",
    "aliases": ["博德之门", "博得之门", "baldur", "bg3", "dnd", "d&d", "龙与地下城", "至上真神", "夺心魔"],
    "spoiler_axis": "act",
    "name": "Baldur's Gate 3 (博德之门3)",
    "name_short": "Baldur's Gate 3",

    "dir": "baldurs_gate3",
    "db_file": "data/baldurs_gate3_data.db",
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
        "博德之门", "博得之门3", "博得之门 3", "博得之门",
        "dnd", "d&d", "龙与地下城",
        "影心", "shadowheart", "养鸡妹", "laezel",
        "阿斯代伦", "astarion", "盖尔", "gale",
        "威尔", "wyll", "卡拉克", "卡菈克", "karlach",
        "明萨拉", "明莎拉", "minthara",
        "贾希拉", "jaheira", "哈尔辛", "halsin",
        "明斯克", "minsc", "米佐拉", "mizora", "拉斐尔", "raphael",
        "塔夫", "tav", "邪念", "dark urge",
        "至上真神", "absolute", "夺心魔", "illithid",
        "费伦", "faerun", "剑湾", "sword coast",
        "第一章", "第二章", "第三章", "月出之塔",
        "下城区", "利文顿", "幽暗地域", "最后之光旅店",
        "塞伦涅", "塞伦捏",
        "蝌蚪", "tadpole", "遗物", "artifact",
        "按等级", "第5版", "5e", "法术", "动作",
    ],
    "exact_patterns": [
        # 整句「BG」是用户对博德之门3的简写（线上 11-14 11:39）。
        # 只锚定整句：不要写 \bbg\b——中文和字母同属 \w，「玩bg」不会命中边界。
        r"^\s*bg\s*$",
        r"\b(?:博德之门3|博德之门 3|博得之门3|博得之门 3|baldur'?s? gate 3|bg3)\b"
    ],

    "extra_prompt": (
        "\nNote: Baldur's Gate 3 is a D&D 5e-based CRPG. "
        "Use the correct 5e terminology for spells, actions, classes, and mechanics.\n"
        "Structured DB tables: characters(角色/同伴), spells(法术), weapons(武器), armor(护甲), "
        "items(物品), locations(地点), quests(任务), bosses(Boss), books(书籍). "
        "For entity lookups just pass the entity name, e.g. 「查询 Astarion」or 「查询 阿斯代伦」; "
        "table names in queries are English only. If unsure, call show_database_schema first. "
        "When querying structured data, pass the exact Chinese/English name as-is "
        "(e.g. 「查询 蒸煮罐」) — do NOT translate or append guessed words; "
        "the system already maps common Chinese names internally.\n"
        "Progress anchors (three acts): Act 1 = 第一章地表/幽暗地域 → "
        "Act 2 = 最后之光旅店/月出之塔一带 → Act 3 = 利文顿/下城区. "
        "Structured tables locations/quests have an `act` column (1/2/3) — "
        "use it to judge whether a place/quest is beyond the user's reported progress, "
        "and never positively describe content past that boundary. "
        "When the user's act progress is known, pass max_act (1/2/3) to "
        "search_knowledge_base so later-act locations/quests are filtered out."
    ),
})


_reg({
    "key": "kcd2",
    "emoji": "⚔️",
    "aliases": ["天国拯救", "天国：拯救", "kingdom come", "kcd"],
    # 主线任务线性推进（特罗斯基地区 → 库滕堡地区），与 BG3 的 act 同构。
    # 目前只影响进度追问的 prompt 文案；chunk 级章节元数据未打。
    "spoiler_axis": "chapter",
    "name": "Kingdom Come: Deliverance II (天国拯救2)",
    "name_short": "Kingdom Come Deliverance II",

    "dir": "kcd2",
    "db_file": "kcd2_data.db",
    "data_file": "wiki_data.md",
    "data_file_zh": "",

    # fandom（KCD1/KCD2 同库，用 KCD2: 前缀分类树，见 docs/KCD2_FETCH_PLAN.md）
    # 必须挂代理抓取（直连 000）；wiki.gg 覆盖太薄已弃用，fextralife 连不通。
    "wiki_api": "https://kingdom-come-deliverance.fandom.com/api.php",
    "wiki_user_agent": "GameGuideBot/2.0 (KCD2 wiki fetcher)",
    "wiki_categories": [
        "KCD2: Achievements", "KCD2: Armour", "KCD2: Characters",
        "KCD2: Codex", "KCD2: DLC", "KCD2: Gameplay",
        "KCD2: Items", "KCD2: Locations", "KCD2: Quests", "KCD2: Weapons",
    ],
    "wiki_zh_categories": [],     # 中文社区 wiki 不存在，en-only（同 BG3/mhw）

    "signals": [
        "天国拯救", "天国：拯救", "kingdom come", "kcd2",
        "亨利", "henry of skalitz",
        "库滕堡", "kuttenberg", "特罗斯基", "trosky",
        "拉泰", "rattay",
        "汉斯", "hans capon", "拉德季", "伊斯特万",
        "波西米亚", "bohemia",
    ],
    "exact_patterns": [
        r"\b(?:kcd2|kingdom come(?:\s*:?\s*deliverance)?(?:\s*(?:ii|2))?)\b",
        r"天国(?:：)?拯救(?:2|ii|二)?",
    ],

    "extra_prompt": (
        "\nNote: Kingdom Come: Deliverance II (released Feb 2025) is a realistic "
        "medieval RPG. Main quests are linear: early game around Trosky region → "
        "late game around Kuttenberg region. Use this as the spoiler boundary: "
        "never positively describe main-quest content beyond the user's reported "
        "progress."
    ),
})

_reg({
    "key": "battlefield5",
    "emoji": "🪖",
    "aliases": ["战地5", "战地风云5", "battlefield v", "bf5", "bfv"],
    # 主打多人对战；单人战役是选集式短篇（Under No Flag / Nordlys / ...），
    # 没有线性主线，不存在「进度即上界」结构。
    "spoiler_axis": "none",
    "name": "Battlefield V (战地5)",
    "name_short": "Battlefield V",

    "dir": "battlefield5",
    "db_file": "battlefield5_data.db",
    "data_file": "wiki_data.md",
    "data_file_zh": "",

    # fandom 英文站是唯一可用源（无 wiki.gg；zh 站 BFV 内容约 20 页太薄弃用，
    # 见 docs/BFV_FETCH_PLAN.md）。必须挂代理抓取（直连 000）。
    # 分类体系是 `X of Battlefield V`；千万别前缀模糊抓——会把
    # `of Battlefield Vietnam`（一代越战作，名字相近）混进来。
    "wiki_api": "https://battlefield.fandom.com/api.php",
    "wiki_user_agent": "GameGuideBot/2.0 (BFV wiki fetcher)",
    "wiki_categories": [
        "Vehicles of Battlefield V", "Weapons of Battlefield V",
        "Features of Battlefield V", "Gadgets of Battlefield V",
        "Characters of Battlefield V", "Specializations of Battlefield V",
        "Maps of Battlefield V", "Factions of Battlefield V",
        "Gamemodes of Battlefield V", "Elite Classes of Battlefield V",
        "Levels of Battlefield V",
        # 武器子类型分类：实测约 27 页不在父分类 Weapons 里（近战/手雷/
        # 配件页完全独立），其余子类型（AR/Carbines/Shotguns）已被父分类覆盖
        "Submachine Guns of Battlefield V", "Sidearms of Battlefield V",
        "Light Machine Guns of Battlefield V",
        "Melee Weapons of Battlefield V", "Grenades of Battlefield V",
        "Attachments of Battlefield V",
    ],
    "wiki_zh_categories": [],

    "signals": [
        "战地5", "战地风云5", "battlefield v", "bf5", "bfv",
        "stg44", "阿拉斯", "arras", "鹿特丹", "rotterdam",
        "硫磺岛", "iwo jima", "太平洋风暴", "pacific storm",
        "猛虎末路", "the last tiger",
    ],
    "exact_patterns": [
        r"\bbfv\b", r"\bbf5\b",
        r"\bbattlefield\s*(?:v|5)\b",
        r"战地(?:风云)?\s*(?:5|v)",
    ],

    "extra_prompt": (
        "\nNote: Battlefield V (2018) is a WWII multiplayer shooter; its single-"
        "player War Stories are standalone short episodes. Weapon stats (damage/"
        "RPM/magazine) and map layouts are the most frequent ask topics."
    ),
})


# ═══════════════════════════════════════════════════════════════
#  跨游戏通用信号（弱证据）
#
# 这些词在 ≥2 款游戏里都会出现，不能当「点名了某游戏」的硬证据：
# 只给 0.25 权重，且不能单独把已确认的会话切到别的游戏。
# 新增词条时请确认它确实跨游戏；名单宁少勿多（多登记 = 新会话识别率下降）。
# 规则见 docs/P0_ROUTING_GATE_PLAN.md §4.1。
# ═══════════════════════════════════════════════════════════════

GENERIC_SIGNALS: set[str] = {
    # 空洞骑士（「护符/灵魂」在任何有装备或死亡机制的游戏里都会出现）
    "灵魂", "护符", "梦境", "梦钉",
    # 博德之门3
    "法术", "动作", "按等级", "第5版", "5e",
    # 缺氧
    "电池", "太空", "火箭", "冷却", "散热", "温度管理", "发电", "管道", "精炼",
    # 泰拉瑞亚
    "钓鱼", "向导", "神圣", "腐化", "血腥", "恶魔", "史莱姆", "矿车",
    # 赛博朋克2077
    "义体", "黑客", "圣物",
    # VA-11 Hall-A
    "调制", "调酒", "鸡尾酒",
}


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
        "spoiler_axis": cfg.get("spoiler_axis", "none"),
    }
    GAME_SIGNALS[key] = cfg["signals"]
    GAME_EXACT_PATTERNS[key] = cfg["exact_patterns"]
    if cfg.get("extra_prompt"):
        EXTRA_PROMPT_NOTES[key] = cfg["extra_prompt"]


# ── graph.py：菜单与 known_keywords（单一来源，别再在 graph.py 里另维护一份）──

GAME_MENU: List[Dict[str, str]] = []     # [{key, emoji, zh, en}]，注册顺序即菜单顺序
KNOWN_KEYWORDS: List[str] = []           # _is_unknown_game_query 的已知游戏排除词

for key, cfg in GAME_REGISTRY.items():
    m = re.search(r"\((.+?)\)", cfg["name"])
    zh = m.group(1) if m else cfg["name"]
    en = cfg["name"][:m.start()].strip() if m else cfg.get("name_short", cfg["name"])
    GAME_MENU.append({"key": key, "emoji": cfg.get("emoji", "🎮"), "zh": zh, "en": en})
    KNOWN_KEYWORDS += [zh, en.lower(), cfg.get("name_short", "").lower()]
    KNOWN_KEYWORDS += [a.lower() for a in cfg.get("aliases", [])]

KNOWN_KEYWORDS = sorted({k for k in KNOWN_KEYWORDS if k}, key=len, reverse=True)


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
