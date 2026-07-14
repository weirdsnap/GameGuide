"""
Hollow Knight 结构化数据库模块。

从 HallownestAPI JSON 数据构建 SQLite 数据库，
提供精确数值查询（Boss HP、护符 Cost、技能伤害等），
补充 RAG 在数值类问题上的不足。
"""

import json
import os
import sqlite3
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DB_DIR = Path(__file__).resolve().parent.parent.parent / "games" / "hollow_knight"
DB_PATH = DB_DIR / "hk_data.db"
API_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
WIKI_DATA_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "wiki_data.md"

# ── Schema ──

SCHEMA = """
CREATE TABLE IF NOT EXISTS charms (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    notch_cost INTEGER,
    cost TEXT,
    effect TEXT,
    description TEXT,
    acquisition TEXT,
    location TEXT,
    area_slug TEXT,
    area_name TEXT,
    inventory_order INTEGER,
    fragile INTEGER DEFAULT 0,
    upgrade_of TEXT,
    upgrades_to TEXT,
    synergies TEXT,
    merchant TEXT,
    verified INTEGER DEFAULT 0,
    wiki_slug TEXT
);

CREATE TABLE IF NOT EXISTS bosses (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    optional INTEGER DEFAULT 0,
    hp_base INTEGER,
    geo INTEGER,
    area_slug TEXT,
    area_name TEXT,
    rewards TEXT,
    attacks TEXT,
    phases TEXT,
    music TEXT,
    hunter_journal TEXT,
    verified INTEGER DEFAULT 0,
    wiki_slug TEXT
);

CREATE TABLE IF NOT EXISTS skills (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT,
    effect TEXT,
    description TEXT,
    acquisition TEXT,
    area_slug TEXT,
    area_name TEXT,
    damage TEXT,
    soul_cost TEXT,
    upgrade_of TEXT,
    upgrades_to TEXT,
    verified INTEGER DEFAULT 0,
    wiki_slug TEXT
);

CREATE TABLE IF NOT EXISTS areas (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT,
    description TEXT,
    parent TEXT,
    stag_station INTEGER DEFAULT 0,
    connects_to TEXT,
    music TEXT,
    verified INTEGER DEFAULT 0,
    wiki_slug TEXT
);

CREATE TABLE IF NOT EXISTS characters (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT,
    role TEXT,
    location TEXT,
    description TEXT,
    hp INTEGER,
    damage TEXT,
    geo_drop INTEGER,
    is_boss INTEGER DEFAULT 0,
    area_slug TEXT,
    area_name TEXT,
    boss_slug TEXT,
    sells TEXT,
    verified INTEGER DEFAULT 0,
    wiki_slug TEXT
);

CREATE TABLE IF NOT EXISTS items (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT,
    effect TEXT,
    description TEXT,
    location TEXT,
    cost TEXT,
    wiki_slug TEXT
);

CREATE TABLE IF NOT EXISTS enemies (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hp INTEGER,
    damage TEXT,
    geo_drop INTEGER,
    location TEXT,
    area TEXT,
    description TEXT,
    wiki_slug TEXT
);

CREATE TABLE IF NOT EXISTS game_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class HollowKnightDB:
    """Hollow Knight 结构化数据库接口。"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = sqlite3.connect(str(self.db_path))
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def init(self):
        """创建表结构。"""
        conn = self.connect()
        conn.executescript(SCHEMA)
        conn.commit()

    # ══════════════════════════════════════════
    # 数据填充
    # ══════════════════════════════════════════

    def populate_charms(self):
        conn = self.connect()
        data_dir = API_DATA_DIR / "charms"
        count = 0
        for fname in sorted(os.listdir(data_dir)):
            if not fname.endswith(".json") or fname.startswith("_"):
                continue
            with open(data_dir / fname) as f:
                d = json.load(f)

            conn.execute("""
                INSERT OR REPLACE INTO charms
                    (slug, name, notch_cost, cost, effect, description,
                     acquisition, location, area_slug, area_name,
                     inventory_order, fragile, upgrade_of, upgrades_to,
                     synergies, merchant, verified, wiki_slug)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d.get("slug"), d.get("name"),
                d.get("notchCost"),
                str(d.get("cost", "")) if d.get("cost") else None,
                d.get("effect"), d.get("description"),
                d.get("acquisition"), d.get("location"),
                d.get("area", {}).get("slug") if d.get("area") else None,
                d.get("area", {}).get("name") if d.get("area") else None,
                d.get("inventoryOrder"),
                1 if d.get("fragile") else 0,
                d.get("upgradeOf"), d.get("upgradesTo"),
                json.dumps(d.get("synergies", []), ensure_ascii=False) if d.get("synergies") else None,
                d.get("merchant"),
                1 if d.get("verified") else 0,
                d.get("wikiSlug")
            ))
            count += 1
        conn.commit()
        return count

    def populate_bosses(self):
        conn = self.connect()
        data_dir = API_DATA_DIR / "bosses"
        count = 0
        for fname in sorted(os.listdir(data_dir)):
            if not fname.endswith(".json") or fname.startswith("_"):
                continue
            with open(data_dir / fname) as f:
                d = json.load(f)

            hp_base = d.get("hp", {}).get("base") if d.get("hp") else None

            conn.execute("""
                INSERT OR REPLACE INTO bosses
                    (slug, name, optional, hp_base, geo,
                     area_slug, area_name, rewards, attacks, phases,
                     music, hunter_journal, verified, wiki_slug)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d.get("slug"), d.get("name"),
                1 if d.get("optional") else 0,
                hp_base,
                d.get("geo"),
                d.get("area", {}).get("slug") if d.get("area") else None,
                d.get("area", {}).get("name") if d.get("area") else None,
                json.dumps(d.get("rewards", []), ensure_ascii=False) if d.get("rewards") else None,
                json.dumps(d.get("attacks", []), ensure_ascii=False) if d.get("attacks") else None,
                json.dumps(d.get("phases", []), ensure_ascii=False) if d.get("phases") else None,
                json.dumps(d.get("music", {}), ensure_ascii=False) if d.get("music") else None,
                json.dumps(d.get("hunterJournal", {}), ensure_ascii=False) if d.get("hunterJournal") else None,
                1 if d.get("verified") else 0,
                d.get("wikiSlug")
            ))
            count += 1
        conn.commit()
        return count

    def populate_skills(self):
        conn = self.connect()
        data_dir = API_DATA_DIR / "skills"
        count = 0
        for fname in sorted(os.listdir(data_dir)):
            if not fname.endswith(".json") or fname.startswith("_"):
                continue
            with open(data_dir / fname) as f:
                d = json.load(f)

            conn.execute("""
                INSERT OR REPLACE INTO skills
                    (slug, name, kind, effect, description,
                     acquisition, area_slug, area_name,
                     damage, soul_cost,
                     upgrade_of, upgrades_to, verified, wiki_slug)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d.get("slug"), d.get("name"),
                d.get("kind"), d.get("effect"), d.get("description"),
                d.get("acquisition"),
                d.get("area", {}).get("slug") if d.get("area") else None,
                d.get("area", {}).get("name") if d.get("area") else None,
                str(d.get("damage", "")) if d.get("damage") else None,
                str(d.get("soulCost", "")) if d.get("soulCost") else None,
                d.get("upgradeOf"), d.get("upgradesTo"),
                1 if d.get("verified") else 0,
                d.get("wikiSlug")
            ))
            count += 1
        conn.commit()
        return count

    def populate_areas(self):
        conn = self.connect()
        data_dir = API_DATA_DIR / "areas"
        count = 0
        for fname in sorted(os.listdir(data_dir)):
            if not fname.endswith(".json") or fname.startswith("_"):
                continue
            with open(data_dir / fname) as f:
                d = json.load(f)

            conn.execute("""
                INSERT OR REPLACE INTO areas
                    (slug, name, kind, description, parent,
                     stag_station, connects_to, music, verified, wiki_slug)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                d.get("slug"), d.get("name"),
                d.get("kind"), d.get("description"), d.get("parent"),
                1 if d.get("stagStation") else 0,
                json.dumps(d.get("connectsTo", {}), ensure_ascii=False) if d.get("connectsTo") else None,
                json.dumps(d.get("music", {}), ensure_ascii=False) if d.get("music") else None,
                1 if d.get("verified") else 0,
                d.get("wikiSlug")
            ))
            count += 1
        conn.commit()
        return count

    def populate_characters(self):
        conn = self.connect()
        data_dir = API_DATA_DIR / "characters"
        count = 0
        for fname in sorted(os.listdir(data_dir)):
            if not fname.endswith(".json") or fname.startswith("_"):
                continue
            with open(data_dir / fname) as f:
                d = json.load(f)

            conn.execute("""
                INSERT OR REPLACE INTO characters
                    (slug, name, kind, role, location, description,
                     hp, damage, geo_drop, is_boss, area_slug, area_name,
                     boss_slug, sells, verified, wiki_slug)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d.get("slug"), d.get("name"),
                d.get("kind"), d.get("role"), d.get("location"), d.get("description"),
                d.get("hp"), str(d.get("damage", "")) if d.get("damage") else None,
                d.get("geoDrop"),
                1 if d.get("isBoss") else 0,
                d.get("area", {}).get("slug") if d.get("area") else None,
                d.get("area", {}).get("name") if d.get("area") else None,
                d.get("bossSlug"),
                json.dumps(d.get("sells", []), ensure_ascii=False) if d.get("sells") else None,
                1 if d.get("verified") else 0,
                d.get("wikiSlug")
            ))
            count += 1
        conn.commit()
        return count

    def populate_from_wiki_enemies(self):
        """从 wiki 数据中提取敌人数值信息。"""
        if not WIKI_DATA_FILE.exists():
            return 0

        text = WIKI_DATA_FILE.read_text(encoding="utf-8")
        import re
        chunks = re.split(r'(?=^#\s*文档)', text, flags=re.MULTILINE)

        conn = self.connect()
        count = 0
        for chunk in chunks:
            if "- 类别：enemies" not in chunk or "enemies" not in chunk[:200]:
                continue

            name_m = re.search(r"# 文档[：:]\s*(.+)", chunk)
            if not name_m:
                continue
            name = name_m.group(1).strip()
            slug = name.lower().replace(" ", "_").replace("'", "").replace("(", "").replace(")", "")

            # Try to extract HP, Geo drop
            hp = None
            geo = None
            damage = None
            location = None
            area = None

            # Look for HP pattern
            hp_m = re.search(r"(\d+)\s*HP", chunk)
            if hp_m:
                hp = int(hp_m.group(1))

            # Look for Geo pattern  
            geo_m = re.search(r"(\d+)\s*[Gg]eo", chunk)
            if geo_m:
                geo = int(geo_m.group(1))

            # Location
            loc_m = re.search(r"(?:位置|Location)[：:]\s*(.+)", chunk)
            if loc_m:
                location = loc_m.group(1).strip()

            conn.execute("""
                INSERT OR REPLACE INTO enemies
                    (slug, name, hp, damage, geo_drop, location, area, description, wiki_slug)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                slug, name, hp, damage, geo, location, area,
                chunk[:500] if len(chunk) > 500 else chunk,
                name.replace(" ", "_")
            ))
            count += 1
        conn.commit()
        return count

    def populate_from_wiki_items(self):
        """从 wiki 数据中提取物品信息。"""
        if not WIKI_DATA_FILE.exists():
            return 0

        text = WIKI_DATA_FILE.read_text(encoding="utf-8")
        import re
        chunks = re.split(r'(?=^#\s*文档)', text, flags=re.MULTILINE)

        conn = self.connect()
        count = 0
        for chunk in chunks:
            if "- 类别：items" not in chunk and "items" not in chunk[:200]:
                continue

            name_m = re.search(r"# 文档[：:]\s*(.+)", chunk)
            if not name_m:
                continue
            name = name_m.group(1).strip()
            slug = name.lower().replace(" ", "_").replace("'", "")

            effect = ""
            desc = ""
            location = ""
            cost = None

            # Simple extraction
            if "Usefulness" in chunk:
                desc = chunk.split("Usefulness")[-1].split("\n\n")[0].strip()[:500]

            conn.execute("""
                INSERT OR REPLACE INTO items
                    (slug, name, kind, effect, description, location, cost, wiki_slug)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                slug, name, "item", effect, desc, location, cost,
                name.replace(" ", "_")
            ))
            count += 1
        conn.commit()
        return count

    # ══════════════════════════════════════════
    # 查询接口
    # ══════════════════════════════════════════

    def query(self, sql: str, params: tuple = ()) -> List[Dict]:
        """通用 SQL 查询，返回字典列表。"""
        conn = self.connect()
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_charm(self, name_or_slug: str) -> Optional[Dict]:
        """查询单个护符。"""
        rows = self.query(
            "SELECT * FROM charms WHERE slug = ? OR name = ? LIMIT 1",
            (name_or_slug.lower(), name_or_slug)
        )
        return rows[0] if rows else None

    def list_charms_by_cost(self, notch_cost: int) -> List[Dict]:
        """按 Cost 列出护符。"""
        return self.query(
            "SELECT name, notch_cost, effect, location FROM charms WHERE notch_cost = ? ORDER BY name",
            (notch_cost,)
        )

    def list_charms_by_location(self, location: str) -> List[Dict]:
        """按位置列出护符。"""
        return self.query(
            "SELECT name, notch_cost, effect, location FROM charms WHERE location LIKE ? ORDER BY name",
            (f"%{location}%",)
        )

    def get_boss(self, name_or_slug: str) -> Optional[Dict]:
        """查询单个 Boss。"""
        rows = self.query(
            "SELECT * FROM bosses WHERE slug = ? OR name = ? LIMIT 1",
            (name_or_slug.lower(), name_or_slug)
        )
        return rows[0] if rows else None

    def get_skill(self, name_or_slug: str) -> Optional[Dict]:
        """查询单个技能。"""
        rows = self.query(
            "SELECT * FROM skills WHERE slug = ? OR name = ? LIMIT 1",
            (name_or_slug.lower(), name_or_slug)
        )
        return rows[0] if rows else None

    def get_area(self, name_or_slug: str) -> Optional[Dict]:
        """查询单个区域。"""
        rows = self.query(
            "SELECT * FROM areas WHERE slug = ? OR name = ? LIMIT 1",
            (name_or_slug.lower(), name_or_slug)
        )
        return rows[0] if rows else None

    def get_character(self, name_or_slug: str) -> Optional[Dict]:
        """查询单个角色/敌人。"""
        rows = self.query(
            "SELECT * FROM characters WHERE slug = ? OR name = ? LIMIT 1",
            (name_or_slug.lower(), name_or_slug)
        )
        return rows[0] if rows else None

    def search_named(self, table: str, keyword: str) -> List[Dict]:
        """按名称关键词搜索指定表。"""
        allowed = {"charms", "bosses", "skills", "areas", "characters", "items", "enemies"}
        if table not in allowed:
            return [{"error": f"不支持的表: {table}, 可选: {', '.join(sorted(allowed))}"}]
        rows = self.query(
            f"SELECT * FROM {table} WHERE name LIKE ? ORDER BY name LIMIT 10",
            (f"%{keyword}%",)
        )
        return rows

    def boss_hp_range(self, min_hp: int = 0, max_hp: int = 99999) -> List[Dict]:
        """按 HP 范围查找 Boss。"""
        return self.query(
            "SELECT name, hp_base, geo, area_name FROM bosses WHERE hp_base >= ? AND hp_base <= ? ORDER BY hp_base",
            (min_hp, max_hp)
        )

    def stat_summary(self) -> Dict:
        """数据库统计摘要。"""
        conn = self.connect()
        stats = {}
        for table in ["charms", "bosses", "skills", "areas", "characters", "items", "enemies"]:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
            stats[table] = row[0]
        return stats

    def get_all(self, table: str) -> List[Dict]:
        """获取某表所有数据（简短字段）。"""
        allowed = {"charms", "bosses", "skills", "areas", "characters", "items", "enemies"}
        if table not in allowed:
            return [{"error": f"不支持的表: {table}"}]
        if table == "charms":
            return self.query("SELECT name, notch_cost, effect, area_name, location FROM charms ORDER BY name")
        elif table == "bosses":
            return self.query("SELECT name, hp_base, geo, area_name, optional FROM bosses ORDER BY name")
        elif table == "skills":
            return self.query("SELECT name, kind, area_name, damage FROM skills ORDER BY name")
        elif table == "areas":
            return self.query("SELECT name, kind, parent, stag_station FROM areas ORDER BY name")
        elif table == "characters":
            return self.query("SELECT name, kind, role, hp, geo_drop FROM characters ORDER BY name")
        elif table == "items":
            return self.query("SELECT name, kind, location FROM items ORDER BY name")
        elif table == "enemies":
            return self.query("SELECT name, hp, damage, geo_drop, location FROM enemies ORDER BY name")


def build_database(db_path: Optional[Path] = None) -> Tuple[int, Dict]:
    """从所有数据源构建完整的 SQLite 数据库。

    Returns:
        (总条目数, 各类别统计)
    """
    hkdb = HollowKnightDB(db_path)
    hkdb.init()

    stats = {}
    stats["charms"] = hkdb.populate_charms()
    print(f"  ✅ 护符: {stats['charms']} 条")
    stats["bosses"] = hkdb.populate_bosses()
    print(f"  ✅ Boss: {stats['bosses']} 条")
    stats["skills"] = hkdb.populate_skills()
    print(f"  ✅ 技能: {stats['skills']} 条")
    stats["areas"] = hkdb.populate_areas()
    print(f"  ✅ 区域: {stats['areas']} 条")
    stats["characters"] = hkdb.populate_characters()
    print(f"  ✅ 角色: {stats['characters']} 条")
    stats["enemies"] = hkdb.populate_from_wiki_enemies()
    print(f"  ✅ 敌人(wiki): {stats['enemies']} 条")
    stats["items"] = hkdb.populate_from_wiki_items()
    print(f"  ✅ 物品(wiki): {stats['items']} 条")

    total = sum(stats.values())
    hkdb.close()
    return total, stats


if __name__ == "__main__":
    import sys
    print("🔧 构建 Hollow Knight 结构化数据库...")
    total, stats = build_database()
    print(f"\n📊 总计: {total} 条记录")
    print(f"   数据库路径: {DB_PATH}")
