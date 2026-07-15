#!/usr/bin/env python3
"""Build structured SQLite database for VA-11 Hall-A from the Fandom API.

The raw wiki_data.md has corrupted drink data (wiki markup artifacts).
This script fetches clean rendered content from the Fandom 'parse' API
to extract drink recipes, cost, and flavor info, and fixes character
section parsing.
"""

import json
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.parse
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI_DATA_PATH = os.path.join(PROJECT_ROOT, "games", "va11halla", "data", "wiki_data.md")
DB_PATH = os.path.join(PROJECT_ROOT, "games", "va11halla", "va11halla_data.db")
API_BASE = "https://va11halla.fandom.com/api.php"
USER_AGENT = "nanobot/1.0"


def call_api(params):
    """Call the Fandom API with the given params and return JSON."""
    params['format'] = 'json'
    url = API_BASE + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())


def get_page_categories(title):
    """Get categories for a page."""
    data = call_api({
        'action': 'query',
        'titles': title,
        'prop': 'categories',
        'cllimit': 50,
    })
    pages = data.get('query', {}).get('pages', {})
    for pid, info in pages.items():
        if pid != '-1':
            cats = [c['title'].replace('Category:', '') for c in info.get('categories', [])]
            return cats
    return []


def get_rendered_content(title):
    """Get rendered (cleaned) HTML content for a page via parse API."""
    data = call_api({
        'action': 'parse',
        'page': title,
        'prop': 'text',
    })
    html = data.get('parse', {}).get('text', {}).get('*', '')
    # Clean HTML tags to get plain text
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def read_markdown_pages():
    """Split wiki_data.md into individual pages by '---' separator."""
    with open(WIKI_DATA_PATH, 'r') as f:
        content = f.read()
    raw_pages = content.split('---\n')
    pages = []
    for raw in raw_pages:
        lines = raw.strip().split('\n')
        title = ''
        for l in lines:
            if l.startswith('## '):
                title = l[3:].strip()
                break
        if title:
            pages.append({'title': title, 'lines': lines, 'content': raw})
    return pages


def classify_page_from_md(page):
    """Classify a VA-11 Hall-A page using its raw MD content."""
    title = page['title']
    content = page['content']

    if title.startswith('Category:') or title.startswith('File:'):
        return 'meta'

    # Known drink names
    known_drinks = [
        'Bad Touch', 'Beer', 'Bleeding Jane', 'Bloom Light', 'Blue Fairy',
        'Brandtini', 'Cobalt Velvet', 'Crevice Spike', 'Flaming Moai',
        'Fluffy Dream', 'Fringe Weaver', 'Frothy Water', 'Grizzly Temple',
        'Gut Punch', 'Marsblast', 'Mercuryblast', 'Moonblast', 'Piano Man',
        'Piano Woman', 'Pile Driver', 'Sparkle Star', 'Sugar Rush',
        'Sunshine Cloud', 'Suplex', 'Zen Star'
    ]
    if title in known_drinks:
        return 'drink'

    # Ingredients
    ingredients = ['Adelhyde', 'Bronson Extract', 'Flanergide', 'Karmotrine', 'Powdered Delta']
    if title in ingredients:
        return 'ingredient'

    # Bars
    bars = ['VA-11 Hall-A']
    if title in bars:
        return 'bar'

    # Locations
    locations = ['Glitch City', 'Neo-San Francisco']
    if title in locations:
        return 'location'

    # Items
    items_list = ['A Fedora', 'Mulan Tea', 'Absinthe', 'BTC']
    if title in items_list:
        return 'item'

    # Character detection: sections with Appearance/Personality/Background
    stripped_lines = [l.strip() for l in page['lines']]
    for sl in stripped_lines:
        if sl in ('Appearance', 'Personality', 'Background', 'Plot'):
            return 'character'

    return 'other'


def fetch_drink_data(title):
    """Fetch clean drink data from the Fandom API."""
    time.sleep(0.5)  # rate limit
    try:
        text = get_rendered_content(title)
    except Exception as e:
        return {'name': title, 'cost': None, 'style': None, 'recipe': None,
                'flavor': None, 'drink_type': None, 'error': str(e)}

    result = {'name': title}

    # Cost: "drink costing $250"
    cost_match = re.search(r'costing\s+\$?(\d+)', text)
    result['cost'] = cost_match.group(1) if cost_match else None

    # Recipe: "2 Bronson Extract, 2 Powdered Delta, 2 Flanergide and 4 Karmotrine"
    recipe_match = re.search(
        r'is\s+(\d+\s+\w+(?:\s+\w+)?[,\s]+.*?(?:and\s+)?\d+\s+\w+(?:\s+\w+)?)',
        text
    )
    result['recipe'] = recipe_match.group(1).strip() if recipe_match else None

    # Style/flavor: "It's a Sour, Classy and Vintage drink"
    style_match = re.search(r"It'?s?\s+a\s+(.+?)drink", text)
    if style_match:
        style_text = style_match.group(1).strip().rstrip(',').strip()
        # Split into flavor and type
        parts = [p.strip() for p in re.split(r'[,]|(?:and)\s+', style_text) if p.strip()]
        result['style'] = ' / '.join(parts)

        # Determine flavor type (first item is usually flavor)
        known_flavors = ['Sour', 'Sweet', 'Bitter', 'Spicy', 'Bubbly', 'Bland', 'Burning']
        result['flavor'] = parts[0] if parts[0] in known_flavors else None
        if len(parts) > 1:
            result['drink_type'] = ' / '.join(parts[1:])
        else:
            result['drink_type'] = None
    else:
        result['style'] = None
        result['flavor'] = None
        result['drink_type'] = None

    return result


def extract_character_sections(page):
    """Extract sections from a character page."""
    sections = {}
    current_section = None
    section_buffer = []

    for line in page['lines']:
        stripped = line.strip()
        if stripped in ('Appearance', 'Personality', 'Background', 'Plot'):
            # Save previous section
            if current_section and section_buffer:
                text = '\n'.join(section_buffer).strip()
                text = re.sub(r'<nowiki>\*</nowiki>', '*', text)
                text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
                sections[current_section] = text
            current_section = stripped.lower()
            section_buffer = []
        elif current_section and stripped and not stripped.startswith('## '):
            # Don't include sub-headings like "Order list", "Early Design", etc.
            if not stripped.startswith('##'):
                section_buffer.append(stripped)

    # Save last section
    if current_section and section_buffer:
        text = '\n'.join(section_buffer).strip()
        text = re.sub(r'<nowiki>\*</nowiki>', '*', text)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        sections[current_section] = text

    return sections


def build_drink_table(drinks, conn):
    conn.execute("DROP TABLE IF EXISTS drinks")
    conn.execute("""
        CREATE TABLE drinks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            cost TEXT,
            style TEXT,
            recipe TEXT,
            flavor TEXT,
            drink_type TEXT
        )
    """)
    for d in drinks:
        conn.execute("""
            INSERT INTO drinks (name, cost, style, recipe, flavor, drink_type)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (d['name'], d['cost'], d['style'], d['recipe'], d['flavor'], d['drink_type']))
    conn.commit()
    return len(drinks)


def build_character_table(characters, conn):
    conn.execute("DROP TABLE IF EXISTS characters")
    conn.execute("""
        CREATE TABLE characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            appearance_text TEXT,
            personality_text TEXT,
            background_text TEXT,
            plot_text TEXT
        )
    """)
    for c in characters:
        conn.execute("""
            INSERT INTO characters (name, appearance_text, personality_text, background_text, plot_text)
            VALUES (?, ?, ?, ?, ?)
        """, (c['name'], c['appearance_text'], c['personality_text'],
              c['background_text'], c['plot_text']))
    conn.commit()
    return len(characters)


def build_ingredient_table(ingredients, conn):
    conn.execute("DROP TABLE IF EXISTS ingredients")
    conn.execute("""
        CREATE TABLE ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT
        )
    """)
    for ing in ingredients:
        desc = ing.get('content', '')[:500]
        # Clean up HTML tags in description
        desc = re.sub(r'<[^>]+>', ' ', desc)
        desc = re.sub(r'\s+', ' ', desc).strip()
        conn.execute("""
            INSERT INTO ingredients (name, description)
            VALUES (?, ?)
        """, (ing['title'], desc))
    conn.commit()
    return len(ingredients)


def build_location_table(locations, conn):
    conn.execute("DROP TABLE IF EXISTS locations")
    conn.execute("""
        CREATE TABLE locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            location_type TEXT
        )
    """)
    for loc in locations:
        desc = loc.get('content', '')[:500]
        desc = re.sub(r'<[^>]+>', ' ', desc)
        desc = re.sub(r'\s+', ' ', desc).strip()
        conn.execute("""
            INSERT INTO locations (name, description, location_type)
            VALUES (?, ?, ?)
        """, (loc['title'], desc, loc.get('type', 'location')))
    conn.commit()
    return len(locations)


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
    pages = read_markdown_pages()
    print(f"  Found {len(pages)} pages")

    # Classify pages
    classified = {}
    for p in pages:
        cat = classify_page_from_md(p)
        classified.setdefault(cat, []).append(p)

    for cat in ['character', 'drink', 'ingredient', 'item', 'location', 'bar', 'other', 'meta']:
        if cat in classified:
            print(f"  {cat}: {len(classified[cat])}")

    print("\nExtracting structured data...")
    conn = sqlite3.connect(DB_PATH)

    # Fetch drink data from API (clean rendered content)
    print("\n  Fetching drink data from Fandom API (25 calls with 0.5s delay)...")
    drink_data = []
    for p in classified.get('drink', []):
        sys.stdout.write(f"    {p['title']:25s} ... ")
        sys.stdout.flush()
        d = fetch_drink_data(p['title'])
        if d.get('error'):
            sys.stdout.write(f"ERROR: {d['error'][:30]}\n")
        else:
            drink_data.append(d)
            sys.stdout.write(f"cost=${d['cost'] or '?'}, recipe={'✓' if d['recipe'] else '✗'}, flavor={d['flavor'] or '?'}\n")

    ndrinks = build_drink_table(drink_data, conn)
    print(f"  drinks: {ndrinks} rows")

    # Character data from markdown
    character_data = []
    for p in classified.get('character', []):
        sections = extract_character_sections(p)
        character_data.append({
            'name': p['title'],
            'appearance_text': sections.get('appearance'),
            'personality_text': sections.get('personality'),
            'background_text': sections.get('background'),
            'plot_text': sections.get('plot'),
        })
    nchars = build_character_table(character_data, conn)
    print(f"  characters: {nchars} rows")

    # Ingredients
    ingredients = classified.get('ingredient', [])
    ning = build_ingredient_table(ingredients, conn)
    print(f"  ingredients: {ning} rows")

    # Locations + bars
    locs = classified.get('location', [])
    for b in classified.get('bar', []):
        b['type'] = 'bar'
        locs.append(b)
    nloc = build_location_table(locs, conn)
    print(f"  locations: {nloc} rows")

    stats = {
        'drinks': ndrinks,
        'characters': nchars,
        'ingredients': ning,
        'locations': nloc,
    }
    update_game_meta(conn, stats)
    conn.close()

    total = sum(stats.values())
    print(f"\nDone! {total} total structured records added to {DB_PATH}")
    if ndrinks > 0:
        print("\nSample drink data:")
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name, cost, recipe, flavor FROM drinks LIMIT 3")
        for r in cur.fetchall():
            print(f"  {r['name']}: ${r['cost']}, {r['flavor']}, recipe={r['recipe']}")
        conn.close()


if __name__ == '__main__':
    main()
