#!/usr/bin/env python3
"""Build structured SQLite database for Cyberpunk 2077 from wiki infoboxes.

Parses {{Infobox ...}} templates from wiki_data.md, extracts structured
fields, and creates dedicated SQLite tables for characters, locations,
weapons, vehicles, cyberware, items, quests, quickhacks, and enemies.
"""

import re
import sqlite3
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI_DATA_PATH = os.path.join(PROJECT_ROOT, "games", "cyberpunk2077", "data", "wiki_data.md")
DB_PATH = os.path.join(PROJECT_ROOT, "games", "cyberpunk2077", "cyberpunk2077_data.db")


def parse_infoboxes(content):
    """Extract all {{Infobox ...}} templates with their fields.

    Parses wiki infobox format:
    {{Infobox Type
    |field1 = value1
    |field2 = value2 that may span
    multiple lines
    |field3 = ...
    }}
    """
    # Match infobox template: {{Infobox Type\n...\n}}
    pattern = re.compile(
        r'\{\{(Infobox \w+?)\n'   # Start: {{Infobox Type\n
        r'(.*?)'                   # Body content (across lines)
        r'\n\}\}',                 # Close: \n}}
        re.DOTALL
    )

    results = []
    for match in pattern.finditer(content):
        type_name = match.group(1).strip()
        body = match.group(2)

        # Parse fields line by line
        fields = {}
        current_field = None
        current_value = []

        for line in body.split('\n'):
            trimmed = line.strip()
            # Line starting with | is a new field (allow leading whitespace)
            if trimmed.startswith('|'):
                # Save previous field
                if current_field is not None:
                    val = '\n'.join(current_value).strip()
                    if val:
                        fields[current_field] = val

                # Parse new field: |field_name = value
                line_content = trimmed[1:]  # remove leading |
                eq_pos = line_content.find('=')
                if eq_pos >= 0:
                    current_field = line_content[:eq_pos].strip()
                    current_value = [line_content[eq_pos + 1:].strip()]
                else:
                    current_field = None
                    current_value = []
            elif current_field is not None and trimmed:
                # Continuation of previous field value
                current_value.append(trimmed)

        # Save last field
        if current_field is not None:
            val = '\n'.join(current_value).strip()
            if val:
                fields[current_field] = val

        if fields:
            results.append((type_name, fields))

    return results


def sanitize_value(val):
    """Clean up a wiki field value."""
    if not val:
        return None
    # Remove wiki markup
    val = re.sub(r'<br\s*/?>', ', ', val, flags=re.IGNORECASE)
    val = re.sub(r"'''?", '', val)
    val = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', val)
    val = re.sub(r'\[https?://[^\s]+\s+([^\]]+)\]', r'\1', val)
    val = re.sub(r'\[https?://[^\]]+\]', '', val)
    val = re.sub(r'\{\{[^}]+\}\}', '', val)
    val = re.sub(r'<[^>]+>', '', val)
    val = val.strip()
    return val if val else None


def extract_title(fields):
    """Try to extract a clean title from fields."""
    for key in ['title', 'name']:
        if key in fields:
            val = sanitize_value(fields[key])
            if val:
                return val
    return None


def build_character_table(infoboxes, conn):
    """Build characters table from Infobox Character."""
    rows = []
    for type_name, fields in infoboxes:
        if type_name != 'Infobox Character':
            continue
        row = {
            'name': sanitize_value(fields.get('title')) or sanitize_value(fields.get('name')) or '',
            'aka': sanitize_value(fields.get('aka')),
            'status': sanitize_value(fields.get('status')),
            'gender': sanitize_value(fields.get('gender')),
            'age': sanitize_value(fields.get('age')),
            'affiliation': sanitize_value(fields.get('affiliation')),
            'location': sanitize_value(fields.get('location')),
            'role': sanitize_value(fields.get('role')),
            'appears_games': sanitize_value(fields.get('appears_games')),
        }
        row['name'] = row['name'] or 'Unknown'
        rows.append(row)

    conn.execute("DROP TABLE IF EXISTS characters")
    conn.execute("""
        CREATE TABLE characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            aka TEXT,
            status TEXT,
            gender TEXT,
            age TEXT,
            affiliation TEXT,
            location TEXT,
            role TEXT,
            appears_games TEXT
        )
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO characters (name, aka, status, gender, age, affiliation, location, role, appears_games)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (r['name'], r['aka'], r['status'], r['gender'], r['age'],
              r['affiliation'], r['location'], r['role'], r['appears_games']))
    conn.commit()
    return len(rows)


def build_location_table(infoboxes, conn):
    """Build locations table from Infobox Location."""
    rows = []
    for type_name, fields in infoboxes:
        if type_name != 'Infobox Location':
            continue
        row = {
            'name': sanitize_value(fields.get('title')) or sanitize_value(fields.get('name')) or '',
            'type': sanitize_value(fields.get('type')),
            'city': sanitize_value(fields.get('city')),
            'district': sanitize_value(fields.get('district')),
            'sub_district': sanitize_value(fields.get('sub_district')),
            'affiliation': sanitize_value(fields.get('affiliation')),
            'owner': sanitize_value(fields.get('owner')),
            'appears_games': sanitize_value(fields.get('appears_games')),
            'appears_books': sanitize_value(fields.get('appears_books')),
        }
        row['name'] = row['name'] or 'Unknown'
        rows.append(row)

    conn.execute("DROP TABLE IF EXISTS locations")
    conn.execute("""
        CREATE TABLE locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT,
            city TEXT,
            district TEXT,
            sub_district TEXT,
            affiliation TEXT,
            owner TEXT,
            appears_games TEXT,
            appears_books TEXT
        )
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO locations (name, type, city, district, sub_district, affiliation, owner, appears_games, appears_books)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (r['name'], r['type'], r['city'], r['district'], r['sub_district'],
              r['affiliation'], r['owner'], r['appears_games'], r['appears_books']))
    conn.commit()
    return len(rows)


def build_weapon_table(infoboxes, conn):
    """Build weapons table from Infobox Weapon2077."""
    rows = []
    for type_name, fields in infoboxes:
        if type_name != 'Infobox Weapon2077':
            continue
        row = {
            'name': sanitize_value(fields.get('title')) or sanitize_value(fields.get('name')) or '',
            'type': sanitize_value(fields.get('type')),
            'quality': sanitize_value(fields.get('quality')),
            'damage_per_hit': sanitize_value(fields.get('damage_per_hit')),
            'attack_speed': sanitize_value(fields.get('attack_speed')),
            'ammo_capacity': sanitize_value(fields.get('ammo_capacity')),
            'armor_penetration': sanitize_value(fields.get('armor_penetration')),
            'effective_range': sanitize_value(fields.get('effective_range')),
            'headshot_damage_multiplier': sanitize_value(fields.get('headshot_damage_multiplier')),
            'cost': sanitize_value(fields.get('cost')),
            'iconic': sanitize_value(fields.get('iconic')),
            'intrinsic': sanitize_value(fields.get('intrinsic')),
            'effects': sanitize_value(fields.get('effects')),
            'baseid': sanitize_value(fields.get('baseid')),
            'manufacturer': sanitize_value(fields.get('manufacturer')),
        }
        row['name'] = row['name'] or 'Unknown'
        rows.append(row)

    conn.execute("DROP TABLE IF EXISTS weapons")
    conn.execute("""
        CREATE TABLE weapons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT,
            quality TEXT,
            damage_per_hit TEXT,
            attack_speed TEXT,
            ammo_capacity TEXT,
            armor_penetration TEXT,
            effective_range TEXT,
            headshot_damage_multiplier TEXT,
            cost TEXT,
            iconic TEXT,
            intrinsic TEXT,
            effects TEXT,
            baseid TEXT,
            manufacturer TEXT
        )
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO weapons (name, type, quality, damage_per_hit, attack_speed, ammo_capacity,
                armor_penetration, effective_range, headshot_damage_multiplier, cost, iconic,
                intrinsic, effects, baseid, manufacturer)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (r['name'], r['type'], r['quality'], r['damage_per_hit'], r['attack_speed'],
              r['ammo_capacity'], r['armor_penetration'], r['effective_range'],
              r['headshot_damage_multiplier'], r['cost'], r['iconic'],
              r['intrinsic'], r['effects'], r['baseid'], r['manufacturer']))
    conn.commit()
    return len(rows)


def build_vehicle_table(infoboxes, conn):
    """Build vehicles table from Infobox Vehicle."""
    rows = []
    for type_name, fields in infoboxes:
        if type_name != 'Infobox Vehicle':
            continue
        row = {
            'name': sanitize_value(fields.get('title')) or sanitize_value(fields.get('name')) or '',
            'type': sanitize_value(fields.get('type')),
            'manufacturer': sanitize_value(fields.get('manufacturer')) or sanitize_value(fields.get('group')),
            'cost': sanitize_value(fields.get('cost')),
            'body': sanitize_value(fields.get('body')),
            'drivetrain': sanitize_value(fields.get('drivetrain')),
            'horse_power': sanitize_value(fields.get('horse_power')),
            'top_speed': sanitize_value(fields.get('top_speed')),
            'acceleration': sanitize_value(fields.get('acceleration')),
            'doors': sanitize_value(fields.get('doors')),
            'baseid': sanitize_value(fields.get('baseid')),
        }
        row['name'] = row['name'] or 'Unknown'
        rows.append(row)

    conn.execute("DROP TABLE IF EXISTS vehicles")
    conn.execute("""
        CREATE TABLE vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT,
            manufacturer TEXT,
            cost TEXT,
            body TEXT,
            drivetrain TEXT,
            horse_power TEXT,
            top_speed TEXT,
            acceleration TEXT,
            doors TEXT,
            baseid TEXT
        )
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO vehicles (name, type, manufacturer, cost, body, drivetrain,
                horse_power, top_speed, acceleration, doors, baseid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (r['name'], r['type'], r['manufacturer'], r['cost'], r['body'],
              r['drivetrain'], r['horse_power'], r['top_speed'], r['acceleration'],
              r['doors'], r['baseid']))
    conn.commit()
    return len(rows)


def build_cyberware_table(infoboxes, conn):
    """Build cyberware table from Infobox Cyberware."""
    rows = []
    for type_name, fields in infoboxes:
        if type_name != 'Infobox Cyberware':
            continue
        row = {
            'name': sanitize_value(fields.get('title')) or sanitize_value(fields.get('name')) or '',
            'type': sanitize_value(fields.get('type')),
            'quality': sanitize_value(fields.get('quality')),
            'buy_price': sanitize_value(fields.get('buy_price')),
            'capacity': sanitize_value(fields.get('capacity')),
            'armor': sanitize_value(fields.get('armor')),
            'effects': sanitize_value(fields.get('effects')),
            'attribute': sanitize_value(fields.get('attribute')),
            'attribute_value': sanitize_value(fields.get('attribute_value')),
            'baseid': sanitize_value(fields.get('baseid')),
            'cd_slots': sanitize_value(fields.get('cd_slots')),
        }
        row['name'] = row['name'] or 'Unknown'
        rows.append(row)

    conn.execute("DROP TABLE IF EXISTS cyberware")
    conn.execute("""
        CREATE TABLE cyberware (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT,
            quality TEXT,
            buy_price TEXT,
            capacity TEXT,
            armor TEXT,
            effects TEXT,
            attribute TEXT,
            attribute_value TEXT,
            baseid TEXT,
            cd_slots TEXT
        )
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO cyberware (name, type, quality, buy_price, capacity, armor,
                effects, attribute, attribute_value, baseid, cd_slots)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (r['name'], r['type'], r['quality'], r['buy_price'], r['capacity'],
              r['armor'], r['effects'], r['attribute'], r['attribute_value'],
              r['baseid'], r['cd_slots']))
    conn.commit()
    return len(rows)


def build_item_table(infoboxes, conn):
    """Build items table from Infobox Item2077, Infobox Item, Infobox Grenade, Infobox Clothing."""
    rows = []
    for type_name, fields in infoboxes:
        if type_name not in ('Infobox Item2077', 'Infobox Item', 'Infobox Grenade', 'Infobox Clothing'):
            continue
        row = {
            'name': sanitize_value(fields.get('title')) or sanitize_value(fields.get('name')) or '',
            'type': sanitize_value(fields.get('type')),
            'quality': sanitize_value(fields.get('quality')),
            'buy_price': sanitize_value(fields.get('buy_price')) or sanitize_value(fields.get('cost')),
            'sell_price': sanitize_value(fields.get('sell_price')),
            'effects': sanitize_value(fields.get('effects')),
            'duration': sanitize_value(fields.get('duration')),
            'manufacturer': sanitize_value(fields.get('manufacturer')),
            'source': sanitize_value(fields.get('source')),
            'baseid': sanitize_value(fields.get('baseid')),
            'damage': sanitize_value(fields.get('damage')),
            'weight': sanitize_value(fields.get('weight')),
            'armor': sanitize_value(fields.get('armor')),
            'style': sanitize_value(fields.get('style')),
        }
        row['name'] = row['name'] or 'Unknown'
        rows.append(row)

    conn.execute("DROP TABLE IF EXISTS items")
    conn.execute("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT,
            quality TEXT,
            buy_price TEXT,
            sell_price TEXT,
            effects TEXT,
            duration TEXT,
            manufacturer TEXT,
            source TEXT,
            baseid TEXT,
            damage TEXT,
            weight TEXT,
            armor TEXT,
            style TEXT
        )
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO items (name, type, quality, buy_price, sell_price, effects,
                duration, manufacturer, source, baseid, damage, weight, armor, style)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (r['name'], r['type'], r['quality'], r['buy_price'], r['sell_price'],
              r['effects'], r['duration'], r['manufacturer'], r['source'], r['baseid'],
              r['damage'], r['weight'], r['armor'], r['style']))
    conn.commit()
    return len(rows)


def build_quest_table(infoboxes, conn):
    """Build quests table from Infobox Quest."""
    rows = []
    for type_name, fields in infoboxes:
        if type_name != 'Infobox Quest':
            continue
        row = {
            'name': sanitize_value(fields.get('title')) or sanitize_value(fields.get('name')) or '',
            'type': sanitize_value(fields.get('type')),
            'quest_giver': sanitize_value(fields.get('quest_giver')),
            'district': sanitize_value(fields.get('district')),
            'sub_district': sanitize_value(fields.get('sub_district')),
            'location': sanitize_value(fields.get('location')),
            'target': sanitize_value(fields.get('target')),
            'objective': sanitize_value(fields.get('objective')),
            'reward_eb': sanitize_value(fields.get('reward_eb')),
            'reward_xp': sanitize_value(fields.get('reward_xp')),
            'reward_item': sanitize_value(fields.get('reward_item')),
            'previous_quest': sanitize_value(fields.get('previous_quest')),
            'next_quest': sanitize_value(fields.get('next_quest')),
            'baseid': sanitize_value(fields.get('baseid')),
        }
        row['name'] = row['name'] or 'Unknown'
        rows.append(row)

    conn.execute("DROP TABLE IF EXISTS quests")
    conn.execute("""
        CREATE TABLE quests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT,
            quest_giver TEXT,
            district TEXT,
            sub_district TEXT,
            location TEXT,
            target TEXT,
            objective TEXT,
            reward_eb TEXT,
            reward_xp TEXT,
            reward_item TEXT,
            previous_quest TEXT,
            next_quest TEXT,
            baseid TEXT
        )
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO quests (name, type, quest_giver, district, sub_district, location,
                target, objective, reward_eb, reward_xp, reward_item,
                previous_quest, next_quest, baseid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (r['name'], r['type'], r['quest_giver'], r['district'], r['sub_district'],
              r['location'], r['target'], r['objective'], r['reward_eb'], r['reward_xp'],
              r['reward_item'], r['previous_quest'], r['next_quest'], r['baseid']))
    conn.commit()
    return len(rows)


def build_quickhack_table(infoboxes, conn):
    """Build quickhacks table from Infobox Quickhack."""
    rows = []
    for type_name, fields in infoboxes:
        if type_name != 'Infobox Quickhack':
            continue
        row = {
            'name': sanitize_value(fields.get('title')) or sanitize_value(fields.get('name')) or '',
            'type': sanitize_value(fields.get('type')),
            'quality': sanitize_value(fields.get('quality')),
            'ram_cost': sanitize_value(fields.get('ram_cost')),
            'upload_time': sanitize_value(fields.get('upload_time')),
            'duration': sanitize_value(fields.get('duration')),
            'effects': sanitize_value(fields.get('effects')),
            'source': sanitize_value(fields.get('source')),
            'buy_price': sanitize_value(fields.get('buy_price')),
            'baseid': sanitize_value(fields.get('baseid')),
        }
        row['name'] = row['name'] or 'Unknown'
        rows.append(row)

    conn.execute("DROP TABLE IF EXISTS quickhacks")
    conn.execute("""
        CREATE TABLE quickhacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT,
            quality TEXT,
            ram_cost TEXT,
            upload_time TEXT,
            duration TEXT,
            effects TEXT,
            source TEXT,
            buy_price TEXT,
            baseid TEXT
        )
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO quickhacks (name, type, quality, ram_cost, upload_time, duration,
                effects, source, buy_price, baseid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (r['name'], r['type'], r['quality'], r['ram_cost'], r['upload_time'],
              r['duration'], r['effects'], r['source'], r['buy_price'], r['baseid']))
    conn.commit()
    return len(rows)


def build_enemy_table(infoboxes, conn):
    """Build enemies table from Infobox Enemy2077."""
    rows = []
    for type_name, fields in infoboxes:
        if type_name not in ('Infobox Enemy2077',):
            continue
        row = {
            'name': sanitize_value(fields.get('title')) or sanitize_value(fields.get('name')) or '',
            'type': sanitize_value(fields.get('type')),
            'affiliation': sanitize_value(fields.get('affiliation')),
            'abilities': sanitize_value(fields.get('abilities')),
            'weakness': sanitize_value(fields.get('weakness')),
            'bounty': sanitize_value(fields.get('bounty')),
            'reward': sanitize_value(fields.get('reward')),
        }
        row['name'] = row['name'] or 'Unknown'
        rows.append(row)

    conn.execute("DROP TABLE IF EXISTS enemies")
    conn.execute("""
        CREATE TABLE enemies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT,
            affiliation TEXT,
            abilities TEXT,
            weakness TEXT,
            bounty TEXT,
            reward TEXT
        )
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO enemies (name, type, affiliation, abilities, weakness, bounty, reward)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (r['name'], r['type'], r['affiliation'], r['abilities'],
              r['weakness'], r['bounty'], r['reward']))
    conn.commit()
    return len(rows)


def update_game_meta(conn, stats):
    conn.execute("DELETE FROM game_meta WHERE key = 'structured_tables'")
    conn.execute("INSERT OR REPLACE INTO game_meta (key, value) VALUES (?, ?)",
                 ('structured_tables', str(stats)))
    conn.commit()


def main():
    if not os.path.exists(WIKI_DATA_PATH):
        print(f"Error: wiki data not found at {WIKI_DATA_PATH}")
        sys.exit(1)

    print("Reading wiki data...")
    with open(WIKI_DATA_PATH, 'r') as f:
        content = f.read()

    print("Parsing infoboxes...")
    infoboxes = parse_infoboxes(content)
    print(f"  Found {len(infoboxes)} infoboxes")

    # Group by type
    type_counts = {}
    for t, _ in infoboxes:
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"    {t}: {c}")

    print("\nBuilding database...")
    conn = sqlite3.connect(DB_PATH)

    stats = {}
    stats['characters'] = build_character_table(infoboxes, conn)
    print(f"  characters: {stats['characters']} rows")

    stats['locations'] = build_location_table(infoboxes, conn)
    print(f"  locations: {stats['locations']} rows")

    stats['weapons'] = build_weapon_table(infoboxes, conn)
    print(f"  weapons: {stats['weapons']} rows")

    stats['vehicles'] = build_vehicle_table(infoboxes, conn)
    print(f"  vehicles: {stats['vehicles']} rows")

    stats['cyberware'] = build_cyberware_table(infoboxes, conn)
    print(f"  cyberware: {stats['cyberware']} rows")

    stats['items'] = build_item_table(infoboxes, conn)
    print(f"  items: {stats['items']} rows")

    stats['quests'] = build_quest_table(infoboxes, conn)
    print(f"  quests: {stats['quests']} rows")

    stats['quickhacks'] = build_quickhack_table(infoboxes, conn)
    print(f"  quickhacks: {stats['quickhacks']} rows")

    stats['enemies'] = build_enemy_table(infoboxes, conn)
    print(f"  enemies: {stats['enemies']} rows")

    update_game_meta(conn, stats)
    conn.close()

    total = sum(stats.values())
    print(f"\nDone! {total} total structured records added to {DB_PATH}")


if __name__ == '__main__':
    main()
