#!/usr/bin/env python3
"""
用 HallownestAPI 的结构化位置数据补充 wiki_data.md 中缺失的位置信息。

用法: python scripts/enrich_wiki_locations.py
输出: 覆盖 wiki_data.md（备份存为 wiki_data.md.bak）
"""

import json
import re
import shutil
from pathlib import Path
from typing import Dict, Optional

DATA_DIR = Path("/data/learning/agent/data")
WIKI_PATH = DATA_DIR / "wiki_data.md"
BACKUP_PATH = DATA_DIR / "wiki_data.md.bak"

# ====== 从 HallownestAPI 加载位置数据 ======

def load_api_locations() -> Dict[str, Dict[str, str]]:
    """从 HallownestAPI JSON 中提取所有实体的位置信息。
    Returns: {slug: {"name": str, "category": str, "location": str}}"""
    locations = {}

    for category_dir in ["areas", "bosses", "characters", "charms", "skills"]:
        base = DATA_DIR / category_dir
        if not base.exists():
            continue
        for f in sorted(base.glob("*.json")):
            if f.stem.startswith("_"):
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            name = data.get("name", f.stem)
            slug = data.get("slug", f.stem)

            # 提取位置信息——支持多个字段名
            loc = data.get("location") or data.get("location_description") or ""
            if not loc and "area" in data:
                loc = data["area"]
            if isinstance(loc, str) and loc.strip():
                locations[slug] = {
                    "name": name,
                    "category": category_dir,
                    "location": loc.strip(),
                }
    return locations


# ====== 判断 wiki 条目是否包含位置信息 ======

LOCATION_KEYWORDS = [
    "found in", "located in", "located at", "can be found",
    "awarded by", "earned by", "reward for", "rewarded by",
    "gifted by", "speak to", "given by",
    "after defeating", "after collecting", "after acquiring",
    "hidden", "found near", "deep in", "atop",
    "at the", "behind a",
]


def has_location_info(text: str) -> bool:
    """粗略判断文本是否包含位置描述。"""
    lower = text.lower()
    return any(kw in lower for kw in LOCATION_KEYWORDS)


# ====== 主逻辑 ======

def main():
    print("📖 读取 wiki_data.md...")
    wiki_text = WIKI_PATH.read_text(encoding="utf-8")

    print("📖 加载 HallownestAPI 位置数据...")
    api_locs = load_api_locations()
    print(f"  → {len(api_locs)} 个实体有位置数据")

    # 按 # 文档 分割
    docs = re.split(r"(?=^# 文档[：:])", wiki_text, flags=re.MULTILINE)

    enriched_count = 0
    new_docs = []

    for doc in docs:
        doc = doc.rstrip()
        if not doc.strip():
            continue

        # 获取实体 name 和 category
        name_match = re.search(r"^#\s*文档[：:]\s*(.+)", doc, re.MULTILINE)
        cat_match = re.search(r"- 类别[：:]\s*(.+)", doc)
        slug_match = re.search(r"- 标识[：:]\s*(.+)", doc)

        doc_name = name_match.group(1).strip() if name_match else ""
        doc_cat = cat_match.group(1).strip() if cat_match else ""
        doc_slug = slug_match.group(1).strip() if slug_match else doc_name.lower().replace(" ", "-")

        # 只在主要类别中补充位置（charm/skill/area/boss）
        if doc_cat not in ("charms", "skills", "areas", "bosses"):
            new_docs.append(doc)
            continue

        # 检查正文是否已有位置信息
        body = doc[name_match.end() if name_match else 0:]
        if has_location_info(body):
            new_docs.append(doc)
            continue

        # 查找 HallownestAPI 中的位置
        # 先用 slug 匹配，再用 name 模糊匹配
        api_entry = api_locs.get(doc_slug)
        if not api_entry:
            # 尝试 name 匹配
            for slug, entry in api_locs.items():
                if entry["name"].lower() == doc_name.lower():
                    api_entry = entry
                    break
        if not api_entry:
            new_docs.append(doc)
            continue

        # 注入位置信息
        loc_text = api_entry["location"]
        inject_line = f"**位置**: {loc_text}\n"

        # 找到正文起始位置（metadata 后的第一个空行）
        # metadata 行通常以 - 开头（- 类别、- 标识、- 来源）
        # 找到最后一个 metadata 行后的第一个 \n\n
        meta_end = doc.rfind("\n- ")
        if meta_end >= 0:
            body_start = doc.find("\n\n", meta_end)
        else:
            body_start = doc.find("\n\n", doc.find("\n#") if "#" in doc else 0)
        if body_start < 0:
            body_start = len(doc)

        # 位置信息插入在 metadata 之后、正文之前
        doc = doc[:body_start] + "\n" + inject_line + doc[body_start + 1:]
        enriched_count += 1
        new_docs.append(doc)

    output = "\n\n".join(new_docs) + "\n"

    # 备份
    shutil.copy2(WIKI_PATH, BACKUP_PATH)
    print(f"  ✅ 备份已保存: {BACKUP_PATH}")

    WIKI_PATH.write_text(output, encoding="utf-8")
    print(f"  ✅ 已写入: {WIKI_PATH}")
    print(f"\n📊 统计:")
    print(f"  补充位置信息的条目: {enriched_count}")
    print(f"  Wiki 文档总数: {len(new_docs)}")


if __name__ == "__main__":
    main()
