#!/usr/bin/env python3
"""
Phase 2 数据修复脚本 (beta1 → beta2)
修复项：
1. 同名但不同实体的分类修复（如 Dashmaster ≠ Sprintmaster）
2. 真正的重复实体合并（Fandom + 独立维基各一条）
3. 反向关联补全（区域←物品/护符/Boss/敌人）
4. 缺失字段补全（location, related_entities 等）
5. 输出 VERSION 文件
"""

import json
import os
import sys
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path("/data/learning/agent/data")
INPUT_FILE = DATA_DIR / "phase2_merged.jsonl"
OUTPUT_FILE = DATA_DIR / "phase2_fixed.jsonl"
VERSION_FILE = DATA_DIR / "VERSION"

# ─── 1. 需要拆分的同名实体 ───
# key: title → [(_entity_key, new_title)]
TITLE_SPLITS = {
    "冲刺大师": [("dashmaster", "冲刺大师"), ("sprintmaster", "急速冲刺")],
    "乌玛": [("ooma", "乌玛"), ("uoma", "乌玛(敌人)")],
}

# ─── 2. 真正的重复实体合并规则 ───
# 当一个 title 有 ≥2 条且不在 TITLE_SPLITS 中，执行合并
# 合并策略：取描述更长的 + 合并关联实体


def load_docs() -> list[dict]:
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def fix_title_splits(docs: list[dict]) -> list[dict]:
    """修复同名不同实体的标题"""
    fixed = []
    splits_applied = 0
    for d in docs:
        title = d["title"]
        key = d["_entity_key"]
        if title in TITLE_SPLITS:
            for ek, new_title in TITLE_SPLITS[title]:
                if key == ek:
                    d["title"] = new_title
                    splits_applied += 1
                    break
        fixed.append(d)
    print(f"  [修复1] 拆分同名实体: {splits_applied} 条改名")
    return fixed


def merge_duplicates(docs: list[dict]) -> list[dict]:
    """合并真正的重复实体（按 title 分组）"""
    groups = defaultdict(list)
    for d in docs:
        groups[d["title"]].append(d)

    merged = []
    dups_merged = 0
    for title, items in groups.items():
        if len(items) == 1:
            merged.append(items[0])
        else:
            # 多个来源合并
            # 选描述最长的作为基础
            items_sorted = sorted(items, key=lambda x: len(x.get("description", "")), reverse=True)
            base = dict(items_sorted[0])  # 深拷贝

            # 合并 related_entities（去重）
            seen_rels = set()
            all_rels = []
            for item in items:
                for rel in item.get("related_entities", []):
                    rel_key = (rel.get("name", ""), rel.get("relation", ""))
                    if rel_key not in seen_rels:
                        seen_rels.add(rel_key)
                        all_rels.append(rel)

            base["related_entities"] = all_rels

            # 合并 keywords
            seen_kw = set(base.get("keywords", []))
            for item in items:
                for kw in item.get("keywords", []):
                    if kw not in seen_kw:
                        seen_kw.add(kw)
            base["keywords"] = list(seen_kw)

            # 合并 location（取非空更详细的）
            locs = [item.get("location", "") for item in items if item.get("location")]
            if locs:
                base["location"] = max(locs, key=len)

            # 合并 description（如果别的来源有补充信息，拼接到后面）
            descriptions = [item.get("description", "") for item in items]
            base["description"] = max(descriptions, key=len)

            # summary 取最长的
            summaries = [item.get("summary", "") for item in items if item.get("summary")]
            if summaries:
                base["summary"] = max(summaries, key=len)

            # 记录来源
            base["_sources"] = [item.get("_entity_key", "") for item in items]

            merged.append(base)
            dups_merged += 1

    print(f"  [修复2] 合并重复实体: {sum(len(items) for items in groups.values() if len(items)>1)}条 → {dups_merged}条")
    return merged


def build_reverse_links(docs: list[dict]) -> list[dict]:
    """补全反向关联"""
    # 建立 name → entity 映射
    name_to_doc = {}
    for d in docs:
        name_to_doc[d["title"]] = d
        name_to_doc[d.get("title_en", "")] = d
        # 也支持英文大小写变体
        if d.get("title_en"):
            name_to_doc[d["title_en"].lower().replace("-", " ")] = d

    # 收集所有"被引用"关系
    referrers = defaultdict(list)  # ref_name → [(referrer_title, relation)]
    for d in docs:
        for rel in d.get("related_entities", []):
            ref_name = rel.get("name", "")
            referrers[ref_name].append((d["title"], rel.get("relation", ""), d.get("category", "")))

    reverse_added = 0
    for d in docs:
        existing_names = {r.get("name", "") for r in d.get("related_entities", [])}
        title = d["title"]
        title_en = d.get("title_en", "")

        # 找谁引用了这个实体
        new_refs = []
        for ref_name, refs in referrers.items():
            # 检查 ref_name 是否指向当前实体
            if ref_name == title or ref_name == title_en or ref_name == title_en.lower().replace("-", " "):
                for ref_title, relation, ref_cat in refs:
                    if ref_title not in existing_names and ref_title != title:
                        new_refs.append({
                            "name": ref_title,
                            "relation": f"被{relation}",
                            "category": ref_cat
                        })

        if new_refs:
            # 去重后添加
            new_names = set()
            for nr in new_refs:
                if nr["name"] not in new_names:
                    new_names.add(nr["name"])
                    d.setdefault("related_entities", []).append(nr)
                    reverse_added += 1

    print(f"  [修复3] 补反向关联: 添加 {reverse_added} 条")
    return docs


def fill_missing_fields(docs: list[dict]) -> list[dict]:
    """补全缺失的关键字段"""
    loc_filled = 0
    rel_filled = 0

    for d in docs:
        # 从 summary 或 description 中提取位置信息
        if not d.get("location"):
            summary = d.get("summary", "")
            desc = d.get("description", "")
            text = summary + desc

            # 尝试从关联实体中找位置信息
            for rel in d.get("related_entities", []):
                if "区域" in rel.get("category", "") or "位置" in rel.get("relation", ""):
                    d["location"] = rel["name"]
                    loc_filled += 1
                    break
            else:
                # 从文本中提取
                import re
                loc_patterns = [
                    r"(?:位于|在|从)(\S+(?:道|径|路|地|境|区|山|川|湖|林|原|园|堡|城|村|巢|殿|殿|堂|室|所))",
                    r"(?:位于|在)(\S+(?:王国边缘|泪水之城|真菌荒野|水晶山峰|皇家水道|深邃巢穴|呼啸悬崖|王后花园|国王小径|遗忘十字路))",
                ]
                for pat in loc_patterns:
                    m = re.search(pat, text)
                    if m:
                        d["location"] = m.group(1)
                        loc_filled += 1
                        break

    print(f"  [修复4] 补位置信息: {loc_filled} 条")
    return docs


def save_version(docs: list[dict], changelog: list[str]):
    """写入 VERSION 文件"""
    version_data = {
        "version": "beta2",
        "previous_version": "beta1",
        "built_at": "2026-07-07",
        "entity_count": len(docs),
        "category_distribution": dict(sorted(defaultdict(int, {
            c: sum(1 for d in docs if d["category"] == c)
            for c in set(d["category"] for d in docs)
        }).items())),
        "changelog": changelog
    }
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(version_data, f, ensure_ascii=False, indent=2)
    print(f"\n  VERSION 写入: {VERSION_FILE}")
    print(f"  version: beta2, 实体数: {len(docs)}")


def main():
    print("Phase 2 数据修复脚本")
    print("=" * 50)

    # 加载
    print("\n[加载] 读取 phase2_merged.jsonl...")
    docs = load_docs()
    print(f"  原始实体数: {len(docs)}")

    # 修复1: 拆分同名不同实体
    print("\n[修复1] 拆分同名不同实体...")
    docs = fix_title_splits(docs)

    # 修复2: 合并真正重复
    print("\n[修复2] 合并重复实体...")
    docs = merge_duplicates(docs)

    # 修复3: 补反向关联
    print("\n[修复3] 补反向关联...")
    docs = build_reverse_links(docs)

    # 修复4: 补缺失字段
    print("\n[修复4] 补缺失字段...")
    docs = fill_missing_fields(docs)

    # 输出
    print(f"\n[输出] 写入 phase2_fixed.jsonl...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for d in docs:
            # 清理临时字段
            d.pop("_sources", None)
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"  修复后实体数: {len(docs)}")

    # 写入 VERSION
    changelog = [
        "beta2: Phase 2 数据首次修复",
        "- 修复: 拆分同名不同实体 (Dashmaster/Sprintmaster, Ooma/Uoma)",
        f"- 修复: 合并 {sum(1 for _ in open(INPUT_FILE)) - len(docs)} 组重复实体",
        "- 修复: 补充反向关联 (区域←物品/护符/Boss/敌人)",
        "- 修复: 补充缺失的位置信息"
    ]
    save_version(docs, changelog)

    # 统计
    from collections import Counter
    cats = Counter(d["category"] for d in docs)
    print(f"\n[统计] 修复后分类分布:")
    for c, n in cats.most_common():
        print(f"  {c}: {n}")

    # 检查修复效果
    no_rel = sum(1 for d in docs if not d.get("related_entities"))
    no_loc = sum(1 for d in docs if d["category"] in ("护符", "道具", "技能", "Boss", "敌人") and not d.get("location"))
    print(f"\n[检查]")
    print(f"  零关联实体: {no_rel} 条")
    print(f"  缺位置实体(应含): {no_loc} 条")

    print("\n✅ 修复完成!")


if __name__ == "__main__":
    main()
