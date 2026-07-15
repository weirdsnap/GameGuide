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


def build_database(monsters: List[Dict[str, Any]]):
    """Build SQLite database for MH Wilds."""
    print(f"\n🗄️  Building database: {DB_PATH}")
    
    if DB_PATH.exists():
        DB_PATH.unlink()
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
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
            name TEXT,
            type TEXT,
            rarity TEXT,
            attack TEXT,
            affinity TEXT,
            element TEXT,
            slots TEXT,
            sharpness TEXT,
            page_title TEXT
        )
    """)
    
    # ── armor table ──
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
            name TEXT UNIQUE,
            description TEXT,
            max_level INTEGER
        )
    """)
    
    # ── items table ──
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
    
    # ── locations table ──
    cur.execute("""
        CREATE TABLE locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            description TEXT,
            areas TEXT
        )
    """)
    
    conn.commit()
    print(f"  Tables created: monsters({len(monsters)}), weapons, armor, skills, items, locations")
    
    # Database stats
    cur.execute("SELECT COUNT(*) FROM monsters")
    print(f"    Monsters: {cur.fetchone()[0]}")
    
    conn.close()
    print(f"✅ Database: {DB_PATH}")


def verify_database():
    """Verify the database contents."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Table list
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    print(f"\n📊 Database tables ({len(tables)}): {', '.join(tables)}")
    
    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM \"{table}\"")
        count = cur.fetchone()[0]
        print(f"  {table}: {count} records")
    
    # Sample data
    print("\n  Sample monsters:")
    cur.execute("SELECT name, species, locations FROM monsters LIMIT 5")
    for row in cur.fetchall():
        print(f"    - {row['name']} ({row['species']}) - {row['locations'][:40] if row['locations'] else 'N/A'}")
    
    conn.close()


def main():
    print("=" * 60)
    print("  Monster Hunter Wilds DB Builder")
    print("=" * 60)
    
    # Step 1: Build wiki data
    monsters = build_wiki_md()
    
    # Step 2: Build database
    build_database(monsters)
    
    # Step 3: Verify
    verify_database()
    
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
