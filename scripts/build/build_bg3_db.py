#!/usr/bin/env python3
"""
Baldur's Gate 3 Structured Database Builder.

Parses the already-fetched wiki_data.md (rendered HTML text from bg3.wiki)
and extracts structured fields into SQLite tables.

Usage:
  python scripts/build/build_bg3_db.py
"""

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GAMES_DIR = PROJECT_ROOT / "games"
BG3_DIR = GAMES_DIR / "baldurs_gate3"
DATA_FILE = BG3_DIR / "data" / "wiki_data.md"
DB_PATH = BG3_DIR / "data" / "baldurs_gate3_data.db"


# ── Helpers ──

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def parse_pages(content: str) -> List[Dict[str, Any]]:
    """Split wiki_data.md into individual page dicts with metadata."""
    blocks = content.split("\n## ")
    pages = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        title = lines[0].strip().lstrip("#").strip()

        # Extract metadata lines
        source_url = ""
        categories = []
        body_start = 1
        for i, line in enumerate(lines[1:], 1):
            m = re.match(r'- \*\*来源\*\*: \[.*?\]\((.*?)\)', line)
            if m:
                source_url = m.group(1)
                body_start = i + 1
                continue
            m = re.match(r'- \*\*分类\*\*: (.+)', line)
            if m:
                cats_raw = m.group(1)
                categories = [c.strip() for c in re.split(r'[、,]', cats_raw) if c.strip()]
                body_start = i + 1
                continue

        body = "\n".join(lines[body_start:]).strip()
        pages.append({
            "title": title,
            "source_url": source_url,
            "categories": categories,
            "body": body,
        })
    return pages


def extract_properties_block(body: str) -> str:
    """Extract text between 'Properties' and the next section header or end."""
    m = re.search(r'Properties\s(.*?)(?=\n[A-Z][a-z]+\s|^Where to find|^Technical details|^How to learn|^Notes|^External links|\Z)', body, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def extract_details_block(body: str) -> str:
    """Extract text between 'Details' and next section."""
    m = re.search(r'Details\s(.*?)(?=\n[A-Z][a-z]+\s|^At higher levels|^Technical details|^Where to find|^Notes|\Z)', body, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def extract_rarity_price_weight(properties: str) -> Dict[str, Any]:
    """Extract Rarity, Price, Weight from a properties block."""
    r = {}
    m = re.search(r'Rarity:\s*([\w\s]+?)(?:\s+\w|$)', properties)
    if m:
        r["rarity"] = m.group(1).strip()
    m = re.search(r'Weight:\s*([\d.]+)\s*kg', properties)
    if m:
        r["weight_kg"] = float(m.group(1))
    m = re.search(r'Price:\s*(\d+)\s*gp', properties)
    if m:
        r["price_gp"] = int(m.group(1))
    m = re.search(r'UID\s+(\S+)', properties)
    if m:
        r["uid"] = m.group(1)
    m = re.search(r'UUID\s+(\S+)', properties)
    if m:
        r["uuid"] = m.group(1)
    return r


def extract_where_to_find(body: str) -> str:
    """Extract 'Where to find' section."""
    m = re.search(r'Where to find\s+(.*?)(?=\n[A-Z][a-z]+\s|^Notes|^Bugs|^External links|\Z)', body, re.DOTALL)
    if m:
        return m.group(1).strip()[:500]
    return ""


def extract_description(body: str) -> str:
    """Extract the first paragraph (description) of a page."""
    # Remove section header lines
    text = re.sub(r'^.*?\[edit section.*?\]\s*', '', body, flags=re.MULTILINE)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    desc = ""
    for line in lines:
        # Skip section headers and property blocks
        if re.match(r'^(Properties|Details|Technical details|How to learn|Notes|Where to find|External links|Contents|Overview|Combat|Description\s)', line):
            continue
        if re.match(r'^[A-Z][a-z]+\s\[edit section', line):
            continue
        if not desc:
            desc = line
        else:
            break
        if len(desc) > 300:
            break
    return desc[:500]


def parse_ability_scores(body: str) -> Tuple[Optional[int], ...]:
    """Parse ability scores from character body."""
    m = re.search(
        r'Ability scores\s*\n'
        r'STR\s+DEX\s+CON\s+INT\s+WIS\s+CHA\s*\n'
        r'(\d+)\s*\([^)]*\)\s+(\d+)\s*\([^)]*\)\s+(\d+)\s*\([^)]*\)\s+(\d+)\s*\([^)]*\)\s+(\d+)\s*\([^)]*\)\s+(\d+)\s*\([^)]*\)',
        body
    )
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), int(m.group(6)))
    return (None, None, None, None, None, None)


def parse_basic_stats(body: str) -> Dict[str, Any]:
    """Extract basic stat block from character pages.

    Handles both newline-separated format:
        Size Medium
        Type Humanoid
    And inline space-separated format:
        Size Medium Type Humanoid Race Half-elf ...
    """
    r = {}

    # First try inline format: key-value pairs separated by spaces
    # Known field names that appear in stat blocks
    stats_keys = [
        "Size", "Type", "Race", "Subrace", "Class", "Subclass",
        "Background", "Deity", "Level", "HP", "AC", "Initiative",
        "Movement", "Weight", "Proficiency", "Darkvision", "Hometown"
    ]
    stats_pattern = r'|'.join(stats_keys)

    # Try to match inline key-value pairs
    inline_body = body[:2000]  # Stats are usually early in the body

    # Simple word-by-word approach: find all key-value pairs
    for key in stats_keys:
        # Try newline-separated first
        if key in ("Race", "Subclass", "Background", "Deity"):
            m = re.search(r'(?<!\w)' + re.escape(key) + r'\s+(.+?)(?=\n\s*(?:' + stats_pattern + r')\s|\n\s*\n|\Z)', inline_body, re.DOTALL)
        elif key in ("Movement", "Weight"):
            m = re.search(r'(?<!\w)' + re.escape(key) + r'\s+([\d.]+)\s*(?:m|kg|lb|ft)?', inline_body)
        elif key == "Level":
            m = re.search(r'(?<!\w)' + re.escape(key) + r'\s+(\d+)', inline_body)
        elif key == "HP":
            m = re.search(r'(?<!\w)HP\s+(\d+)', inline_body)
        elif key == "AC":
            m = re.search(r'(?<!\w)AC\s+(\d+)', inline_body)
        elif key == "Initiative":
            m = re.search(r'(?<!\w)Initiative\s+([+-]?\d+)', inline_body)
        elif key == "Size":
            m = re.search(r'(?<!\w)Size\s+(\w+)', inline_body)
        elif key == "Type":
            m = re.search(r'(?<!\w)Type\s+(\w+)', inline_body)
        else:
            m = re.search(r'(?<!\w)' + re.escape(key) + r'\s+(\S+)', inline_body)

        if m:
            val = m.group(1).strip()
            if key == "HP":
                r["hp"] = int(val)
            elif key == "AC":
                r["ac"] = int(val)
            elif key == "Level":
                r["level"] = int(val)
            elif key == "Initiative":
                r["initiative"] = int(val)
            elif key == "Movement":
                r["movement_m"] = float(val)
            elif key == "Weight":
                r["weight_kg"] = float(val)
            elif key == "Size":
                r["size"] = val
            elif key == "Type":
                r["type"] = val
            elif key == "Race":
                r["race"] = val
            elif key == "Class":
                r["class_name"] = val
            elif key == "Subclass":
                r["subclass"] = val
            elif key == "Background":
                r["background"] = val
            elif key == "Deity":
                r["deity"] = val

    return r


# ── Category-specific Parsers ──

def parse_character(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a character/NPC/companion page."""
    body = page["body"]
    stats = parse_basic_stats(body)
    str_, dex, con, int_, wis, cha = parse_ability_scores(body)
    if str_ is not None:
        stats["strength"] = str_
        stats["dexterity"] = dex
        stats["constitution"] = con
        stats["intelligence"] = int_
        stats["wisdom"] = wis
        stats["charisma"] = cha

    # Extract class level info
    m = re.search(r'(?:Class level|Level)\s*(\d+):\s*(.+?)(?=\n|$)', body)
    if m:
        stats["class_level"] = int(m.group(1))

    # Extract deity
    m = re.search(r'Deity\s+(.+?)(?=\n\S)', body)
    if m:
        stats["deity"] = m.group(1).strip()

    # Extract related quests
    quests = re.findall(r'Related\s*quests?\s*(.*?)(?=\n[A-Z][a-z]+|\Z)', body, re.DOTALL)
    if quests:
        stats["related_quests"] = re.sub(r'\[edit section.*?\]', '', quests[0]).strip()[:300]

    # Extract location
    m = re.search(r'Hometown\s+(.+?)(?=\n\S)', body)
    if m:
        stats["location"] = m.group(1).strip()

    # Extract UID
    m = re.search(r'UID\s+(\S+)', body)
    if m:
        stats["uid"] = m.group(1)

    # Extract UUID
    m = re.search(r'UUID\s+(\S+)', body)
    if m:
        stats["uuid"] = m.group(1)

    # Extract narrative description (after the first few paragraphs)
    desc = extract_description(body)
    if desc:
        stats["description"] = desc

    return stats


def parse_spell(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a spell page."""
    body = page["body"]
    props = extract_properties_block(body)
    details = extract_details_block(body)
    r: Dict[str, Any] = {}

    # Level and school from first line
    first_line = body.split("\n")[0] if body else ""
    m = re.search(r'is a level\s+(\d+)\s+(\w+)', first_line)
    if m:
        r["level"] = int(m.group(1))
        r["school"] = m.group(2)
    else:
        # Maybe a cantrip
        m = re.search(r'is a (\w+)\s+cantrip', first_line)
        if m:
            r["level"] = 0
            r["school"] = m.group(1)

    # Action type
    if "Bonus Action" in body or "Bonus_actions" in page["categories"]:
        r["action_type"] = "bonus action"
    elif "Reaction" in body or "Reactions" in page["categories"]:
        r["action_type"] = "reaction"
    else:
        r["action_type"] = "action"

    # Cost
    m = re.search(r'Cost\s+(.+?)(?:\n|$)', props)
    if m:
        r["cost"] = m.group(1).strip()

    # Damage/Healing
    m = re.search(r'Damage:\s*([^∞\n]+?)(?:\s+[⁠\s]|$)', props)
    if m:
        r["damage"] = m.group(1).strip()
        # Try to extract dice
        dm = re.search(r'(\d+d\d+)', r["damage"])
        if dm:
            r["damage_dice"] = dm.group(1)
    m = re.search(r'Healing:\s*([^∞\n]+?)(?:\s+[⁠\s]|$)', props)
    if m:
        r["healing"] = m.group(1).strip()

    # Range
    m = re.search(r'Range:\s*([\d.]+\s*m)\s*\([^)]*\)', details or body)
    if m:
        r["range_m"] = m.group(1)
    elif props:
        m = re.search(r'Range:\s*([\d.]+\s*m)\s*\([^)]*\)', props)
        if m:
            r["range_m"] = m.group(1)

    # AoE
    m = re.search(r'AoE:\s*([^∞\n]+?)(?:\s+[⁠\s]|$)', details or body)
    if m:
        r["aoe"] = m.group(1).strip()

    # Concentration
    r["concentration"] = "Concentration" in (details or props or body)

    # Save type
    m = re.search(r'(\w+)\s+Saving\s+Throw', body)
    if m:
        r["save_type"] = m.group(1)

    # At higher levels
    m = re.search(r'At higher levels\s+(.*?)(?=\n[A-Z][a-z]+|Technical details|\Z)', body, re.DOTALL)
    if m:
        r["upcast"] = m.group(1).strip()[:300]

    # How to learn
    m = re.search(r'How to learn\s+(.*?)(?=\n[A-Z][a-z]+|Notes|\Z)', body, re.DOTALL)
    if m:
        r["how_to_learn"] = m.group(1).strip()[:500]

    # UID
    m = re.search(r'UID\s+(\S+)', body)
    if m:
        r["uid"] = m.group(1)

    # Spell flags
    m = re.search(r'Spell flags\s+(.*?)(?:\n|$)', body)
    if m:
        r["spell_flags"] = m.group(1).strip()

    # Notes
    m = re.search(r'Notes\s+(.*?)(?=\n[A-Z][a-z]+|External links|\Z)', body, re.DOTALL)
    if m:
        r["notes"] = m.group(1).strip()[:300]

    # Description
    desc = extract_description(body)
    if desc:
        r["description"] = desc

    return r


def parse_weapon(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a weapon page."""
    body = page["body"]
    props = extract_properties_block(body)
    details = extract_details_block(body)
    r = extract_rarity_price_weight(props + "\n" + details)

    # Weapon type from categories
    weapon_types = [c for c in page["categories"] if c not in ("Weapons",)]
    r["weapon_type"] = weapon_types[0] if weapon_types else ""

    # Damage
    # Handle formats like:
    # "Damage 2d6 (2~12) + Strength modifier Slashing"
    # "One-handed damage 1d8 + 1 (2~9) + Strength or Dexterity modifier Slashing"
    # "Two-handed damage 1d10 + 1 (2~11) + Strength modifier Slashing"
    m = re.search(r'(?:One-handed\s+)?(?:Two-handed\s+)?Damage\s+(\d+d\d+(?:\s*\+\s*\d+)?)\s*\([^)]*\)\s*\+\s*[^⁠†\n]+?(?:modifier\s+)?(\w+)', body)
    if m:
        r["damage_dice"] = m.group(1)
        r["damage_type"] = m.group(2)

    # Extra damage
    m = re.search(r'Extra damage\s+(\S+\s*\([^)]*\))\s+(\w+)', body)
    if m:
        r["extra_damage_dice"] = m.group(1)
        r["extra_damage_type"] = m.group(2)

    # Enchantment
    m = re.search(r'Enchantment:\s*(\S+)', details or props)
    if m:
        r["enchantment"] = m.group(1)

    # Properties (Two-Handed, Versatile, etc.)
    if details:
        # Remove known prefixes
        props_text = details
        for prefix in weapon_types:
            props_text = props_text.replace(prefix, "", 1)
        props_text = re.sub(r'Rarity:\s*[\w\s]+\s*', '', props_text)
        props_text = re.sub(r'Enchantment:\s*\S+\s*', '', props_text)
        props_text = re.sub(r'Weight:\s*[\d.]+\s*kg\s*\([^)]*\)', '', props_text)
        props_text = re.sub(r'Price:\s*\d+\s*gp', '', props_text)
        props_text = re.sub(r'UID\s+\S+', '', props_text)
        props_text = re.sub(r'UUID\s+\S+', '', props_text)
        props_text = re.sub(r'Stats\s+\S+', '', props_text)
        r["properties"] = props_text.strip()[:200]

    # Weapon actions
    m = re.search(r'Weapon actions\s+(.*?)(?=Where to find|Notes|Bugs|\Z)', body, re.DOTALL)
    if m:
        r["weapon_actions"] = m.group(1).strip()[:300]

    # Where to find
    wtf = extract_where_to_find(body)
    if wtf:
        r["where_to_find"] = wtf

    # Description
    desc = extract_description(body)
    if desc:
        r["description"] = desc

    return r


def parse_armor(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse armor/clothing page (helmets, gloves, boots, shields, cloaks)."""
    body = page["body"]
    props = extract_properties_block(body)
    details = extract_details_block(body)
    r = extract_rarity_price_weight(props + "\n" + details)

    # Armor type from categories
    excluded = ("Body", "Underwear", "Camp_Clothing", "Camp_Shoes", "Clothing")
    armor_types = [c for c in page["categories"] if c not in excluded]
    r["armor_type"] = armor_types[0] if armor_types else page["categories"][0]

    # Special effects
    m = re.search(r'Special\s+(.*?)(?=Where to find|Notes|Bugs|\Z)', body, re.DOTALL)
    if m:
        r["special_effect"] = m.group(1).strip()[:300]

    # AC bonus (for shields)
    m = re.search(r'AC\s+([+-]?\d+)', body)
    if m:
        r["ac_bonus"] = int(m.group(1))

    # Where to find
    wtf = extract_where_to_find(body)
    if wtf:
        r["where_to_find"] = wtf

    # Description
    desc = extract_description(body)
    if desc:
        r["description"] = desc

    # Also save full properties text
    if props:
        r["properties"] = props[:200]

    return r


def parse_item(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse item page (rings, amulets, potions, scrolls, elixirs, grenades, arrows)."""
    body = page["body"]
    props = extract_properties_block(body)
    r = extract_rarity_price_weight(props)

    item_type = page["categories"][0] if page["categories"] else "Item"
    r["item_type"] = item_type

    # Effect
    m = re.search(r'Effect\s+(.*?)(?=Where to find|Notes|Bugs|\Z)', body, re.DOTALL)
    if m:
        r["effect"] = m.group(1).strip()[:400]
    elif "Special" in body:
        m = re.search(r'Special\s+(.*?)(?=Where to find|Notes|Bugs|\Z)', body, re.DOTALL)
        if m:
            r["effect"] = m.group(1).strip()[:400]

    # Description
    desc = extract_description(body)
    if desc:
        r["description"] = desc

    # Where to find
    wtf = extract_where_to_find(body)
    if wtf:
        r["where_to_find"] = wtf

    return r


def strip_toc(body: str) -> str:
    """Remove the Table of Contents section from location/quest pages.
    ToC is between 'Contents' and the first '[edit section]'."""
    # Find the Contents section
    toc_end = body.find("[edit section]")
    contents_start = body.find("Contents")
    if contents_start >= 0 and toc_end > contents_start:
        # Contents line + ToC entries up to first [edit section]
        toc_match = re.search(r'Contents\s+\d[\d\s.,a-zA-Z()\'\"/-]*?(?=\s*(?:Overview|Background|History|Walkthrough)\s+\[edit)', body)
        if toc_match:
            return body[:contents_start] + body[toc_end:]
    return body


def parse_location(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse location/area page."""
    body = page["body"]
    content = strip_toc(body)

    # Determine act from category
    act = None
    for cat in page["categories"]:
        m = re.search(r'Act_(\w+)_Locations', cat)
        if m:
            act_map = {"One": 1, "Two": 2, "Three": 3}
            act = act_map.get(m.group(1))

    # Connected locations from table at top (always before ToC)
    conn_locs = []
    m = re.search(r'↑\s*(.+?)\s*↓\s*(.+?)(?:\n|$)', body)
    if m:
        conn_locs.append({"above": m.group(1).strip()})
        conn_locs.append({"below": m.group(2).strip()})

    # Waypoints - find the section after ToC
    waypoint = ""
    m = re.search(r'Waypoints?\s+\[edit section[^\]]*\]\s*(.*?)(?=\n\s*(?:\d+\s+)?[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s+\[edit section|$)', content, re.DOTALL)
    if m:
        waypoint = m.group(1).strip()[:200]

    # Related quests
    quests = ""
    m = re.search(r'Related\s+quests?\s+\[edit section[^\]]*\]\s*(.*?)(?=\n\s*(?:\d+\s+)?[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s+\[edit section|$)', content, re.DOTALL)
    if m:
        quests = re.sub(r'\[edit section.*?\]', '', m.group(1)).strip()[:300]

    # Characters
    characters = ""
    m = re.search(r'(?<!\w)Characters\s+\[edit section[^\]]*\]\s*(.*?)(?=\n\s*(?:\d+\s+)?[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s+\[edit section|$)', content, re.DOTALL)
    if m:
        characters = re.sub(r'\[edit section.*?\]', '', m.group(1)).strip()[:300]

    # Items / Loot
    loot = ""
    m = re.search(r'(?:Loot|Items?)\s+\[edit section[^\]]*\]\s*(.*?)(?=\n\s*(?:\d+\s+)?[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s+\[edit section|$)', content, re.DOTALL)
    if m:
        loot = re.sub(r'\[edit section.*?\]', '', m.group(1)).strip()[:300]

    desc = extract_description(body)
    r = {
        "act": act,
        "connected_locations": json.dumps(conn_locs, ensure_ascii=False) if conn_locs else "",
        "waypoint": waypoint,
        "related_quests": quests,
        "characters": characters,
        "loot": loot,
        "description": desc if desc else body[:300],
    }
    return r


def parse_quest(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a quest page."""
    body = page["body"]
    content = strip_toc(body)

    # Determine act
    act = None
    m = re.search(r'(?:Act\s+(\w+)|is a Quest in Act (\w+))', body)
    if m:
        act_str = m.group(1) or m.group(2)
        act_map = {"One": 1, "Two": 2, "Three": 3}
        act = act_map.get(act_str, act_str)

    # Objectives
    objectives = ""
    m = re.search(r'Objectives?\s+\[edit section[^\]]*\]\s*(.*?)(?=\n\s*(?:\d+\s+)?[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s+\[edit section|$)', content, re.DOTALL)
    if m:
        objectives = re.sub(r'\[edit section.*?\]', '', m.group(1)).strip()[:500]
    if not objectives:
        m = re.search(r'Objectives?\s*(.*?)(?=\nWalkthrough|\Z)', body, re.DOTALL)
        if m:
            objectives = m.group(1).strip()[:500]

    # Walkthrough
    walkthrough = ""
    m = re.search(r'Walkthrough\s+\[edit section[^\]]*\]\s*(.*?)(?=\n\s*(?:\d+\s+)?[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s+\[edit section|$)', content, re.DOTALL)
    if m:
        walkthrough = re.sub(r'\[edit section.*?\]', '', m.group(1)).strip()[:500]
    if not walkthrough:
        m = re.search(r'Walkthrough\s*(.*?)(?=\n(?:[A-Z][a-z]+|Notes|Rewards|\Z))', body, re.DOTALL)
        if m:
            walkthrough = m.group(1).strip()[:500]

    # Rewards
    rewards = ""
    m = re.search(r'(?:Quest\s*)?[Rr]ewards?\s+\[edit section[^\]]*\]\s*(.*?)(?=\n\s*(?:\d+\s+)?[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s+\[edit section|$)', content, re.DOTALL)
    if m:
        rewards = re.sub(r'\[edit section.*?\]', '', m.group(1)).strip()[:300]
    if not rewards:
        m = re.search(r'(?:Quest\s*)?[Rr]ewards?\s*(.*?)(?=\Z)', body, re.DOTALL)
        if m:
            rewards = m.group(1).strip()[:300]

    desc = extract_description(body)
    r = {
        "act": act,
        "objectives": objectives,
        "walkthrough": walkthrough,
        "related_locations": "",
        "rewards": rewards,
        "description": desc if desc else "",
    }
    return r


def parse_boss(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a boss page (overlaps with characters)."""
    body = page["body"]
    r = parse_basic_stats(body)
    str_, dex, con, int_, wis, cha = parse_ability_scores(body)
    if str_ is not None:
        r["strength"] = str_
        r["dexterity"] = dex
        r["constitution"] = con
        r["intelligence"] = int_
        r["wisdom"] = wis
        r["charisma"] = cha

    # Location/region
    m = re.search(r'(?:Location|Hometown)\s+(.+?)(?=\n\S)', body)
    if m:
        r["location"] = m.group(1).strip()

    # Also look for location in the Act/area descriptions
    if not r.get("location"):
        m = re.search(r'in (?:the )?(Act \w+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', body[:300])
        if m:
            r["location"] = m.group(1).strip()

    # Related quests
    m = re.search(r'Related\s*quests?\s*(.*?)(?=\n[A-Z][a-z]+|\Z)', body, re.DOTALL)
    if m:
        r["related_quests"] = re.sub(r'\[edit section.*?\]', '', m.group(1)).strip()[:300]

    # Loot
    m = re.search(r'Loot\s*(.*?)(?=\n[A-Z][a-z]+|\Z)', body, re.DOTALL)
    if m:
        r["loot"] = re.sub(r'\[edit section.*?\]', '', m.group(1)).strip()[:300]

    # Also try to get loot from "Drops" or "Notable loot" sections
    if not r.get("loot"):
        m = re.search(r'(?:Drops?|Notable loot)\s*(.*?)(?=\n[A-Z][a-z]+|\Z)', body, re.DOTALL)
        if m:
            r["loot"] = re.sub(r'\[edit section.*?\]', '', m.group(1)).strip()[:300]

    # UID
    m = re.search(r'UID\s+(\S+)', body)
    if m:
        r["uid"] = m.group(1)

    # Narrative description
    desc = extract_description(body)
    if desc:
        r["description"] = desc

    return r


def parse_book(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a book/lore page — extract metadata and text."""
    body = page["body"]
    props = extract_properties_block(body)
    r = extract_rarity_price_weight(props)

    # Author
    m = re.search(r'Author:\s*(.+?)(?=\s+\w+:|$)', props or "")
    if m:
        r["author"] = m.group(1).strip()

    # Book text content — the main body after Properties
    # Books often have the actual text in a block
    text = ""
    # Find the content after "Text" section or after Properties
    m = re.search(r'Text\s+(.*?)(?=External links|\Z)', body, re.DOTALL)
    if m:
        text = m.group(1).strip()
    if not text:
        # Try to get the body after properties
        lines = body.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("Properties") or line.startswith("Where to find"):
                text = "\n".join(lines[i+1:]).strip()
                break
    if text:
        r["text_content"] = text[:1000]
    return r


# ── Schema ──

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT,
    categories TEXT,
    size TEXT,
    type TEXT,
    race TEXT,
    class_name TEXT,
    subclass TEXT,
    background TEXT,
    level INTEGER,
    hp INTEGER,
    ac INTEGER,
    initiative INTEGER,
    strength INTEGER,
    dexterity INTEGER,
    constitution INTEGER,
    intelligence INTEGER,
    wisdom INTEGER,
    charisma INTEGER,
    movement_m REAL,
    weight_kg REAL,
    deity TEXT,
    location TEXT,
    related_quests TEXT,
    uid TEXT,
    uuid TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS spells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT,
    categories TEXT,
    level INTEGER,
    school TEXT,
    action_type TEXT,
    cost TEXT,
    damage TEXT,
    damage_dice TEXT,
    healing TEXT,
    range_m TEXT,
    aoe TEXT,
    concentration INTEGER DEFAULT 0,
    save_type TEXT,
    upcast TEXT,
    how_to_learn TEXT,
    spell_flags TEXT,
    uid TEXT,
    notes TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS weapons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT,
    categories TEXT,
    weapon_type TEXT,
    rarity TEXT,
    damage_dice TEXT,
    damage_type TEXT,
    extra_damage_dice TEXT,
    extra_damage_type TEXT,
    enchantment TEXT,
    weight_kg REAL,
    price_gp INTEGER,
    properties TEXT,
    weapon_actions TEXT,
    where_to_find TEXT,
    uid TEXT,
    uuid TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS armor (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT,
    categories TEXT,
    armor_type TEXT,
    rarity TEXT,
    ac_bonus INTEGER,
    weight_kg REAL,
    price_gp INTEGER,
    special_effect TEXT,
    properties TEXT,
    where_to_find TEXT,
    uid TEXT,
    uuid TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT,
    categories TEXT,
    item_type TEXT,
    rarity TEXT,
    weight_kg REAL,
    price_gp INTEGER,
    effect TEXT,
    where_to_find TEXT,
    uid TEXT,
    uuid TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT,
    categories TEXT,
    act INTEGER,
    connected_locations TEXT,
    waypoint TEXT,
    related_quests TEXT,
    characters TEXT,
    loot TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS quests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT,
    categories TEXT,
    act INTEGER,
    objectives TEXT,
    walkthrough TEXT,
    related_locations TEXT,
    rewards TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS bosses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT,
    categories TEXT,
    size TEXT,
    type TEXT,
    race TEXT,
    class_name TEXT,
    level INTEGER,
    hp INTEGER,
    ac INTEGER,
    initiative INTEGER,
    strength INTEGER,
    dexterity INTEGER,
    constitution INTEGER,
    intelligence INTEGER,
    wisdom INTEGER,
    charisma INTEGER,
    movement_m REAL,
    weight_kg REAL,
    location TEXT,
    related_quests TEXT,
    loot TEXT,
    uid TEXT,
    uuid TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT,
    categories TEXT,
    rarity TEXT,
    author TEXT,
    weight_kg REAL,
    price_gp INTEGER,
    text_content TEXT,
    uid TEXT,
    uuid TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT,
    categories TEXT,
    body TEXT,
    table_name TEXT
);
"""


def slugify(name: str) -> str:
    """Create a unique slug from a page title."""
    s = name.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_]+', '_', s)
    s = s.strip('_')[:100]
    return s


def make_unique_slug(slug: str, used_slugs: set) -> str:
    """Make a slug unique by adding a suffix if needed."""
    original = slug
    counter = 1
    while slug in used_slugs:
        slug = f"{original}_{counter}"
        counter += 1
    used_slugs.add(slug)
    return slug


# ── Main ──

def main():
    print(f"📖 读取数据文件: {DATA_FILE}")
    if not DATA_FILE.exists():
        print(f"❌ 数据文件不存在: {DATA_FILE}")
        sys.exit(1)

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"📄 文件大小: {len(content):,} 字符")
    pages = parse_pages(content)
    print(f"📑 解析出 {len(pages)} 个页面")

    # Create DB
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    conn.executescript(SCHEMA_SQL)
    print(f"🗄️ 数据库已创建: {DB_PATH}")

    # Category → table map
    CAT_TABLE_MAP = {
        "Characters": ("characters", parse_character),
        "Companions": ("characters", parse_character),
        "Spells": ("spells", parse_spell),
        "Cantrips": ("spells", parse_spell),
        "Weapons": ("weapons", parse_weapon),
        "Greatswords": ("weapons", parse_weapon),
        "Longswords": ("weapons", parse_weapon),
        "Shortswords": ("weapons", parse_weapon),
        "Daggers": ("weapons", parse_weapon),
        "Scimitars": ("weapons", parse_weapon),
        "Rapiers": ("weapons", parse_weapon),
        "Maces": ("weapons", parse_weapon),
        "Clubs": ("weapons", parse_weapon),
        "Spears": ("weapons", parse_weapon),
        "Quarterstaves": ("weapons", parse_weapon),
        "Warhammers": ("weapons", parse_weapon),
        "Battleaxes": ("weapons", parse_weapon),
        "Helmets": ("armor", parse_armor),
        "Gloves": ("armor", parse_armor),
        "Boots": ("armor", parse_armor),
        "Shields": ("armor", parse_armor),
        "Cloaks": ("armor", parse_armor),
        "Clothing": ("armor", parse_armor),
        "Camp_Clothing": ("armor", parse_armor),
        "Camp_Shoes": ("armor", parse_armor),
        "Rings": ("items", parse_item),
        "Amulets": ("items", parse_item),
        "Scrolls": ("items", parse_item),
        "Potions": ("items", parse_item),
        "Elixirs": ("items", parse_item),
        "Grenades": ("items", parse_item),
        "Arrows": ("items", parse_item),
        "Areas": ("locations", parse_location),
        "Act_One_Locations": ("locations", parse_location),
        "Act_Two_Locations": ("locations", parse_location),
        "Act_Three_Locations": ("locations", parse_location),
        "Quests": ("quests", parse_quest),
        "Bosses": ("bosses", parse_boss),
        "Books": ("books", parse_book),
    }

    # Priority order for primary category → table mapping
    # Bosses must come before Characters since many bosses are also Characters
    PRIMARY_MAP = [
        ("bosses", ["Bosses"]),
        ("characters", ["Characters", "Companions"]),
        ("spells", ["Spells", "Cantrips"]),
        ("weapons", ["Weapons", "Greatswords", "Longswords", "Shortswords",
                     "Daggers", "Scimitars", "Rapiers", "Maces", "Clubs",
                     "Spears", "Quarterstaves", "Warhammers", "Battleaxes"]),
        ("armor", ["Helmets", "Gloves", "Boots", "Shields", "Cloaks",
                   "Clothing", "Camp_Clothing", "Camp_Shoes"]),
        ("items", ["Rings", "Amulets", "Scrolls", "Potions", "Elixirs",
                   "Grenades", "Arrows"]),
        ("locations", ["Areas", "Act_One_Locations", "Act_Two_Locations", "Act_Three_Locations"]),
        ("quests", ["Quests"]),
        ("books", ["Books"]),
    ]

    def get_primary_table(categories):
        """Determine the primary table for a page based on categories."""
        for table, cat_list in PRIMARY_MAP:
            for cat in cat_list:
                if cat in categories:
                    return table
        return "pages"  # fallback

    # Parse and insert
    used_slugs: set = set()
    stats: Dict[str, int] = {"pages": 0}
    errors: Dict[str, int] = {}

    for i, page in enumerate(pages):
        if (i + 1) % 500 == 0:
            print(f"  进度: {i+1}/{len(pages)}")

        slug = make_unique_slug(slugify(page["title"]), used_slugs)
        table = get_primary_table(page["categories"])

        # Insert raw page into pages table
        try:
            conn.execute(
                """INSERT OR IGNORE INTO pages (slug, name, source_url, categories, body, table_name)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (slug, page["title"], page["source_url"],
                 ",".join(page["categories"]), page["body"][:5000], table)
            )
        except Exception as e:
            errors["pages"] = errors.get("pages", 0) + 1

        stats["pages"] = stats.get("pages", 0) + 1

        # Skip if only fallback
        if table == "pages":
            continue

        # Parse structured data
        parser_funcs = {
            "characters": parse_character,
            "spells": parse_spell,
            "weapons": parse_weapon,
            "armor": parse_armor,
            "items": parse_item,
            "locations": parse_location,
            "quests": parse_quest,
            "bosses": parse_boss,
            "books": parse_book,
        }

        parser = parser_funcs.get(table)
        if not parser:
            continue

        try:
            data = parser(page)
        except Exception as e:
            errors[table] = errors.get(table, 0) + 1
            continue

        if not data:
            continue

        # Insert into specific table
        data["slug"] = slug
        data["name"] = page["title"]
        data["source_url"] = page["source_url"]
        data["categories"] = ",".join(page["categories"])

        try:
            # Filter to only valid columns for this table
            col_info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            valid_cols = [c[1] for c in col_info]
            valid_data = {k: v for k, v in data.items() if k in valid_cols}

            if not valid_data:
                continue

            columns = list(valid_data.keys())
            placeholders = ",".join(["?"] * len(columns))
            values = [valid_data.get(c) for c in columns]
            # Some values might be lists/dicts, convert to JSON string
            values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in values]

            sql = f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
            conn.execute(sql, values)
            stats[table] = stats.get(table, 0) + 1
        except Exception as e:
            errors[table] = errors.get(table, 0) + 1

    conn.commit()

    # Print stats
    print(f"\n📊 构建完成:")
    print(f"  原始页: {stats.get('pages', 0)}")
    for table in ["characters", "spells", "weapons", "armor", "items",
                  "locations", "quests", "bosses", "books"]:
        count = stats.get(table, 0)
        if count:
            print(f"  {table}: {count}")

    if errors:
        print(f"\n⚠️ 错误统计:")
        for table, count in sorted(errors.items(), key=lambda x: -x[1]):
            print(f"  {table}: {count} 个错误")

    # Print table sizes
    print(f"\n📐 数据库大小:")
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall():
        tname = row["name"]
        count = conn.execute(f"SELECT COUNT(*) as cnt FROM {tname}").fetchone()["cnt"]
        print(f"  {tname}: {count} 行")

    conn.close()
    print(f"\n✅ 数据库已保存: {DB_PATH}")


if __name__ == "__main__":
    main()
