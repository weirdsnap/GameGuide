#!/usr/bin/env python3
"""
Generic Game Database Builder.
Parses wiki_data.md (both # 文档  and ## 格式) and builds a minimal SQLite DB.
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAMES_DIR = PROJECT_ROOT / "games"

GAMES = {
    "va11halla": {
        "name": "VA-11 Hall-A",
        "data_path": GAMES_DIR / "va11halla" / "data" / "wiki_data.md",
        "db_path": GAMES_DIR / "va11halla" / "va11halla_data.db",
    },
    "cyberpunk2077": {
        "name": "Cyberpunk 2077",
        "data_path": GAMES_DIR / "cyberpunk2077" / "data" / "wiki_data.md",
        "db_path": GAMES_DIR / "cyberpunk2077" / "cyberpunk2077_data.db",
    },
}


def parse_wiki_docs(filepath: Path):
    """Parse wiki_data.md and yield (title, category, content) tuples."""
    if not filepath.exists():
        print(f"  ❌ 找不到: {filepath}")
        return

    text = filepath.read_text(encoding="utf-8")

    # Try format A: # 文档：Title
    chunks_a = re.split(r"(?=^# 文档[：:])", text, flags=re.MULTILINE)
    chunks_a = [c for c in chunks_a if c.strip().startswith("# 文档")]

    if chunks_a:
        for chunk in chunks_a:
            chunk = chunk.strip()
            lines = chunk.split("\n")
            title_m = re.search(r"^#\s*文档[：:]\s*(.*)", lines[0])
            title = title_m.group(1).strip() if title_m else "Unknown"
            cat = ""
            for l in lines[:6]:
                m = re.search(r"- 类别[：:]\s*(.*)", l)
                if m:
                    cat = m.group(1).strip()
                    break
            content_lines = [l for l in lines if not any(
                l.startswith(p) for p in ("# 文档", "- 类别", "- 标识", "- 来源", "- 路径"))]
            content = "\n".join(content_lines).strip()
            yield title, cat, content
        return

    # Try format B: ## Title
    chunks_b = re.split(r"(?=^##\s+.*(?:\n|$))", text, flags=re.MULTILINE)
    # skip header chunk
    chunks_b = [c for c in chunks_b if c.strip().startswith("##")]
    if chunks_b:
        for chunk in chunks_b:
            chunk = chunk.strip()
            lines = chunk.split("\n")
            title_m = re.search(r"^##\s+(.*)", lines[0])
            title = title_m.group(1).strip() if title_m else "Unknown"
            content_lines = lines[1:]
            while content_lines and content_lines[-1].strip() in ("---", ""):
                content_lines = content_lines[:-1]
            content = "\n".join(content_lines).strip()
            yield title, "", content


def build_game_db(game_key: str):
    cfg = GAMES[game_key]
    print(f"\n📦 {cfg['name']}")

    db_path = cfg["db_path"]
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS game_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT DEFAULT '',
            wiki_slug TEXT,
            content TEXT DEFAULT ''
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_title ON pages(title)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pages_category ON pages(category)")

    cur.execute("DELETE FROM pages")
    cur.execute("DELETE FROM game_meta")

    count = 0
    for title, cat, content in parse_wiki_docs(cfg["data_path"]):
        if not content:
            continue
        slug = re.sub(r'[^a-zA-Z0-9_\-]', '_', title.lower().replace(" ", "_"))
        cur.execute(
            "INSERT INTO pages (title, category, wiki_slug, content) VALUES (?, ?, ?, ?)",
            (title, cat, slug, content[:5000]),
        )
        count += 1

    cur.execute("INSERT OR REPLACE INTO game_meta (key, value) VALUES (?, ?)",
                ("page_count", str(count)))
    cur.execute("INSERT OR REPLACE INTO game_meta (key, value) VALUES (?, ?)",
                ("source", "fandom_wiki"))
    conn.commit()
    conn.close()

    print(f"  ✅ {count} 页写入 {db_path.name}")


def main():
    parser = argparse.ArgumentParser(description="Generic Game DB Builder")
    parser.add_argument("--game", "-g", required=True,
                        choices=list(GAMES.keys()) + ["all"],
                        help="Game key or 'all'")
    args = parser.parse_args()

    if args.game == "all":
        for key in GAMES:
            build_game_db(key)
    else:
        build_game_db(args.game)

    print("\n✅ 完成")


if __name__ == "__main__":
    main()
