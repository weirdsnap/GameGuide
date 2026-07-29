#!/usr/bin/env python3
"""
Enrich Oxygen Not Included database with resource, plant, and seed data.
Fetches raw wikitext from Fandom API for solid/liquid/gas resources and plant seeds.
Parses {{Resource infobox}}, {{Plant infobox}}, and {{Infobox Food}} templates.
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
GAME_DIR = PROJECT_ROOT / "games" / "oni"
DB_PATH = GAME_DIR / "oni_data.db"

API_URL = "https://oxygennotincluded.fandom.com/api.php"
HEADERS = {"User-Agent": "GameGuideBot/1.0 (enriching ONI resources/plants)"}

# ── Categories to fetch ──

# Solid resources (metals, minerals, ores, etc.)
SOLID_RESOURCE_CATS = ["Solid"]

# Liquid resources (Water, Crude Oil, Petroleum, etc.)
LIQUID_RESOURCE_CATS = ["Liquid"]

# Gas resources (Oxygen, Hydrogen, Natural Gas, etc.)
GAS_RESOURCE_CATS = ["Gas"]

# Category:Seed lists seeds (plantable items)
PLANT_SEED_CATS = ["Seed"]

# Category:Food Plants lists actual plant entities
FOOD_PLANT_CATS = ["Food Plants"]

# Additional pages to fetch which may be missing from category queries
# (manually identified gaps)
EXTRA_RESOURCE_PAGES = [
    "Solid Carbon Dioxide",  # may not be in any category
    "Liquid Carbon Dioxide",
]

# Pages to skip (meta pages, not actual resources)
META_PAGES = {
    "Liquid", "Gas", "Solid", "Resource", "Unreleased Content",
    "Compostable", "High Thermal Conductivity", "Insulator",
    "Metal", "Slow Heating", "Thermally reactive",
    "Plant", "Plant/Plant comparison",
    "Seed", "Liquid/Gas", "Liquid/Unreleased Content",
    "Consumables",  # meta food page
}


# ════════════════════════════════════════════════════════════
#  Fandom API helpers
# ════════════════════════════════════════════════════════════

def fetch_category_pages(category: str, max_pages: int = 200) -> List[str]:
    """Fetch all page titles in a Fandom category."""
    titles = []
    cmcontinue = None
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmtype": "page",
            "cmlimit": min(50, max_pages - len(titles)),
            "format": "json",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url = f"{API_URL}?{qs}"
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"  ⚠️  Failed to fetch category '{category}': {e}")
            break

        members = data.get("query", {}).get("categorymembers", [])
        for m in members:
            if m.get("ns") == 0:  # main namespace = actual pages
                title = m["title"]
                if title not in META_PAGES:
                    titles.append(title)

        cont = data.get("continue", {})
        cmcontinue = cont.get("cmcontinue")
        if not cmcontinue or len(titles) >= max_pages:
            break
        time.sleep(0.3)

    return titles


def fetch_wikitext(page: str) -> Optional[str]:
    """Fetch raw wikitext for a single page."""
    params = {
        "action": "query",
        "titles": page,
        "prop": "revisions",
        "rvprop": "content",
        "rvlimit": 1,
        "format": "json",
    }
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{API_URL}?{qs}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  ⚠️  Failed to fetch wikitext for '{page}': {e}")
        return None

    pages = data.get("query", {}).get("pages", {})
    for pid, pdata in pages.items():
        if pid == "-1":
            return None
        revs = pdata.get("revisions", [])
        if revs:
            return revs[0].get("*", "")
    return None


def slugify(name: str) -> str:
    """Convert page name to DB slug."""
    return name.lower().replace(" ", "_").replace("(", "").replace(")", "")


# ════════════════════════════════════════════════════════════
#  Template parsers
# ════════════════════════════════════════════════════════════

def parse_resource_infobox(wikitext: str) -> Dict[str, Any]:
    """
    Parse {{Resource infobox ...}} template.

    Format:
    {{Resource infobox|POSITIONAL1|POSITIONAL2|...|
    | named_param = value
    | ...
    }}

    Returns dict of named params plus positional categories.
    """
    result = {}
    match = re.search(r'\{\{Resource infobox(.*?)\}\}', wikitext, re.DOTALL | re.IGNORECASE)
    if not match:
        return result

    body = match.group(1).strip()

    # Split into parts by |, preserving nested {{...}}
    parts = []
    depth = 0
    current = []
    for ch in body:
        if ch == '{':
            depth += 1
            current.append(ch)
        elif ch == '}':
            depth -= 1
            current.append(ch)
        elif ch == '|' and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append(''.join(current).strip())

    # Parse named params (parts with '=') and positional params
    positional = []
    for part in parts:
        if '=' in part and not part.startswith('='):
            key, _, value = part.partition('=')
            key = key.strip().lower().replace(' ', '_')
            value = value.strip()
            # Clean wiki markup
            value = re.sub(r'\{\{.*?\}\}', '', value)
            value = re.sub(r'\[\[(?:[^|]+\|)?([^\]]+)\]\]', r'\1', value)
            value = value.replace('<br/>', ', ').replace('<br>', ', ')
            value = re.sub(r'<[^>]+>', '', value).strip()
            result[key] = value
        else:
            pos = part.strip()
            if pos:
                positional.append(pos)

    result['_positional'] = positional
    return result


def parse_food_infobox(wikitext: str) -> Dict[str, Any]:
    """Parse {{Infobox Food ...}} template."""
    result = {}
    match = re.search(r'\{\{Infobox Food(.*?)\}\}', wikitext, re.DOTALL | re.IGNORECASE)
    if not match:
        return result

    body = match.group(1)
    for line in body.split('\n'):
        line = line.strip()
        if not line.startswith('|'):
            continue
        line = line.lstrip('|').strip()
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip().lower().replace(' ', '_')
        value = value.strip()
        value = re.sub(r'\{\{.*?\}\}', '', value)
        value = re.sub(r'\[\[(?:[^|]+\|)?([^\]]+)\]\]', r'\1', value)
        value = re.sub(r'\[\[([^\]]+)\]\]', r'\1', value)
        result[key] = value

    return result


def parse_plant_infobox(wikitext: str) -> Dict[str, Any]:
    """Parse {{Plant infobox ...}} template."""
    result = {}
    match = re.search(r'\{\{Plant infobox(.*?)\}\}', wikitext, re.DOTALL | re.IGNORECASE)
    if not match:
        # Also try {{Infobox Plant}}
        match = re.search(r'\{\{Infobox Plant(.*?)\}\}', wikitext, re.DOTALL | re.IGNORECASE)
    if not match:
        return result

    body = match.group(1)
    for line in body.split('\n'):
        line = line.strip()
        if not line.startswith('|'):
            continue
        line = line.lstrip('|').strip()
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip().lower().replace(' ', '_')
        value = value.strip()
        value = re.sub(r'\{\{.*?\}\}', '', value)
        value = re.sub(r'\[\[(?:[^|]+\|)?([^\]]+)\]\]', r'\1', value)
        value = re.sub(r'\[\[([^\]]+)\]\]', r'\1', value)
        value = value.replace('<br/>', ', ').replace('<br>', ', ')
        result[key] = value

    return result


# ════════════════════════════════════════════════════════════
#  Category classifiers
# ════════════════════════════════════════════════════════════

def classify_resource_category(positional: List[str]) -> str:
    """Determine resource category (Solid/Liquid/Gas) from positional params."""
    pos_str = ' '.join(positional).lower()
    if 'solid' in pos_str:
        return 'Solid'
    elif 'liquid' in pos_str:
        return 'Liquid'
    elif 'gas' in pos_str:
        return 'Gas'
    return 'Unknown'


def determine_resource_type(positional: List[str]) -> str:
    """Determine the material type from positional params."""
    pos_str = ' '.join(positional)
    type_hints = {
        'Refined Metal': ['refined metal'],
        'Raw Metal': ['raw metal'],
        'Metal Ore': ['metal ore'],
        'Organic': ['organic'],
        'Consumable Ore': ['consumable ore'],
        'Cultivable Soil': ['cultivable soil', 'generic buildable.*cultivable'],
        'Plastic': ['plastic'],
        'Glass': ['glass', 'transparent'],
        'Ceramic': ['ceramic'],
        'Filtration': ['filtration'],
        'Liquid': ['liquid', 'unreleased content.*liquid',
                    'water based', 'petroleum based'],
        'Gas': ['gas', 'breathable gas', 'unbreathable gas'],
    }
    for label, patterns in type_hints.items():
        for p in patterns:
            if re.search(p, pos_str, re.IGNORECASE):
                return label
    return 'Miscellaneous'


# ════════════════════════════════════════════════════════════
#  DB operations
# ════════════════════════════════════════════════════════════

def upgrade_schema(conn: sqlite3.Connection):
    """Ensure the resources and plants tables have all needed columns."""
    cur = conn.cursor()
    tables = {row[0] for row in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    # Add missing columns to resources table
    if "resources" in tables:
        existing = {row[1] for row in cur.execute("PRAGMA table_info(resources)").fetchall()}
        for col, dtype in {
            "phase": "TEXT DEFAULT ''",
            "molar_mass": "REAL",
            "hardness": "REAL",
            "light_absorption": "INTEGER",
            "radiation_absorption": "REAL",
            "max_mass": "REAL",
            "chem": "TEXT DEFAULT ''",
        }.items():
            if col not in existing:
                print(f"  ⬆️  Adding column 'resources.{col}'")
                cur.execute(f"ALTER TABLE resources ADD COLUMN {col} {dtype}")

    # Add missing columns to plants table
    if "plants" in tables:
        existing = {row[1] for row in cur.execute("PRAGMA table_info(plants)").fetchall()}
        for col, dtype in {
            "seed": "TEXT DEFAULT ''",
            "decor": "TEXT DEFAULT ''",
            "effects": "TEXT DEFAULT ''",
            "irrigation": "TEXT DEFAULT ''",
            "fertilizer": "TEXT DEFAULT ''",
        }.items():
            if col not in existing:
                print(f"  ⬆️  Adding column 'plants.{col}'")
                cur.execute(f"ALTER TABLE plants ADD COLUMN {col} {dtype}")

    # Add missing columns to food table
    if "food" in tables:
        existing = {row[1] for row in cur.execute("PRAGMA table_info(food)").fetchall()}
        for col, dtype in {
            "spoil_time": "REAL",
            "source": "TEXT DEFAULT ''",
        }.items():
            if col not in existing:
                print(f"  ⬆️  Adding column 'food.{col}'")
                cur.execute(f"ALTER TABLE food ADD COLUMN {col} {dtype}")

    conn.commit()


def insert_resource(conn: sqlite3.Connection, data: Dict[str, Any]) -> bool:
    """Insert or update a resource entry. Returns True if inserted."""
    slug = data["slug"]
    cur = conn.cursor()

    # Check if row exists
    existing = cur.execute(
        "SELECT slug FROM resources WHERE slug = ?", (slug,)).fetchone()

    if existing:
        # Update existing row (merge data)
        updates = []
        params = []
        for key in ["thermal_conductivity", "specific_heat_capacity",
                     "melting_point", "category", "phase", "molar_mass",
                     "hardness", "light_absorption", "radiation_absorption",
                     "max_mass", "chem"]:
            if key in data and data[key] is not None:
                updates.append(f"{key} = ?")
                params.append(data[key])
        if updates:
            params.append(slug)
            cur.execute(
                f"UPDATE resources SET {', '.join(updates)} WHERE slug = ?",
                params)
            conn.commit()
            return False
        return False

    # Insert new row
    columns = ["slug", "name", "thermal_conductivity", "specific_heat_capacity",
               "melting_point", "category", "phase", "molar_mass",
               "hardness", "light_absorption", "radiation_absorption",
               "max_mass", "chem"]
    values = [data.get(c) for c in columns]
    placeholders = ','.join(['?' for _ in columns])
    columns_str = ','.join(columns)
    try:
        cur.execute(
            f"INSERT INTO resources ({columns_str}) VALUES ({placeholders})",
            values)
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        print(f"  ⚠️  Insert error for {slug}: {e}")
        return False


def insert_plant(conn: sqlite3.Connection, data: Dict[str, Any]) -> bool:
    """Insert or update a plant entry. Returns True if inserted."""
    slug = data["slug"]
    cur = conn.cursor()

    existing = cur.execute(
        "SELECT slug FROM plants WHERE slug = ?", (slug,)).fetchone()

    if existing:
        updates = []
        params = []
        for key in ["growth_time", "harvest", "temp_range", "seed",
                     "decor", "effects", "irrigation", "fertilizer"]:
            if key in data and data[key] is not None:
                updates.append(f"{key} = ?")
                params.append(data[key])
        if updates:
            params.append(slug)
            cur.execute(
                f"UPDATE plants SET {', '.join(updates)} WHERE slug = ?",
                params)
            conn.commit()
            return False
        return False

    columns = ["slug", "name", "growth_time", "harvest", "temp_range",
               "seed", "decor", "effects", "irrigation", "fertilizer"]
    values = [data.get(c) for c in columns]
    placeholders = ','.join(['?' for _ in columns])
    columns_str = ','.join(columns)
    try:
        cur.execute(
            f"INSERT INTO plants ({columns_str}) VALUES ({placeholders})",
            values)
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        print(f"  ⚠️  Insert error for plant {slug}: {e}")
        return False


def insert_food(conn: sqlite3.Connection, data: Dict[str, Any]) -> bool:
    """Insert or update a food entry. Returns True if inserted."""
    slug = data["slug"]
    cur = conn.cursor()

    existing = cur.execute(
        "SELECT slug FROM food WHERE slug = ?", (slug,)).fetchone()

    if existing:
        updates = []
        params = []
        for key in ["calories", "quality", "spoil_time", "source"]:
            if key in data and data[key] is not None:
                updates.append(f"{key} = ?")
                params.append(data[key])
        if updates:
            params.append(slug)
            cur.execute(
                f"UPDATE food SET {', '.join(updates)} WHERE slug = ?",
                params)
            conn.commit()
            return False
        return False

    columns = ["slug", "name", "calories", "quality", "spoil_time", "source"]
    values = [data.get(c) for c in columns]
    placeholders = ','.join(['?' for _ in columns])
    columns_str = ','.join(columns)
    try:
        cur.execute(
            f"INSERT INTO food ({columns_str}) VALUES ({placeholders})",
            values)
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        print(f"  ⚠️  Insert error for food {slug}: {e}")
        return False


# ════════════════════════════════════════════════════════════
#  Resource processing
# ════════════════════════════════════════════════════════════

def extract_resource_data(page: str, wikitext: str) -> Optional[Dict[str, Any]]:
    """Extract resource data from {{Resource infobox}} template."""
    ib = parse_resource_infobox(wikitext)
    if not ib:
        return None

    name = page.strip()
    slug = slugify(name)

    # Determine phase and category from positional params
    positional = ib.get('_positional', [])
    phase = classify_resource_category(positional)
    category = determine_resource_type(positional)

    # Parse numeric fields
    def parse_float(val: Any) -> Optional[float]:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            # Remove °C, %, etc.
            val = re.sub(r'[^\d.\-]', '', val.strip())
            try:
                return float(val) if val else None
            except ValueError:
                return None
        return None

    def parse_int(val: Any) -> Optional[int]:
        f = parse_float(val)
        return int(f) if f is not None else None

    data = {
        "slug": slug,
        "name": name,
        "category": ib.get('type', category),
        "thermal_conductivity": parse_float(ib.get('thermal_conductivity')),
        "specific_heat_capacity": parse_float(ib.get('heat_capacity')),
        "melting_point": parse_float(ib.get('melting_point')),
        "phase": phase,
        "molar_mass": parse_float(ib.get('molar_mass')),
        "hardness": parse_float(ib.get('hardness')),
        "light_absorption": parse_int(ib.get('light_absorption')),
        "radiation_absorption": parse_float(ib.get('radiation_absorption')),
        "max_mass": parse_float(ib.get('max_mass')),
        "chem": ib.get('chem', ''),
    }

    return data


def process_resource_page(page: str, conn: sqlite3.Connection) -> Dict[str, Any]:
    """Fetch wikitext, parse resource infobox, insert into DB."""
    wt = fetch_wikitext(page)
    if not wt:
        return {"page": page, "status": "fetch_failed"}

    data = extract_resource_data(page, wt)
    if not data:
        return {"page": page, "status": "no_infobox"}

    inserted = insert_resource(conn, data)
    return {"page": page, "status": "inserted" if inserted else "updated"}


def process_food_page(page: str, conn: sqlite3.Connection) -> Dict[str, Any]:
    """Fetch wikitext, parse food infobox, insert into DB."""
    wt = fetch_wikitext(page)
    if not wt:
        return {"page": page, "status": "fetch_failed"}

    ib = parse_food_infobox(wt)
    if not ib:
        return {"page": page, "status": "no_infobox"}

    name = page.strip()
    slug = slugify(name)

    def parse_kcal(val: str) -> Optional[int]:
        val = re.sub(r'[^\d]', '', val)
        try:
            return int(val) if val else None
        except ValueError:
            return None

    def parse_quality(val: str) -> Optional[int]:
        val = val.strip()
        try:
            return int(val)
        except ValueError:
            return None

    data = {
        "slug": slug,
        "name": name,
        "calories": parse_kcal(ib.get('kcal', '')),
        "quality": parse_quality(ib.get('quality', '-1')),
        "spoil_time": parse_quality(ib.get('spoilTime', ib.get('spoil_time', ''))),
        "source": ib.get('source', ''),
    }

    inserted = insert_food(conn, data)
    return {"page": page, "status": "inserted" if inserted else "updated"}


def process_plant_page(page: str, conn: sqlite3.Connection) -> Dict[str, Any]:
    """Fetch wikitext, parse plant infobox, insert into DB."""
    wt = fetch_wikitext(page)
    if not wt:
        return {"page": page, "status": "fetch_failed"}

    ib = parse_plant_infobox(wt)
    if not ib:
        # If no plant infobox, try resource infobox for seeds
        rib = parse_resource_infobox(wt)
        if rib:
            # Seed items (like Blossom Seed, Arbor Acorn)
            name = page.strip()
            slug = slugify(name)
            data = {
                "slug": slug,
                "name": name,
                "growth_time": None,
                "harvest": '',
                "temp_range": '',
                "seed": name,
                "decor": '',
                "effects": '',
                "irrigation": '',
                "fertilizer": '',
            }
            inserted = insert_plant(conn, data)
            return {"page": page, "status": "inserted_seed" if inserted else "updated"}
        return {"page": page, "status": "no_infobox"}

    name = page.strip()
    slug = slugify(name)

    def parse_float(val: Any) -> Optional[float]:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            val = re.sub(r'[^\d.]', '', val.strip())
            try:
                return float(val) if val else None
            except ValueError:
                return None
        return None

    # Map from plant infobox fields to DB columns
    # Actual template field names: growth_cycles, temperature_min, temperature_max,
    # produces_item, seed_item, decor, effect, irrigation_element, fertilizer_element
    growth_time = parse_float(ib.get('growth_cycles', ib.get('growth', '')))
    seed_item = ib.get('seed_item', '')
    harvest = ib.get('produces_item', ib.get('harvest', ''))
    temp_range = ''
    if 'temperature_min' in ib or 'temperature_max' in ib:
        tmin = ib.get('temperature_min', '')
        tmax = ib.get('temperature_max', '')
        temp_range = f"{tmin}-{tmax} °C" if tmin and tmax else (tmin or tmax)
    elif ib.get('temperature_range'):
        temp_range = ib['temperature_range']
    elif ib.get('temperature'):
        temp_range = ib['temperature']

    effects = ib.get('effect', ib.get('effects', ''))
    decor = ib.get('decor', '')
    # Build irrigation from element + amount
    irrig_elem = ib.get('irrigation_element', '')
    irrig_amt = ib.get('irrigation_amount', '')
    if irrig_elem and irrig_amt:
        irrigation = f"{irrig_elem} {irrig_amt} kg/cycle"
    elif irrig_elem:
        irrigation = irrig_elem
    else:
        irrigation = ib.get('irrigation', '')
    # Build fertilizer from element + amount
    fert_elem = ib.get('fertilizer_element', '')
    fert_amt = ib.get('fertilizer_amount', '')
    if fert_elem and fert_amt:
        fertilizer_str = f"{fert_elem} {fert_amt} kg/cycle"
    elif fert_elem:
        fertilizer_str = fert_elem
    else:
        fertilizer_str = ib.get('fertilizer', '')

    data = {
        "slug": slug,
        "name": name,
        "growth_time": growth_time,
        "harvest": harvest or seed_item,
        "temp_range": temp_range,
        "seed": seed_item,
        "decor": decor,
        "effects": effects,
        "irrigation": irrigation,
        "fertilizer": fertilizer_str,
    }

    inserted = insert_plant(conn, data)
    return {"page": page, "status": "inserted" if inserted else "updated"}


# ════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════

def print_stats(conn: sqlite3.Connection):
    """Print table row counts."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cur.fetchall()
    print("\n📊 数据库统计:")
    total = 0
    for (t,) in tables:
        cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        count = cur.fetchone()[0]
        total += count
        print(f"  {t}: {count} 条记录")
    print(f"  ────────\n  总计: {total} 条记录")


def main():
    print("=" * 60)
    print("  Oxygen Not Included 数据补全脚本")
    print("  目标: 补充 resources(固体/液体/气体) + plants(种子/作物)")
    print("=" * 60)

    # ── Connect to DB ──
    if not DB_PATH.exists():
        print(f"❌ 数据库不存在: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    upgrade_schema(conn)
    print(f"\n📂 数据库: {DB_PATH}")

    # ── Collect pages from categories ──
    print("\n🔍 扫描分类...")

    all_pages = set()

    # Solid resources
    print("  📦 Solids...")
    for cat in SOLID_RESOURCE_CATS:
        pages = fetch_category_pages(cat)
        print(f"    Category:{cat} → {len(pages)} pages")
        for p in pages:
            all_pages.add(("resource", p))
        # Count
        time.sleep(0.5)

    # Liquid resources
    print("  💧 Liquids...")
    for cat in LIQUID_RESOURCE_CATS:
        pages = fetch_category_pages(cat)
        print(f"    Category:{cat} → {len(pages)} pages")
        for p in pages:
            all_pages.add(("resource", p))
        time.sleep(0.5)

    # Gas resources
    print("  💨 Gases...")
    for cat in GAS_RESOURCE_CATS:
        pages = fetch_category_pages(cat)
        print(f"    Category:{cat} → {len(pages)} pages")
        for p in pages:
            all_pages.add(("resource", p))
        time.sleep(0.5)

    # Seeds (for plants table)
    print("  🌱 Seeds...")
    for cat in PLANT_SEED_CATS:
        pages = fetch_category_pages(cat)
        print(f"    Category:{cat} → {len(pages)} pages")
        for p in pages:
            all_pages.add(("seed", p))
        time.sleep(0.5)

    # Food Plants
    print("  🌿 Food Plants...")
    for cat in FOOD_PLANT_CATS:
        pages = fetch_category_pages(cat)
        print(f"    Category:{cat} → {len(pages)} pages")
        for p in pages:
            all_pages.add(("plant", p))
        time.sleep(0.5)

    # Extra pages
    for p in EXTRA_RESOURCE_PAGES:
        all_pages.add(("resource", p))

    print(f"\n📋 共计 {len(all_pages)} 个页面待处理")

    # ── Process pages ──
    results = {"inserted": 0, "updated": 0, "failed": 0, "skipped": 0}
    processed = 0

    for page_type, page_title in sorted(all_pages, key=lambda x: x[1]):
        processed += 1
        if processed % 10 == 0:
            print(f"\n  📈 进度: {processed}/{len(all_pages)}"
                  f" (✅ {results['inserted']} 新增 / "
                  f"🔄 {results['updated']} 更新 / "
                  f"⚠️ {results['failed']} 失败)")

        if page_type == "resource":
            result = process_resource_page(page_title, conn)
        elif page_type == "seed":
            result = process_plant_page(page_title, conn)
        elif page_type == "plant":
            result = process_plant_page(page_title, conn)

        status = result["status"]
        if status == "inserted":
            results["inserted"] += 1
        elif status in ("updated", "inserted_seed"):
            results["updated"] += 1
        elif status == "no_infobox":
            results["skipped"] += 1
            if results["skipped"] <= 5:
                print(f"  ⏭️  {result['page']} (no infobox)")
        else:
            results["failed"] += 1
            print(f"  ⚠️  {result['page']}: {status}")

        time.sleep(0.3)  # rate limiting

    # ── Stats ──
    print(f"\n\n📊 处理结果:")
    print(f"  ✅ 新增: {results['inserted']}")
    print(f"  🔄 更新: {results['updated']}")
    print(f"  ⏭️  跳过: {results['skipped']}")
    print(f"  ⚠️  失败: {results['failed']}")

    print_stats(conn)
    conn.close()

    print("\n✅ ONI 数据补全完成!")
    print("💡 建议下一步: 查看数据库完整性，确认 resources 和 plants 表已充实")


if __name__ == "__main__":
    main()
