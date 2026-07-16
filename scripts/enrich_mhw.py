#!/usr/bin/env python3
"""
Enrich Monster Hunter Wilds database with weapon + armor data.
Parses weapon tree pages and armor pages from Fandom Wiki HTML.
"""

import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAME_DIR = PROJECT_ROOT / "games" / "mhw"
DB_PATH = GAME_DIR / "mhw_data.db"
WIKI_PATH = GAME_DIR / "data" / "wiki_data.md"

API_URL = "https://monsterhunter.fandom.com/api.php"
HEADERS = {"User-Agent": "GameGuideBot/1.0 (enriching MHW structured data)"}

# ── Weapon tree pages ──
WEAPON_TREE_PAGES = {
    "MHWilds: Great Sword Weapon Tree": "Great Sword",
    "MHWilds: Long Sword Weapon Tree": "Long Sword",
    "MHWilds: Sword and Shield Weapon Tree": "Sword and Shield",
    "MHWilds: Dual Blades Weapon Tree": "Dual Blades",
    "MHWilds: Hammer Weapon Tree": "Hammer",
    "MHWilds: Hunting Horn Weapon Tree": "Hunting Horn",
    "MHWilds: Lance Weapon Tree": "Lance",
    "MHWilds: Gunlance Weapon Tree": "Gunlance",
    "MHWilds: Switch Axe Weapon Tree": "Switch Axe",
    "MHWilds: Charge Blade Weapon Tree": "Charge Blade",
    "MHWilds: Insect Glaive Weapon Tree": "Insect Glaive",
    "MHWilds: Light Bowgun Weapon Tree": "Light Bowgun",
    "MHWilds: Heavy Bowgun Weapon Tree": "Heavy Bowgun",
    "MHWilds: Bow Weapon Tree": "Bow",
}

# ── Armor pages ──
ARMOR_PAGES = [
    "MHWilds: Low Rank Armor",
    "MHWilds: High Rank Armor",
    "MHWilds: Event Armor",
]


# ════════════════════════════════════════════════════════════
#  Utility
# ════════════════════════════════════════════════════════════

def api_get_html(page: str) -> Optional[str]:
    """Fetch rendered HTML from Fandom API."""
    params = {"action": "parse", "page": page, "prop": "text", "format": "json"}
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{API_URL}?{qs}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["parse"]["text"]["*"]
    except Exception as e:
        print(f"  ⚠️  Failed to fetch '{page}': {e}")
        return None


def clean_html_text(html: str) -> str:
    """Strip HTML tags, collapse whitespace, decode entities."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("&#160;", "").replace("&nbsp;", "")
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'")
    return text


# ════════════════════════════════════════════════════════════
#  Weapon tree parser
# ════════════════════════════════════════════════════════════

def extract_weapons_from_html(page: str, weapon_type: str) -> List[Dict[str, str]]:
    """Extract weapon rows from a weapon tree page HTML."""
    html = api_get_html(page)
    if not html:
        return []

    # Find all <table> elements
    tables = re.findall(r'<table[^>]*>.*?</table>', html, re.DOTALL | re.IGNORECASE)
    weapons = []

    for tbl in tables:
        # Skip tables without "Weapon Name" header
        if 'Weapon Name' not in tbl and 'weapon name' not in tbl.lower():
            continue

        # Find tree name from spanning header row
        tree_match = re.search(
            r'<th[^>]*colspan="?\d+"?[^>]*>(.*?)</th>',
            tbl, re.DOTALL
        )
        tree_name = clean_html_text(tree_match.group(1)) if tree_match else ""

        # Find the header row (row with th elements, excluding colspan headers)
        rows = re.findall(r'<tr>(.*?)</tr>', tbl, re.DOTALL)

        # Identify column names from header row
        col_names = []  # ordered list of column names
        for row in rows:
            ths = re.findall(r'<th[^>]*>(.*?)</th>', row, re.DOTALL)
            if len(ths) >= 7:  # minimum weapon columns
                # Filter out spanning headers (very short text like a single number or empty)
                for th in ths:
                    label = clean_html_text(th)
                    if label and label not in ("", "?", "Notes:") and len(label) > 1:
                        col_names.append(label)
                # Remove the tree name if present (it's a spanning header, not a column)
                col_names = [c for c in col_names if "Tree" not in c or "Expedition" not in c]
                break

        # If we couldn't identify column names, fall back to position-based mapping
        if not col_names:
            # Detect weapon type from page content
            num_cols = None
            for row in rows:
                tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if len(tds) >= 8 and re.search(r'<a[^>]*>', tds[0]):
                    num_cols = len(tds)
                    break

            if num_cols == 9:
                col_names = ["Weapon Name", "Attack", "Element", "Sharpness",
                             "Slots", "Affinity", "Kinsect Level", "DEF", "Weapon Skills"]
            elif num_cols == 8:
                # Try to guess format from table content
                # Standard: has Sharpness column (images) + Element
                # Bowgun: has Shot Level + Special Ammo, no Sharpness
                is_bowgun_table = "Shot Level" in tbl or "Special Ammo" in tbl
                if is_bowgun_table:
                    col_names = ["Weapon Name", "Attack", "Slots", "Affinity",
                                 "Shot Level", "Special Ammo", "DEF", "Weapon Skills"]
                else:
                    col_names = ["Weapon Name", "Attack", "Element", "Sharpness",
                                 "Slots", "Affinity", "DEF", "Weapon Skills"]
            else:
                continue  # can't map this

        # Extract data rows using column name mapping
        for row in rows:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(tds) < 7:
                continue

            name_match = re.search(r'<a[^>]*>([^<]+)</a>', tds[0])
            if not name_match:
                continue
            name = name_match.group(1).strip()

            weapon = {
                "name": name,
                "type": weapon_type,
                "attack": "",
                "element": "",
                "slots": "",
                "affinity": "",
                "defense": "",
                "skills": "",
                "kinsect_level": "",
                "tree": tree_name,
            }

            # Map td values to column names
            for idx, col in enumerate(col_names):
                if idx >= len(tds):
                    break
                val = clean_html_text(tds[idx])
                col_lower = col.lower()
                if "weapon name" in col_lower or "weapon" == col_lower:
                    # Skip, already got name from link
                    pass
                elif "attack" in col_lower:
                    weapon["attack"] = val
                elif "element" in col_lower:
                    weapon["element"] = val
                elif "slot" in col_lower:
                    weapon["slots"] = val
                elif "affinity" in col_lower:
                    weapon["affinity"] = val
                elif "def" == col_lower or "defense" in col_lower:
                    weapon["defense"] = val
                elif "skill" in col_lower:
                    weapon["skills"] = val
                elif "kinsect" in col_lower:
                    weapon["kinsect_level"] = val
                # Skip: sharpness (image), coatings, shot level, special ammo, etc.

            weapons.append(weapon)

    return weapons


# ════════════════════════════════════════════════════════════
#  Armor table parser
# ════════════════════════════════════════════════════════════

def extract_armor_from_html(page: str, rank: str) -> List[Dict[str, str]]:
    """Extract armor sets from an armor list page."""
    html = api_get_html(page)
    if not html:
        return []

    tables = re.findall(r'<table[^>]*>.*?</table>', html, re.DOTALL | re.IGNORECASE)
    armors = []

    for tbl in tables:
        # Each armor set is in its own table (4 rows: name, image, skills header, skills)
        rows = re.findall(r'<tr>(.*?)</tr>', tbl, re.DOTALL)
        if len(rows) < 4:
            continue

        # Row 0: set name
        name_match = re.search(r'<a[^>]*>([^<]+)</a>', rows[0])
        if not name_match:
            continue
        set_name = name_match.group(1).strip()

        # Row 3: skills
        skills = clean_html_text(rows[3])

        armors.append({
            "set_name": set_name,
            "rank": rank,
            "skills": skills,
        })

    return armors


# ════════════════════════════════════════════════════════════
#  Database insertion
# ════════════════════════════════════════════════════════════

def upgrade_schema(conn: sqlite3.Connection):
    """Ensure all tables have needed columns."""
    cur = conn.cursor()

    # Check existing tables
    tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    if "weapons" in tables:
        existing = {row[1] for row in cur.execute("PRAGMA table_info(weapons)").fetchall()}
        new_cols = {
            "defense": "TEXT DEFAULT ''",
            "skills": "TEXT DEFAULT ''",
            "kinsect_level": "TEXT DEFAULT ''",
            "tree": "TEXT DEFAULT ''",
        }
        for col, dtype in new_cols.items():
            if col not in existing:
                print(f"  ⬆️  Adding column 'weapons.{col}'")
                cur.execute(f"ALTER TABLE weapons ADD COLUMN {col} {dtype}")

    if "armor" in tables:
        existing = {row[1] for row in cur.execute("PRAGMA table_info(armor)").fetchall()}
        for col, dtype in {"rank": "TEXT DEFAULT ''"}.items():
            if col not in existing:
                print(f"  ⬆️  Adding column 'armor.{col}'")
                cur.execute(f"ALTER TABLE armor ADD COLUMN {col} {dtype}")
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS armor (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                set_name TEXT,
                rank TEXT,
                skills TEXT
            )
        """)
        print("  ✅ Created table 'armor'")

    conn.commit()


def insert_weapons(conn: sqlite3.Connection, weapons: List[Dict[str, str]]):
    """Insert weapons, skip duplicates by name+type."""
    cur = conn.cursor()
    inserted = 0
    skipped = 0
    for w in weapons:
        try:
            cur.execute("""
                INSERT OR IGNORE INTO weapons
                    (name, type, attack, element, slots, affinity,
                     defense, skills, kinsect_level, tree, rarity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                w["name"], w["type"], w["attack"], w["element"],
                w["slots"], w["affinity"], w["defense"], w["skills"],
                w["kinsect_level"], w["tree"], "",
            ))
            if cur.rowcount:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ⚠️  Failed to insert {w['name']}: {e}")
    conn.commit()
    return inserted, skipped


def insert_armor(conn: sqlite3.Connection, armors: List[Dict[str, str]]):
    """Insert armor sets."""
    cur = conn.cursor()
    inserted = 0
    skipped = 0
    for a in armors:
        try:
            cur.execute("""
                INSERT OR IGNORE INTO armor
                    (set_name, rank, skills)
                VALUES (?, ?, ?)
            """, (a["set_name"], a["rank"], a["skills"]))
            if cur.rowcount:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ⚠️  Failed to insert {a['set_name']}: {e}")
    conn.commit()
    return inserted, skipped


def print_stats(conn: sqlite3.Connection):
    """Print table row counts."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cur.fetchall()
    print("\n📊 数据库统计:")
    for (t,) in tables:
        cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        count = cur.fetchone()[0]
        print(f"  {t}: {count} 条记录")


# ════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  MH Wilds 数据补全脚本")
    print("=" * 60)

    # ── Connect to existing DB ──
    if not DB_PATH.exists():
        print(f"❌ 数据库不存在: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    upgrade_schema(conn)
    print(f"\n📂 数据库: {DB_PATH}")

    # ── Weapons ──
    print(f"\n🔫 解析武器树 (共 {len(WEAPON_TREE_PAGES)} 个武器类型)...")
    all_weapons = []
    for page, wtype in WEAPON_TREE_PAGES.items():
        print(f"  [{wtype}] {page}...", end=" ")
        sys.stdout.flush()
        weapons = extract_weapons_from_html(page, wtype)
        if weapons:
            print(f"✅ {len(weapons)} 把武器")
        else:
            print("⚠️  未找到数据")
        all_weapons.extend(weapons)
        time.sleep(0.5)

    if all_weapons:
        inserted, skipped = insert_weapons(conn, all_weapons)
        print(f"\n📌 武器入库: {inserted} 新增, {skipped} 跳过 (共 {len(all_weapons)} 条)")
    else:
        print("\n⚠️  未解析到任何武器数据")

    # ── Armor ──
    print(f"\n🛡️  解析防具 (共 {len(ARMOR_PAGES)} 个页面)...")
    all_armor = []
    for page in ARMOR_PAGES:
        rank = "Low Rank" if "Low" in page else "High Rank" if "High" in page else "Event"
        print(f"  [{rank}] {page}...", end=" ")
        sys.stdout.flush()
        armors = extract_armor_from_html(page, rank)
        if armors:
            print(f"✅ {len(armors)} 个套装")
        else:
            print("⚠️  未找到数据")
        all_armor.extend(armors)
        time.sleep(0.5)

    if all_armor:
        inserted, skipped = insert_armor(conn, all_armor)
        print(f"\n📌 防具入库: {inserted} 新增, {skipped} 跳过 (共 {len(all_armor)} 条)")
    else:
        print("\n⚠️  未解析到任何防具数据")

    # ── Stats ──
    print_stats(conn)
    conn.close()

    print("\n✅ MHW 数据补全完成!")
    print("💡 下一步: 在 Mac 上重新构建向量库: python3 scripts/ingest_game.py --game mhw")


if __name__ == "__main__":
    main()
