#!/usr/bin/env python3
"""
Game Structured Database Builder.

For each game, fetches raw wikitext from the wiki API,
extracts infobox/structured data, and builds SQLite databases.

Usage:
  python scripts/build_game_db.py --game terraria
  python scripts/build_game_db.py --game oni
  python scripts/build_game_db.py --game silksong
  python scripts/build_game_db.py --game all
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    print("❌ 需要 requests 库：pip install requests")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAMES_DIR = PROJECT_ROOT / "games"


# ── Infobox Parser ──

def extract_infobox(wikitext: str, template_name: str) -> Dict[str, str]:
    """从 wikitext 中提取指定模板的参数。

    支持两种情况：
    - {{Infobox Building\n| param = value\n}}
    - {{npc infobox\n| param = value\n}}
    - 嵌套参数如 {{gc|2}} 保留原始字符串
    """
    params = {}
    # 匹配 {{TemplateName\n|...}}
    pattern = re.compile(
        r'\{\{[\s_]*' + re.escape(template_name) + r'[\s_]*\n(.*?)\}\}',
        re.DOTALL | re.IGNORECASE
    )
    match = pattern.search(wikitext)
    if not match:
        # Try with different spacing
        pattern2 = re.compile(
            r'\{\{[\s_]*' + re.escape(template_name.replace('_', '')) + r'[\s_]*(.*?)\}\}',
            re.DOTALL | re.IGNORECASE
        )
        match = pattern2.search(wikitext)
    if not match:
        return params

    body = match.group(1)
    for line in body.split('\n'):
        line = line.strip()
        if not line or not line.startswith('|'):
            continue
        line = line.lstrip('|').strip()
        # Split on first = sign
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip().lower().replace(' ', '_')
        value = value.strip()
        # Remove HTML comments
        value = re.sub(r'<!--.*?-->', '', value).strip()
        if key:
            params[key] = value
    return params


def clean_value(value: str) -> str:
    """清理 infobox 参数值：去除 wikitext 标记但不丢失数值。"""
    # Remove image/file links
    value = re.sub(r'\[\[File:[^\]]*\|([^\]]*)\]\]', r'\1', value)
    value = re.sub(r'\[\[(?:File|Image):[^\]]*\]\]', '', value)
    # Convert [[Link|text]] → text
    value = re.sub(r'\[\[([^\]|]*?)\|([^\]]*?)\]\]', r'\2', value)
    value = re.sub(r'\[\[([^\]]*?)\]\]', r'\1', value)
    # Convert price templates: {{gc|2}} → 2gc
    value = re.sub(r'\{\{gc\|(\d+)\}\}', r'\1 gc', value)
    value = re.sub(r'\{\{sc\|(\d+)\}\}', r'\1 sc', value)
    value = re.sub(r'\{\{cc\|(\d+)\}\}', r'\1 cc', value)
    # Remove other templates
    value = re.sub(r'\{\{[^{}]*?\}\}', '', value)
    # Remove HTML tags
    value = re.sub(r'<[^>]+>', '', value)
    # Clean whitespace
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def parse_numeric(value: str) -> Optional[float]:
    """从字符串中解析数字。"""
    value = clean_value(value)
    # Remove {{...}} templates that might contain formatting
    cleaned = re.sub(r'\{\{[^{}]*?\}\}', '', value)
    # Remove commas in numbers
    cleaned = cleaned.replace(',', '')
    # Try to find a number
    match = re.search(r'(-?\d+(?:\.\d+)?)', cleaned)
    if match:
        return float(match.group(1))
    return None


def parse_currency(value: str) -> Optional[int]:
    """解析 Fandom 金钱表示法。"""
    text = clean_value(value)
    total = 0
    # {{gc|2}} {{sc|50}} {{cc|10}}
    gc = re.search(r'\{\{gc\|(\d+)\}\}', value)
    sc = re.search(r'\{\{sc\|(\d+)\}\}', value)
    cc = re.search(r'\{\{cc\|(\d+)\}\}', value)
    if gc:
        total += int(gc.group(1)) * 100 * 100
    if sc:
        total += int(sc.group(1)) * 100
    if cc:
        total += int(cc.group(1))
    # Also try plain text like "2 gold 50 silver"
    if total == 0:
        gold_m = re.search(r'(\d+)\s*gold', text, re.I)
        silver_m = re.search(r'(\d+)\s*silver', text, re.I)
        copper_m = re.search(r'(\d+)\s*copper', text, re.I)
        if gold_m:
            total += int(gold_m.group(1)) * 100 * 100
        if silver_m:
            total += int(silver_m.group(1)) * 100
        if copper_m:
            total += int(copper_m.group(1))
    return total if total > 0 else None


# ── Wiki API 交互 ──

def fetch_raw_wikitext(api_url: str, titles: List[str], user_agent: str) -> Dict[str, str]:
    """批量获取页面的原始 wikitext。"""
    results = {}
    headers = {'User-Agent': user_agent}
    batch_size = 20

    for batch_start in range(0, len(titles), batch_size):
        batch = list(set(titles[batch_start:batch_start + batch_size]))
        params = {
            'action': 'query',
            'titles': '|'.join(batch),
            'redirects': '1',
            'prop': 'revisions',
            'rvprop': 'content',
            'format': 'json',
        }
        try:
            resp = requests.get(api_url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"  ⚠️ 请求失败: {e}")
            time.sleep(2)
            continue

        query = data.get('query', {})

        # 处理重定向
        title_map = {}
        if 'redirects' in query:
            for rd in query['redirects']:
                title_map[rd['from']] = rd['to']
        for t in batch:
            if t not in title_map:
                title_map[t] = t

        # 获取内容
        for page_id, page_data in query.get('pages', {}).items():
            if page_id == '-1':
                continue
            title = page_data.get('title', '')
            revs = page_data.get('revisions', [])
            if revs:
                results[title] = revs[0].get('*', '')

        time.sleep(1.5)

    return results


def get_category_members(api_url: str, category: str, user_agent: str, limit: int = 200) -> List[str]:
    """获取分类下的页面标题。"""
    titles = []
    cmcontinue = None
    headers = {'User-Agent': user_agent}

    while True:
        params = {
            'action': 'query',
            'list': 'categorymembers',
            'cmtitle': f'Category:{category}',
            'cmlimit': min(50, limit),
            'format': 'json',
        }
        if cmcontinue:
            params['cmcontinue'] = cmcontinue

        try:
            resp = requests.get(api_url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"  ⚠️ 分类请求失败: {e}")
            break

        for member in data.get('query', {}).get('categorymembers', []):
            if member.get('ns') == 0:
                titles.append(member['title'])

        if len(titles) >= limit:
            break

        cont = data.get('continue', {})
        cmcontinue = cont.get('cmcontinue')
        if not cmcontinue:
            break

    return titles[:limit]


# ── Database Helpers ──

def get_db(db_path: Path):
    """获取 SQLite 连接。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════
# ║  Terraria
# ═══════════════════════════════════════════════

TERRARIA_CONFIG = {
    "api": "https://terraria.fandom.com/api.php",
    "user_agent": "GameGuideBot/1.0 (Terraria structured data builder)",
    "output": GAMES_DIR / "terraria" / "terraria_data.db",
    "categories": {
        "Boss_NPCs": "bosses",
        "Enemy_NPCs": "enemies",
        "Weapon_items": "weapons",
        "Armor_items": "armor",
        "Accessory_items": "accessories",
        "Potion_items": "potions",
        "NPCs": "npcs",
    }
}

TERRARIA_SCHEMA = """
CREATE TABLE IF NOT EXISTS bosses (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hp INTEGER,
    damage INTEGER,
    defense INTEGER,
    knockback_resist REAL,
    environment TEXT,
    drops TEXT,
    coins TEXT
);

CREATE TABLE IF NOT EXISTS enemies (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hp INTEGER,
    damage INTEGER,
    defense INTEGER,
    knockback_resist REAL,
    environment TEXT,
    coins TEXT
);

CREATE TABLE IF NOT EXISTS weapons (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    damage INTEGER,
    use_time INTEGER,
    knockback REAL,
    mana INTEGER,
    critical INTEGER,
    rarity TEXT,
    sell TEXT,
    type TEXT,
    velocity REAL
);

CREATE TABLE IF NOT EXISTS armor (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    defense INTEGER,
    set_bonus TEXT,
    rarity TEXT,
    sell TEXT,
    piece_type TEXT
);

CREATE TABLE IF NOT EXISTS accessories (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    rarity TEXT,
    sell TEXT,
    effect TEXT
);

CREATE TABLE IF NOT EXISTS potions (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    buff TEXT,
    duration TEXT,
    rarity TEXT,
    sell TEXT
);

CREATE TABLE IF NOT EXISTS npcs (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hp INTEGER,
    defense INTEGER,
    damage INTEGER,
    biome TEXT,
    shop_items TEXT
);
"""


def build_terraria_db():
    cfg = TERRARIA_CONFIG
    print(f"\n{'='*50}")
    print(f"⚔️ Terraria — 构建结构化数据库")
    print(f"{'='*50}")
    db = get_db(cfg["output"])
    db.executescript(TERRARIA_SCHEMA)

    all_titles = {}
    for cat_name, doc_type in cfg["categories"].items():
        titles = get_category_members(cfg["api"], cat_name, cfg["user_agent"])
        all_titles[doc_type] = [t for t in titles
                                if not t.startswith(('User:', 'Template:', 'File:', 'Category:', 'Module:'))]
        print(f"  {doc_type}: {len(all_titles[doc_type])} pages")

    # Fetch raw wikitext
    all_to_fetch = []
    for doc_type, titles in all_titles.items():
        all_to_fetch.extend(titles)
    print(f"\n📥 获取原始 wikitext（{len(all_to_fetch)} 篇）...")
    raw_data = fetch_raw_wikitext(cfg["api"], all_to_fetch, cfg["user_agent"])
    print(f"  → 获取到 {len(raw_data)} 篇")

    # Parse and insert
    count = {"bosses": 0, "enemies": 0, "weapons": 0, "armor": 0,
             "accessories": 0, "potions": 0, "npcs": 0}

    for doc_type, titles in all_titles.items():
        print(f"\n📋 处理 {doc_type}...")
        for title in titles:
            wt = raw_data.get(title)
            if not wt:
                continue
            slug = title.lower().replace(' ', '-').replace("'", '').replace('(', '').replace(')', '')
            slug = re.sub(r'[^a-z0-9\-]', '', slug)

            if doc_type in ("bosses", "enemies"):
                params = extract_infobox(wt, "npc infobox")
                hp = parse_numeric(params.get("life", ""))
                damage = parse_numeric(params.get("damage", ""))
                defense = parse_numeric(params.get("defense", ""))
                kb_text = clean_value(params.get("knockback", "0"))
                try:
                    kb = float(kb_text) if kb_text else None
                except ValueError:
                    kb = None
                env = clean_value(params.get("environment", ""))
                coins_raw = clean_value(params.get("money", ""))
                drops_raw = clean_value(params.get("drops", ""))

                try:
                    if doc_type == "bosses":
                        db.execute("""INSERT OR REPLACE INTO bosses
                            (slug, name, hp, damage, defense, knockback_resist, environment, drops, coins)
                            VALUES (?,?,?,?,?,?,?,?,?)""",
                            (slug, title, hp, damage, defense, kb, env, drops_raw, coins_raw))
                        count["bosses"] += 1
                    else:
                        db.execute("""INSERT OR REPLACE INTO enemies
                            (slug, name, hp, damage, defense, knockback_resist, environment, coins)
                            VALUES (?,?,?,?,?,?,?,?)""",
                            (slug, title, hp, damage, defense, kb, env, coins_raw))
                        count["enemies"] += 1
                except sqlite3.IntegrityError as e:
                    print(f"  ⚠️ 跳过 {title}: {e}")

            elif doc_type == "weapons":
                params = extract_infobox(wt, "item infobox")
                damage = parse_numeric(params.get("damage", ""))
                use_time = parse_numeric(params.get("usetime", ""))
                kb = parse_numeric(params.get("knockback", ""))
                mana = parse_numeric(params.get("mana", ""))
                crit = parse_numeric(params.get("critical", ""))
                rarity = clean_value(params.get("rare", ""))
                sell_text = clean_value(params.get("sell", ""))
                wtype = clean_value(params.get("type", ""))
                vel = parse_numeric(params.get("velocity", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO weapons
                        (slug, name, damage, use_time, knockback, mana, critical, rarity, sell, type, velocity)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (slug, title, damage, use_time, kb, mana, crit, rarity, sell_text, wtype, vel))
                    count["weapons"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "armor":
                params = extract_infobox(wt, "item infobox")
                defense = parse_numeric(params.get("defense", ""))
                set_bonus = clean_value(params.get("bonus", "")) or clean_value(params.get("set_bonus", ""))
                rarity = clean_value(params.get("rare", ""))
                sell_text = clean_value(params.get("sell", ""))
                # Determine piece type
                ptype = "set"
                body_type = clean_value(params.get("body_slot", "")).lower()
                if 'head' in body_type or 'helmet' in slug:
                    ptype = "helmet"
                elif 'chest' in body_type or 'breastplate' in slug or 'mail' in slug:
                    ptype = "chestplate"
                elif 'leg' in body_type or 'greave' in slug:
                    ptype = "greaves"

                try:
                    db.execute("""INSERT OR REPLACE INTO armor
                        (slug, name, defense, set_bonus, rarity, sell, piece_type)
                        VALUES (?,?,?,?,?,?,?)""",
                        (slug, title, defense, set_bonus, rarity, sell_text, ptype))
                    count["armor"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "accessories":
                params = extract_infobox(wt, "item infobox")
                rarity = clean_value(params.get("rare", ""))
                sell_text = clean_value(params.get("sell", ""))
                # Try to extract effect from text
                body_text = clean_value(wt[:3000])
                effect = ""

                try:
                    db.execute("""INSERT OR REPLACE INTO accessories
                        (slug, name, rarity, sell, effect)
                        VALUES (?,?,?,?,?)""",
                        (slug, title, rarity, sell_text, effect))
                    count["accessories"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "potions":
                params = extract_infobox(wt, "item infobox")
                buff = clean_value(params.get("buff", ""))
                duration = clean_value(params.get("duration", ""))
                rarity = clean_value(params.get("rare", ""))
                sell_text = clean_value(params.get("sell", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO potions
                        (slug, name, buff, duration, rarity, sell)
                        VALUES (?,?,?,?,?,?)""",
                        (slug, title, buff, duration, rarity, sell_text))
                    count["potions"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "npcs":
                params = extract_infobox(wt, "npc infobox")
                hp = parse_numeric(params.get("life", ""))
                damage = parse_numeric(params.get("damage", ""))
                defense = parse_numeric(params.get("defense", ""))
                env = clean_value(params.get("environment", ""))
                # Try to get shop items from text
                shop = ""
                shop_match = re.search(r'(?:Sells|shop).*?(?:\n|$)', wt, re.I | re.MULTILINE)
                if shop_match:
                    shop = clean_value(shop_match.group(0))

                try:
                    db.execute("""INSERT OR REPLACE INTO npcs
                        (slug, name, hp, defense, damage, biome, shop_items)
                        VALUES (?,?,?,?,?,?,?)""",
                        (slug, title, hp, defense, damage, env, shop))
                    count["npcs"] += 1
                except sqlite3.IntegrityError:
                    pass

    db.commit()
    db.close()

    print(f"\n✅ Terraria 数据库构建完成：{cfg['output']}")
    for t, n in count.items():
        print(f"  {t}: {n} 条")
    total = sum(count.values())
    print(f"  总计：{total} 条")


# ═══════════════════════════════════════════════
# ║  Oxygen Not Included
# ═══════════════════════════════════════════════

ONI_CONFIG = {
    "api": "https://oxygennotincluded.fandom.com/api.php",
    "user_agent": "GameGuideBot/1.0 (ONI structured data builder)",
    "output": GAMES_DIR / "oni" / "oni_data.db",
    "categories": {
        "Buildings": "buildings",
        "Critters": "critters",
        "Food": "food",
        "Geysers": "geysers",
        "Liquid": "liquids",
        "Gas": "gases",
        "Resources": "resources",
        "Plants": "plants",
        "Duplicants": "duplicants",
        "Diseases": "diseases",
    }
}

ONI_SCHEMA = """
CREATE TABLE IF NOT EXISTS buildings (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    power INTEGER,
    heat REAL,
    material_cost TEXT,
    dimensions TEXT,
    category TEXT
);

CREATE TABLE IF NOT EXISTS critters (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    calories INTEGER,
    reproduction TEXT,
    temp_range TEXT,
    diet TEXT
);

CREATE TABLE IF NOT EXISTS food (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    calories INTEGER,
    quality INTEGER
);

CREATE TABLE IF NOT EXISTS geysers (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    output TEXT,
    temperature REAL,
    pressure REAL
);

CREATE TABLE IF NOT EXISTS resources (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    thermal_conductivity REAL,
    specific_heat_capacity REAL,
    melting_point REAL,
    category TEXT
);

CREATE TABLE IF NOT EXISTS plants (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    growth_time REAL,
    harvest TEXT,
    temp_range TEXT
);

CREATE TABLE IF NOT EXISTS diseases (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    effects TEXT,
    treatment TEXT
);

CREATE TABLE IF NOT EXISTS buildings (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    power INTEGER,
    heat REAL,
    material_cost TEXT,
    dimensions TEXT,
    category TEXT
);
"""


def build_oni_db():
    cfg = ONI_CONFIG
    print(f"\n{'='*50}")
    print(f"💨 Oxygen Not Included — 构建结构化数据库")
    print(f"{'='*50}")

    # Remove duplicate buildings table in schema
    db = get_db(cfg["output"])
    oni_schema = """
    CREATE TABLE IF NOT EXISTS buildings (
        slug TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        power INTEGER,
        heat REAL,
        material_cost TEXT,
        dimensions TEXT,
        category TEXT
    );
    CREATE TABLE IF NOT EXISTS critters (
        slug TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        calories INTEGER,
        reproduction TEXT,
        temp_range TEXT,
        diet TEXT
    );
    CREATE TABLE IF NOT EXISTS food (
        slug TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        calories INTEGER,
        quality INTEGER
    );
    CREATE TABLE IF NOT EXISTS geysers (
        slug TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        output TEXT,
        temperature REAL,
        pressure REAL
    );
    CREATE TABLE IF NOT EXISTS resources (
        slug TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        thermal_conductivity REAL,
        specific_heat_capacity REAL,
        melting_point REAL,
        category TEXT
    );
    CREATE TABLE IF NOT EXISTS plants (
        slug TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        growth_time REAL,
        harvest TEXT,
        temp_range TEXT
    );
    CREATE TABLE IF NOT EXISTS diseases (
        slug TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        effects TEXT,
        treatment TEXT
    );
    """
    db.executescript(oni_schema)

    all_titles = {}
    for cat_name, doc_type in cfg["categories"].items():
        titles = get_category_members(cfg["api"], cat_name, cfg["user_agent"])
        all_titles[doc_type] = [t for t in titles
                                if not t.startswith(('User:', 'Template:', 'File:', 'Category:', 'Module:'))]
        print(f"  {doc_type}: {len(all_titles[doc_type])} pages")

    # Fetch raw wikitext
    all_to_fetch = []
    for titles in all_titles.values():
        all_to_fetch.extend(titles)
    print(f"\n📥 获取原始 wikitext（{len(all_to_fetch)} 篇）...")
    raw_data = fetch_raw_wikitext(cfg["api"], all_to_fetch, cfg["user_agent"])
    print(f"  → 获取到 {len(raw_data)} 篇")

    count = {"buildings": 0, "critters": 0, "food": 0, "geysers": 0,
             "resources": 0, "plants": 0, "diseases": 0}

    for doc_type, titles in all_titles.items():
        print(f"\n📋 处理 {doc_type}...")
        for title in titles:
            wt = raw_data.get(title)
            if not wt:
                continue
            slug = title.lower().replace(' ', '-').replace("'", '').replace('(', '').replace(')', '')
            slug = re.sub(r'[^a-z0-9\-]', '', slug)

            if doc_type == "buildings":
                params = extract_infobox(wt, "Infobox Building")
                power = parse_numeric(params.get("power", ""))
                if power is not None:
                    power = int(abs(power))  # ONI power is usually negative (consumption)
                heat = parse_numeric(params.get("heat", ""))
                material = clean_value(params.get("construction_cost", ""))
                dims = clean_value(params.get("dimensions", ""))
                cat = clean_value(params.get("category", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO buildings
                        (slug, name, power, heat, material_cost, dimensions, category)
                        VALUES (?,?,?,?,?,?,?)""",
                        (slug, title, power, heat, material, dims, cat))
                    count["buildings"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "critters":
                params = extract_infobox(wt, "Infobox Critter")
                cal = parse_numeric(params.get("calories", ""))
                repro = clean_value(params.get("reproduction", ""))
                temp = clean_value(params.get("temperature_range", "")) or clean_value(params.get("temp", ""))
                diet = clean_value(params.get("diet", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO critters
                        (slug, name, calories, reproduction, temp_range, diet)
                        VALUES (?,?,?,?,?,?)""",
                        (slug, title, int(cal) if cal else None, repro, temp, diet))
                    count["critters"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "food":
                params = extract_infobox(wt, "Infobox Food")
                # ONI uses "kcal" not "calories"
                cal = parse_numeric(params.get("kcal", ""))
                if cal is None:
                    cal = parse_numeric(params.get("calories", ""))
                qual = parse_numeric(params.get("quality", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO food
                        (slug, name, calories, quality)
                        VALUES (?,?,?,?)""",
                        (slug, title, int(cal) if cal else None, int(qual) if qual else None))
                    count["food"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "geysers":
                params = extract_infobox(wt, "Infobox Geyser")
                output = clean_value(params.get("output", ""))
                temp = parse_numeric(params.get("temperature", ""))
                pressure = parse_numeric(params.get("pressure", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO geysers
                        (slug, name, output, temperature, pressure)
                        VALUES (?,?,?,?,?)""",
                        (slug, title, output, temp, pressure))
                    count["geysers"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "resources":
                params = extract_infobox(wt, "Infobox Resource")
                tc = parse_numeric(params.get("thermal_conductivity", ""))
                shc = parse_numeric(params.get("specific_heat_capacity", ""))
                mp = parse_numeric(params.get("melting_point", ""))
                cat = clean_value(params.get("category", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO resources
                        (slug, name, thermal_conductivity, specific_heat_capacity, melting_point, category)
                        VALUES (?,?,?,?,?,?)""",
                        (slug, title, tc, shc, mp, cat))
                    count["resources"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "plants":
                params = extract_infobox(wt, "Infobox Plant")
                growth = parse_numeric(params.get("growth_time", ""))
                harvest = clean_value(params.get("harvest", ""))
                temp = clean_value(params.get("temperature_range", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO plants
                        (slug, name, growth_time, harvest, temp_range)
                        VALUES (?,?,?,?,?)""",
                        (slug, title, growth, harvest, temp))
                    count["plants"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "diseases":
                params = extract_infobox(wt, "Infobox Disease")
                effects = clean_value(params.get("effects", ""))
                treatment = clean_value(params.get("treatment", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO diseases
                        (slug, name, effects, treatment)
                        VALUES (?,?,?,?)""",
                        (slug, title, effects, treatment))
                    count["diseases"] += 1
                except sqlite3.IntegrityError:
                    pass

    db.commit()
    db.close()

    print(f"\n✅ ONI 数据库构建完成：{cfg['output']}")
    for t, n in count.items():
        print(f"  {t}: {n} 条")
    total = sum(count.values())
    print(f"  总计：{total} 条")


# ═══════════════════════════════════════════════
# ║  Silksong
# ═══════════════════════════════════════════════

SILKSONG_CONFIG = {
    "api": "https://hollowknight.fandom.com/api.php",
    "user_agent": "GameGuideBot/1.0 (Silksong structured data builder)",
    "output": GAMES_DIR / "silksong" / "silksong_data.db",
    "categories": {
        "Bosses_(Silksong)": "bosses",
        "Enemies_(Silksong)": "enemies",
        "Abilities_(Silksong)": "abilities",
        "Items_(Silksong)": "items",
        "Areas_(Silksong)": "areas",
        "NPCs_(Silksong)": "npcs",
    },
    "extra_abilities": [
        "Weaver Talents (Silksong)",
        "Clawline (Silksong)",
    ],
    "extra_areas": [
        "The Abyss (Silksong)",
    ]
}

SILKSONG_SCHEMA = """
CREATE TABLE IF NOT EXISTS bosses (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hp INTEGER,
    location TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS enemies (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hp INTEGER,
    location TEXT
);

CREATE TABLE IF NOT EXISTS abilities (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    effect TEXT,
    acquisition TEXT
);

CREATE TABLE IF NOT EXISTS items (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    effect TEXT,
    location TEXT
);

CREATE TABLE IF NOT EXISTS areas (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS npcs (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT,
    location TEXT,
    description TEXT
);
"""


def build_silksong_db():
    cfg = SILKSONG_CONFIG
    print(f"\n{'='*50}")
    print(f"🪱 Silksong — 构建结构化数据库")
    print(f"{'='*50}")

    db = get_db(cfg["output"])
    db.executescript(SILKSONG_SCHEMA)

    all_titles = {}
    for cat_name, doc_type in cfg["categories"].items():
        titles = get_category_members(cfg["api"], cat_name, cfg["user_agent"])
        all_titles[doc_type] = [t for t in titles
                                if not t.startswith(('User:', 'Template:', 'File:', 'Category:', 'Module:'))]
        print(f"  {doc_type}: {len(all_titles[doc_type])} pages")

    all_to_fetch = []
    for titles in all_titles.values():
        all_to_fetch.extend(titles)
    print(f"\n📥 获取原始 wikitext（{len(all_to_fetch)} 篇）...")
    raw_data = fetch_raw_wikitext(cfg["api"], all_to_fetch, cfg["user_agent"])
    print(f"  → 获取到 {len(raw_data)} 篇")

    count = {"bosses": 0, "enemies": 0, "abilities": 0, "items": 0, "areas": 0, "npcs": 0}

    for doc_type, titles in all_titles.items():
        print(f"\n📋 处理 {doc_type}...")
        for title in titles:
            wt = raw_data.get(title)
            if not wt:
                continue
            slug = title.lower().replace(' ', '-').replace("'", '').replace('(', '').replace(')', '')
            slug = re.sub(r'[^a-z0-9\-]', '', slug)

            if doc_type in ("bosses", "enemies"):
                # Try different infobox templates
                params = extract_infobox(wt, "HK Infobox Enemy")
                if not params:
                    params = extract_infobox(wt, "HK Infobox Boss")
                if not params:
                    params = extract_infobox(wt, "infobox enemy")
                if not params:
                    params = extract_infobox(wt, "SS Infobox NPC")

                hp = parse_numeric(params.get("health", "")) or parse_numeric(params.get("hp", ""))
                loc = clean_value(params.get("location", "")) or clean_value(params.get("area", ""))
                desc = clean_value(params.get("description", ""))

                # Try to find HP in body text if not in infobox
                if hp is None:
                    hp_text = re.search(r'(?i)(?:health|hp)\s*[:：]?\s*(\d+)', wt)
                    if hp_text:
                        hp = int(hp_text.group(1))

                try:
                    if doc_type == "bosses":
                        db.execute("""INSERT OR REPLACE INTO bosses
                            (slug, name, hp, location, description)
                            VALUES (?,?,?,?,?)""",
                            (slug, title, hp, loc, desc))
                        count["bosses"] += 1
                    else:
                        db.execute("""INSERT OR REPLACE INTO enemies
                            (slug, name, hp, location)
                            VALUES (?,?,?,?)""",
                            (slug, title, hp, loc))
                        count["enemies"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "abilities":
                params = extract_infobox(wt, "HK Infobox Ability")
                effect = clean_value(params.get("effect", "")) or clean_value(params.get("description", ""))
                acq = clean_value(params.get("acquisition", "")) or clean_value(params.get("source", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO abilities
                        (slug, name, effect, acquisition)
                        VALUES (?,?,?,?)""",
                        (slug, title, effect, acq))
                    count["abilities"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "items":
                params = extract_infobox(wt, "HK Infobox Item")
                effect = clean_value(params.get("effect", ""))
                loc = clean_value(params.get("location", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO items
                        (slug, name, effect, location)
                        VALUES (?,?,?,?)""",
                        (slug, title, effect, loc))
                    count["items"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "areas":
                params = extract_infobox(wt, "HK Infobox Area")
                desc = clean_value(params.get("description", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO areas
                        (slug, name, description)
                        VALUES (?,?,?)""",
                        (slug, title, desc))
                    count["areas"] += 1
                except sqlite3.IntegrityError:
                    pass

            elif doc_type == "npcs":
                params = extract_infobox(wt, "SS Infobox NPC")
                if not params:
                    params = extract_infobox(wt, "HK Infobox NPC")
                role = clean_value(params.get("role", "")) or clean_value(params.get("title", ""))
                loc = clean_value(params.get("location", "")) or clean_value(params.get("area", ""))
                desc = clean_value(params.get("description", ""))

                try:
                    db.execute("""INSERT OR REPLACE INTO npcs
                        (slug, name, role, location, description)
                        VALUES (?,?,?,?,?)""",
                        (slug, title, role, loc, desc))
                    count["npcs"] += 1
                except sqlite3.IntegrityError:
                    pass

    # ── Fetch extra pages not covered by categories ──
    extra_titles = cfg.get("extra_abilities", []) + cfg.get("extra_areas", [])
    if extra_titles:
        print(f"\n📥 获取额外页面（{len(extra_titles)} 篇）...")
        extra_raw = fetch_raw_wikitext(cfg["api"], extra_titles, cfg["user_agent"])
        for title in extra_titles:
            wt = extra_raw.get(title)
            if not wt:
                continue
            slug = title.lower().replace(' ', '-').replace("'", '').replace('(', '').replace(')', '')
            slug = re.sub(r'[^a-z0-9\-]', '', slug)

            if title in cfg.get("extra_abilities", []):
                params = extract_infobox(wt, "HK Infobox Ability")
                effect = clean_value(params.get("effect", "")) or clean_value(params.get("description", ""))
                acq = clean_value(params.get("acquisition", "")) or clean_value(params.get("source", ""))
                try:
                    db.execute("""INSERT OR REPLACE INTO abilities
                        (slug, name, effect, acquisition)
                        VALUES (?,?,?,?)""",
                        (slug, title, effect, acq))
                    count["abilities"] += 1
                    print(f"    + abilities: {title}")
                except sqlite3.IntegrityError:
                    pass

            elif title in cfg.get("extra_areas", []):
                params = extract_infobox(wt, "HK Infobox Area")
                desc = clean_value(params.get("description", ""))
                try:
                    db.execute("""INSERT OR REPLACE INTO areas
                        (slug, name, description)
                        VALUES (?,?,?)""",
                        (slug, title, desc))
                    count["areas"] += 1
                    print(f"    + areas: {title}")
                except sqlite3.IntegrityError:
                    pass

    db.commit()
    db.close()

    print(f"\n✅ Silksong 数据库构建完成：{cfg['output']}")
    for t, n in count.items():
        print(f"  {t}: {n} 条")
    total = sum(count.values())
    print(f"  总计：{total} 条")


# ═══════════════════════════════════════════════
# ║  Main
# ═══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="构建游戏结构化数据库")
    parser.add_argument("--game", "-g", required=True,
                        choices=["terraria", "oni", "silksong", "all"],
                        help="要构建的游戏")
    args = parser.parse_args()

    games = {
        "terraria": build_terraria_db,
        "oni": build_oni_db,
        "silksong": build_silksong_db,
    }

    if args.game == "all":
        for name, builder in games.items():
            builder()
    else:
        games[args.game]()

    print("\n🎉 全部完成！")


if __name__ == "__main__":
    main()
