#!/usr/bin/env python3
"""
Build Monster Hunter Wilds (怪物猎人荒野) SQLite database + wiki data.

爬取 Monster Hunter Fandom Wiki 中 MHWilds 相关页面，
生成 wiki_data.md（用于向量库）和 mhw_data.db（结构化数据）。
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
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAME_DIR = PROJECT_ROOT / "games" / "mhw"
DATA_DIR = GAME_DIR / "data"
DB_PATH = GAME_DIR / "mhw_data.db"
WIKI_PATH = DATA_DIR / "wiki_data.md"

API_URL = "https://monsterhunter.fandom.com/api.php"
HEADERS = {
    "User-Agent": "GameGuideBot/1.0 (building structured DB for personal project)"
}

# MH Wilds pages to scrape
MHW_PAGES = [
    "MHWilds",
    "MHWilds: Monsters",
    "MHWilds: Weapons",
    "MHWilds: Armors",
    "MHWilds: Skills",
    "MHWilds: Locations",
    "MHWilds: Item List",
    "MHWilds: Decorations",
    "MHWilds: Charm List",
    "MHWilds: Decoration List",
    "MHWilds: Low Rank Armor",
    "MHWilds: High Rank Armor",
    "MHWilds: Event Armor",
    "MHWilds: Endemic Life List",
    "MHWilds: Monster Material List",
    "MHWilds: Bow Weapon Tree",
    "MHWilds: Charge Blade Weapon Tree",
    "MHWilds: Dual Blades Weapon Tree",
    "MHWilds: Great Sword Weapon Tree",
    "MHWilds: Gunlance Weapon Tree",
    "MHWilds: Hammer Weapon Tree",
    "MHWilds: Heavy Bowgun Weapon Tree",
    "MHWilds: Hunting Horn Weapon Tree",
    "MHWilds: Insect Glaive Weapon Tree",
    "MHWilds: Kinsect Tree",
    "MHWilds: Lance Weapon Tree",
    "MHWilds: Light Bowgun Weapon Tree",
    "MHWilds: Long Sword Weapon Tree",
    "MHWilds: Switch Axe Weapon Tree",
    "MHWilds: Sword and Shield Weapon Tree",
    "MHWilds: Horn Melodies",
    "MHWilds: Assigned Quests",
    "MHWilds: Assignments",
    "MHWilds: Optional Quests",
    "MHWilds: Event Quests",
    "MHWilds: Missions",
    "MHWilds: Turf War List",
]

# Weapon tree pages (for structured weapon data parsing)
WEAPON_TREE_PAGES = [
    "MHWilds: Bow Weapon Tree",
    "MHWilds: Charge Blade Weapon Tree",
    "MHWilds: Dual Blades Weapon Tree",
    "MHWilds: Great Sword Weapon Tree",
    "MHWilds: Gunlance Weapon Tree",
    "MHWilds: Hammer Weapon Tree",
    "MHWilds: Heavy Bowgun Weapon Tree",
    "MHWilds: Hunting Horn Weapon Tree",
    "MHWilds: Insect Glaive Weapon Tree",
    "MHWilds: Kinsect Tree",
    "MHWilds: Lance Weapon Tree",
    "MHWilds: Light Bowgun Weapon Tree",
    "MHWilds: Long Sword Weapon Tree",
    "MHWilds: Switch Axe Weapon Tree",
    "MHWilds: Sword and Shield Weapon Tree",
]

# Also fetch individual monster pages
MONSTER_PAGES = [
    "Ajarakan",
    "Arkveld",
    "Balahara",
    "Blangonga",
    "Chatacabra",
    "Congalala",
    "Doshaguma",
    "Gore Magala",
    "Gravios",
    "Guardian Arkveld",
    "Guardian Doshaguma",
    "Guardian Ebony Odogaron",
    "Guardian Fulgur Anjanath",
    "Guardian Rathalos",
    "Guardian Seikret",
    "Hirabami",
    "Jin Dahaad",
    "Lala Barina",
    "Nerscylla",
    "Nu Udra",
    "Quematrice",
    "Rathalos",
    "Rathian",
    "Rey Dau",
    "Rompopolo",
    "Uth Duna",
    "Xu Wu",
    "Yian Kut-Ku",
    "Zoh Shia",
]

ALL_PAGES = MHW_PAGES + MONSTER_PAGES


def api_request(params: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Send request to Fandom API."""
    params["format"] = "json"
    query_string = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{API_URL}?{query_string}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  API request failed: {e}")
        return None


def fetch_page_content(title: str) -> Optional[str]:
    """Fetch rendered page content (text) from wiki."""
    params = {
        "action": "parse",
        "page": title,
        "prop": "text",
        "format": "json",
    }
    data = api_request(params)
    if not data or "parse" not in data:
        return None
    
    html = data["parse"]["text"].get("*", "")
    
    # Strip HTML tags, keep text
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    
    # Decode HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'")
    
    return text


def clean_text(raw: str) -> str:
    """Clean raw HTML cell content to plain text."""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    # Decode HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'")
    text = text.replace("&#160;", " ").replace("&nbsp;", " ")
    return text.strip()


def extract_infobox_data(html: str) -> Dict[str, str]:
    """Extract infobox key-value pairs from page HTML.

    Supports portable infobox (<aside class="portable-infobox">) used
    by MH Fandom wiki, and traditional table-based infobox as fallback.

    For portable infobox, uses data-source attribute as key,
    and extracts the pi-data-value text content as value.
    """
    data = {}

    # Try portable infobox first
    pi_match = re.search(
        r'<aside[^>]*class="[^"]*portable-infobox[^"]*"[^>]*>(.*?)</aside>',
        html, re.DOTALL | re.IGNORECASE
    )

    if pi_match:
        infobox_html = pi_match.group(1)

        # Extract data-source and value pairs.
        # Pattern: data-source="KEY" ... <div class="pi-data-value ...">VALUE</div>
        # Value div is the one immediately after the data-source attribute in the same container.
        items = re.findall(
            r'data-source="([^"]+)"(?:[^>]*>.*?)<div[^>]*class="[^"]*pi-data-value[^"]*"[^>]*>(.*?)</div>',
            infobox_html, re.DOTALL
        )

        for source_key, value_html in items:
            value = re.sub(r"<[^>]+>", " ", value_html).strip()
            value = re.sub(r"\s+", " ", value).strip()
            # Skip meta fields
            if source_key.lower() in ("image", "name", "japanese name", "japanese title",
                                      "english title", "chinese name", "chinese title",
                                      "korean name", "korean title"):
                continue
            if value and value != "(Unknown)":
                data[source_key] = value

        return data

    # Fallback: traditional table-based infobox
    table_match = re.search(
        r'<table[^>]*class="[^"]*infobox[^"]*"[^>]*>(.*?)</table>',
        html, re.DOTALL | re.IGNORECASE
    )
    if not table_match:
        return data

    infobox_html = table_match.group(1)

    # Extract rows
    rows = re.findall(
        r'<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>',
        infobox_html, re.DOTALL
    )

    for th, td in rows:
        key = re.sub(r"<[^>]+>", " ", th).strip()
        value = re.sub(r"<[^>]+>", " ", td).strip()
        value = re.sub(r"\s+", " ", value).strip()
        if key and value:
            data[key] = value

    return data


def parse_monster_page(title: str, html: str) -> Optional[Dict[str, Any]]:
    """Parse a monster page into structured data."""
    info = extract_infobox_data(html)
    if not info:
        return None

    monster = {
        "name": title,
        "species": info.get("Monster Type", ""),
        "locations": info.get("Habitats", ""),
        "weaknesses": info.get("Weakest to", ""),
        "hp": "",  # Not in infobox
        "size": info.get("Monster Size", ""),
        "elements": info.get("Element", ""),
        "ailments": info.get("Ailments", ""),
    }
    return monster


def build_wiki_md() -> List[Dict[str, Any]]:
    """Build wiki_data.md from all pages."""
    print(f"📖 Fetching {len(ALL_PAGES)} pages from MH Wilds wiki...")
    
    all_texts = []
    monsters_data = []
    
    for i, page in enumerate(ALL_PAGES, 1):
        print(f"  [{i}/{len(ALL_PAGES)}] {page}...", end=" ")
        sys.stdout.flush()
        
        content = fetch_page_content(page)
        if not content:
            print("❌ (no content)")
            time.sleep(0.5)
            continue
        
        # Save text for wiki_data.md
        entry = f"# {page}\n\n{content}\n"
        all_texts.append(entry)
        
        # Try to extract structured data
        monster_data = parse_monster_page(page, content)
        if monster_data:
            monsters_data.append(monster_data)
            print(f"✅ ({len(content)} chars, monster data extracted)")
        else:
            print(f"✅ ({len(content)} chars)")
        
        time.sleep(0.5)  # Rate limiting
    
    # Write wiki_data.md
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_PATH.write_text("\n\n---\n\n".join(all_texts), encoding="utf-8")
    print(f"\n📝 Wiki data written: {WIKI_PATH} ({WIKI_PATH.stat().st_size} bytes, {len(all_texts)} pages)")
    
    return monsters_data


def parse_weapon_tree_html(html: str, weapon_type: str) -> List[Dict[str, Any]]:
    """Parse weapon tree HTML table into structured weapon data.

    MH Fandom wiki weapon tree pages use <table class="themetable">
    with columns varying by weapon type.
    """
    weapons = []
    themetables = re.findall(
        r'<table class="themetable"[^>]*>(.*?)</table>', html, re.DOTALL
    )

    for themetable in themetables:
        trs = re.findall(r'<tr>(.*?)</tr>', themetable, re.DOTALL)
        if len(trs) < 2:
            continue

        # Find header row (contains "Weapon Name")
        header_row = None
        header_idx = None
        for i, tr in enumerate(trs):
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL)
            raw = [clean_text(c) for c in cells]
            if any('Weapon Name' in r for r in raw):
                header_row = raw
                header_idx = i
                break

        if not header_row:
            continue

        # Parse data rows after header
        for tr in trs[header_idx + 1:]:
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL)
            if len(cells) != len(header_row):
                continue

            values = [clean_text(c) for c in cells]
            if not values[0] or values[0].lower() == 'weapon name':
                continue

            weapon: Dict[str, Any] = {
                'name': values[0],
                'type': weapon_type,
                'attack': values[1] if len(values) > 1 else '',
                'element': values[2] if len(values) > 2 else '',
            }

            # Map remaining columns by header name
            for j in range(3, len(values)):
                h = header_row[j].lower()
                v = values[j]
                if 'sharpness' in h:
                    weapon['sharpness'] = v
                elif 'slot' in h:
                    weapon['slots'] = v
                elif 'affinity' in h:
                    weapon['affinity'] = v
                elif h in ('def', 'defense'):
                    weapon['defense'] = v
                elif 'skill' in h:
                    weapon['skills'] = v
                elif 'coating' in h:
                    weapon['coatings'] = v
                elif 'kinsect' in h:
                    weapon['kinsect_level'] = v
                elif 'note' in h:
                    weapon['notes'] = v
                elif 'melod' in h:
                    weapon['melodies'] = v
                elif 'rarity' in h:
                    weapon['rarity'] = v

            weapons.append(weapon)

    return weapons


def fetch_page_raw_html(title: str) -> Optional[str]:
    """Fetch page and return raw HTML (for table parsing)."""
    params = {"action": "parse", "page": title, "prop": "text", "format": "json"}
    data = api_request(params)
    if not data or "parse" not in data:
        return None
    return data["parse"]["text"].get("*", "")


def fetch_weapons_from_pages() -> List[Dict[str, Any]]:
    """Fetch weapon tree pages and parse structured weapon data."""
    all_weapons = []

    type_map = {
        "MHWilds: Bow Weapon Tree": "Bow",
        "MHWilds: Charge Blade Weapon Tree": "Charge Blade",
        "MHWilds: Dual Blades Weapon Tree": "Dual Blades",
        "MHWilds: Great Sword Weapon Tree": "Great Sword",
        "MHWilds: Gunlance Weapon Tree": "Gunlance",
        "MHWilds: Hammer Weapon Tree": "Hammer",
        "MHWilds: Heavy Bowgun Weapon Tree": "Heavy Bowgun",
        "MHWilds: Hunting Horn Weapon Tree": "Hunting Horn",
        "MHWilds: Insect Glaive Weapon Tree": "Insect Glaive",
        "MHWilds: Lance Weapon Tree": "Lance",
        "MHWilds: Light Bowgun Weapon Tree": "Light Bowgun",
        "MHWilds: Long Sword Weapon Tree": "Long Sword",
        "MHWilds: Switch Axe Weapon Tree": "Switch Axe",
        "MHWilds: Sword and Shield Weapon Tree": "Sword and Shield",
    }

    print(f"\n🔫 Fetching {len(WEAPON_TREE_PAGES)} weapon tree pages...")
    for i, page in enumerate(WEAPON_TREE_PAGES, 1):
        weapon_type = type_map.get(page, page.replace("MHWilds: ", ""))
        print(f"  [{i}/{len(WEAPON_TREE_PAGES)}] {weapon_type}...", end=" ")
        sys.stdout.flush()

        html = fetch_page_raw_html(page)
        if not html:
            print("❌ (fetch failed)")
            time.sleep(0.5)
            continue

        parsed = parse_weapon_tree_html(html, weapon_type)
        if parsed:
            all_weapons.extend(parsed)
            print(f"✅ {len(parsed)} weapons")
        else:
            print("⚠️  (no weapons parsed)")

        time.sleep(0.5)

    return all_weapons


def parse_skills_from_wiki() -> List[Dict[str, Any]]:
    """Parse MHW skills from wiki_data.md text."""
    import re, json

    with open(str(WIKI_PATH), encoding="utf-8") as f:
        content = f.read()

    JP = '\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff\u3000-\u303f\u2160-\u217f\uff00-\uffef\u3400-\u4dbf'

    skills_pos = content.find('# MHWilds: Skills')
    if skills_pos < 0:
        return []

    lines = content[skills_pos:skills_pos+60000].split('\n')
    if len(lines) < 3:
        return []

    data_line = lines[2]
    idx = data_line.find('Airborne')
    if idx < 0:
        return []

    data = data_line[idx:]

    boundary = r'(?<=[.\d)])\s+(?=[A-Z][a-z]+[\w\s\'\/\-]+ [' + JP + r'])'
    segments = re.split(boundary, data)

    def _parse_one(text):
        m = re.match(r'^([A-Za-z][A-Za-z\s\'.\/\-]*)', text)
        if not m:
            return None

        name = m.group(1).strip()
        rest = text[m.end():].strip()

        desc_start = re.search(
            r'\s+([A-Z][a-z]+(?:ed|es|s|ing|ly)\b)|'
            r'(?<![A-Z])\s+(?:Increases|Extends|Grants|Reduces|Powers|Allows|Lets|Has|The|Once|While|Temporarily|Prevents|Delays|Shortens|Slightly|Moderately|Greatly|Nullifies|Activates|Enables|Adds|Makes|Pro)|'
            r'(?<![A-Z])\s+(?:Restores|Recovers|Infects|Grants|Regenerates|Level|Further|Additionally|Greatly)',
            rest
        )

        if desc_start:
            jp_text = rest[:desc_start.start()].strip()
            after_jp = rest[desc_start.start():].strip()
        else:
            jp_text = rest
            after_jp = ""

        if not after_jp:
            return {"name": name, "name_jp": jp_text, "description": "", "max_level": 0, "levels_json": "[]"}

        level_pattern = r'(\d+)\s+(.+?)(?=\s+\d+\s+[A-Z' + JP + r']|\s*$)'
        levels = re.findall(level_pattern, after_jp)

        if levels:
            first_lv_pos = re.search(
                r'(?<!\d)\b(\d+)\s+(?:[A-Z][a-z]|Bonus|Activates|Extends|Increases|Grants|Slightly|Moderately|Greatly|Reduces|Nullifies|Delays|Prevents|Allows|Lets|Enables|Temporarily|While|The|Once|When|After|Makes|Pro|Restores|Recovers|Infects|Regenerates|Level|Further|Additionally|Greatly)',
                after_jp
            )
            if first_lv_pos:
                description = after_jp[:first_lv_pos.start()].strip()
                level_data = after_jp[first_lv_pos.start():]
                level_list = re.findall(r'(\d+)\s+(.+?)(?=\s+\d+\s+[A-Z' + JP + r']|\s*$)', level_data)
            else:
                description = after_jp
                level_list = levels
        else:
            description = after_jp
            level_list = []

        max_level = max(int(l[0]) for l in level_list) if level_list else 1
        level_effects = [f"Lv{l[0]}: {l[1].strip()}" for l in level_list]

        return {
            "name": name,
            "name_jp": jp_text,
            "description": description,
            "max_level": max_level,
            "levels_json": json.dumps(level_effects)
        }

    results = []
    for seg in segments:
        s = _parse_one(seg)
        if s:
            results.append(s)
    return results


def build_database(monsters: List[Dict[str, Any]], weapons: List[Dict[str, Any]]):
    """Build SQLite database for MH Wilds."""
    print(f"\n🗄️  Building database: {DB_PATH}")

    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.cursor()

    # ── monsters table ──
    cur.execute("""
        CREATE TABLE monsters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            species TEXT,
            locations TEXT,
            weaknesses TEXT,
            hp TEXT,
            size TEXT,
            elements TEXT,
            ailments TEXT
        )
    """)

    for m in monsters:
        cur.execute("""
            INSERT OR IGNORE INTO monsters
                (name, species, locations, weaknesses, hp, size, elements, ailments)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            m["name"], m["species"], m["locations"],
            m["weaknesses"], m["hp"], m["size"],
            m["elements"], m["ailments"]
        ))

    # ── weapons table ──
    cur.execute("""
        CREATE TABLE weapons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            attack TEXT,
            element TEXT,
            sharpness TEXT,
            slots TEXT,
            affinity TEXT,
            defense TEXT,
            skills TEXT,
            coatings TEXT,
            kinsect_level TEXT,
            notes TEXT,
            melodies TEXT,
            rarity TEXT,
            page_title TEXT
        )
    """)

    for w in weapons:
        cur.execute("""
            INSERT INTO weapons
                (name, type, attack, element, sharpness, slots, affinity,
                 defense, skills, coatings, kinsect_level, notes, melodies, rarity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            w.get("name", ""), w.get("type", ""),
            w.get("attack", ""), w.get("element", ""),
            w.get("sharpness", ""), w.get("slots", ""),
            w.get("affinity", ""), w.get("defense", ""),
            w.get("skills", ""), w.get("coatings", ""),
            w.get("kinsect_level", ""), w.get("notes", ""),
            w.get("melodies", ""), w.get("rarity", ""),
        ))

    # ── armor table (empty, need HTML parser) ──
    cur.execute("""
        CREATE TABLE armor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            set_name TEXT,
            piece TEXT,
            defense TEXT,
            slots TEXT,
            skills TEXT,
            page_title TEXT
        )
    """)

    # ── skills table ──
    cur.execute("""
        CREATE TABLE skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            name_jp TEXT,
            description TEXT,
            max_level INTEGER,
            levels_json TEXT
        )
    """)
    # Parse skills from wiki_data.md
    if WIKI_PATH.exists():
        try:
            skills = parse_skills_from_wiki()
            for s in skills:
                cur.execute(
                    "INSERT INTO skills (name, name_jp, description, max_level, levels_json) VALUES (?, ?, ?, ?, ?)",
                    (s["name"], s["name_jp"], s["description"], s["max_level"], s["levels_json"])
                )
            print(f"  Skills: {len(skills)}")
        except Exception as e:
            print(f"  Skills: error - {e}")
    else:
        print(f"  Skills: WIKI_PATH not found at {WIKI_PATH}")

    # ── items table (empty) ──
    cur.execute("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            description TEXT,
            rarity TEXT,
            buy_price TEXT,
            sell_price TEXT,
            category TEXT
        )
    """)

    # ── locations table (empty) ──
    cur.execute("""
        CREATE TABLE locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            description TEXT,
            areas TEXT
        )
    """)

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM monsters")
    print(f"  Monsters: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM weapons")
    print(f"  Weapons: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM skills")
    print(f"  Skills: {cur.fetchone()[0]}")

    conn.close()
    print(f"✅ Database: {DB_PATH}")


def verify_database():
    """Verify the database contents."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    print(f"\n📊 Database tables ({len(tables)}): {', '.join(tables)}")

    for table in tables:
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        count = cur.fetchone()[0]
        print(f"  {table}: {count} records")

    print("\n  Sample monsters:")
    cur.execute("SELECT name, species, locations FROM monsters LIMIT 5")
    for row in cur.fetchall():
        print(f"    - {row['name']} ({row['species']}) - {str(row['locations'])[:40] if row['locations'] else 'N/A'}")

    print("\n  Sample weapons (by type):")
    cur.execute("SELECT type, COUNT(*) as cnt FROM weapons GROUP BY type ORDER BY cnt DESC")
    for row in cur.fetchall():
        print(f"    {row['type']}: {row['cnt']} weapons")

    print("\n  First 5 weapons:")
    cur.execute("SELECT name, type, attack, element, affinity, skills FROM weapons LIMIT 5")
    for row in cur.fetchall():
        print(f"    - {row['name']} ({row['type']}) ATK:{row['attack']} ELE:{row['element']} AFF:{row['affinity']}")

    conn.close()


def main():
    print("=" * 60)
    print("  Monster Hunter Wilds DB Builder")
    print("=" * 60)

    # Step 1: Build wiki data
    monsters = build_wiki_md()

    # Step 2: Parse weapon trees
    weapons = fetch_weapons_from_pages()

    # Step 3: Build database
    build_database(monsters, weapons)

    # Step 4: Verify
    verify_database()

    print("\n✅ Done!")


if __name__ == "__main__":
    main()
