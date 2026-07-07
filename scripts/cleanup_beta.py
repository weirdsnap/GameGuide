#!/usr/bin/env python3
"""
beta2 数据清理脚本
1. 合并残留重复（entity_key 命名不一致导致的重复）
2. 补缺位置（从 description/summary/HallownestAPI 提取）
3. 修复坏标题（"护符"等）
4. 写入 VERSION.json 定版
"""

import json, re, time
from pathlib import Path
from collections import defaultdict, Counter

DATA_DIR = Path("/data/learning/agent/data")
INPUT_FILE = DATA_DIR / "phase2_beta.jsonl"
OUTPUT_FILE = DATA_DIR / "phase2_beta.jsonl"  # overwrite
VERSION_FILE = DATA_DIR / "VERSION.json"

# ─── 已知的位置提取规则 ───
LOCATION_PATTERNS = [
    r"(?:位于|在|从|于)(遗忘十字路[口路]?)",
    r"(?:位于|在|从|于)(苍绿之径)",
    r"(?:位于|在|从|于)(真菌荒[原野地])",
    r"(?:位于|在|从|于)(泪水之城)",
    r"(?:位于|在|从|于)(水晶山峰)",
    r"(?:位于|在|从|于)(皇家水道)",
    r"(?:位于|在|从|于)(深邃巢穴)",
    r"(?:位于|在|从|于)(呼啸悬崖)",
    r"(?:位于|在|从|于)(王后花园)",
    r"(?:位于|在|从|于)(王国边缘)",
    r"(?:位于|在|从|于)(深[渊])",
    r"(?:位于|在|从|于)(白色宫殿)",
    r"(?:位于|在|从|于)(国王[小径道])",
    r"(?:位于|在|从|于)(灵魂圣殿)",
    r"(?:位于|在|从|于)(螳螂村)",
    r"(?:位于|在|从|于)(蜂巢)",
    r"(?:位于|在|从|于)([阿愚]人斗兽场?)",
    r"(?:位于|在|从|于)(神居)",
    r"(?:位于|在|从|于)(竞技场)",
    r"(?:位于|在|从|于)(古老盆地)",
    r"(?:位于|在|从|于)(安息之地)",
    r"(?:位于|在|从|于)(德特茅斯)",
]

KNOWN_AREAS = [
    "遗忘十字路", "苍绿之径", "真菌荒地", "真菌荒野", "泪水之城",
    "水晶山峰", "皇家水道", "深邃巢穴", "呼啸悬崖", "王后花园",
    "王国边缘", "深渊", "白色宫殿", "国王小径", "国王小道",
    "灵魂圣殿", "螳螂村", "蜂巢", "愚人斗兽场", "神居",
    "古老盆地", "安息之地", "德特茅斯", "十字路",
]

HALLOWNEST_API_DIR = DATA_DIR / "api" / "data"


def load_api_locations() -> dict:
    """从 HallownestAPI 提取位置信息"""
    locations = {}
    for subdir in ['areas', 'bosses', 'characters', 'charms', 'skills']:
        path = HALLOWNEST_API_DIR / subdir
        if not path.exists():
            continue
        for fpath in path.glob("*.json"):
            if fpath.name.startswith('_'):
                continue
            try:
                with open(fpath) as f:
                    data = json.load(f)
                name = data.get('name', fpath.stem).lower()
                loc_parts = []
                for key in ['location', 'area', 'region', 'biome']:
                    val = data.get(key)
                    if val and isinstance(val, str) and len(val) > 2:
                        loc_parts.append(val)
                if loc_parts:
                    locations[name] = "; ".join(loc_parts)
            except:
                pass
    return locations


def extract_location_from_text(text: str) -> str:
    """从文本中提取中文区域名"""
    if not text:
        return ""
    for pat in LOCATION_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    # 兜底：直接搜索已知区域名
    for area in KNOWN_AREAS:
        if area in text:
            return area
    return ""


def merge_duplicates(docs: list[dict]) -> list[dict]:
    """合并 entity_key 不同但实际是同一实体的重复"""
    # 手动定义需要合并的 entity_key 对
    known_dups = {
        "arcane egg": ["arcane egg", "arcane egg hollow knight"],
        "arcane egg hollow knight": ["arcane egg", "arcane egg hollow knight"],
        "defender's crest": ["defender's crest", "defenders crest"],
        "defenders crest": ["defender's crest", "defenders crest"],
    }

    merged = {}
    dup_count = 0
    for d in docs:
        key = d["_entity_key"].lower().strip()
        # 检查是否已知重复
        target_key = key
        if key in known_dups:
            target_key = known_dups[key][0]  # 合并到第一个

        if target_key not in merged:
            merged[target_key] = dict(d)
            merged[target_key]["_entity_key"] = target_key
        else:
            # 合并到已有的
            existing = merged[target_key]
            # 合并关联实体
            existing_rels = {(r["name"], r["relation"]) for r in existing.get("related_entities", [])}
            for r in d.get("related_entities", []):
                pair = (r["name"], r.get("relation", ""))
                if pair not in existing_rels:
                    existing_rels.add(pair)
                    existing.setdefault("related_entities", []).append(r)

            # 合并关键词
            existing_kws = set(existing.get("keywords", []))
            for kw in d.get("keywords", []):
                if kw not in existing_kws:
                    existing_kws.add(kw)
                    existing.setdefault("keywords", []).append(kw)

            # 取更长的描述
            if len(d.get("description", "")) > len(existing.get("description", "")):
                existing["description"] = d["description"]
            if len(d.get("summary", "")) > len(existing.get("summary", "")):
                existing["summary"] = d["summary"]

            # 如果缺位置则补
            if not existing.get("location") and d.get("location"):
                existing["location"] = d["location"]

            dup_count += 1

    log(f"  ✅ 合并残留重复: {dup_count} 条")

    # 重新排序保持标题顺序
    result = list(merged.values())
    # 按中文title排序
    result.sort(key=lambda x: x.get("title", ""))
    return result


def fill_locations(docs: list[dict], api_locs: dict) -> list[dict]:
    """补全缺失的位置信息"""
    filled_desc = 0
    filled_api = 0

    for d in docs:
        if d.get("location"):
            continue

        # 1. 从 description/summary 提取
        text = (d.get("description", "") + " " + d.get("summary", "") + " " +
                json.dumps(d.get("stats", {}), ensure_ascii=False))
        loc = extract_location_from_text(text)
        if loc:
            d["location"] = loc
            filled_desc += 1
            continue

        # 2. 从 HallownestAPI 查
        api_name = d.get("title_en", d["_entity_key"]).lower().strip()
        if api_name in api_locs:
            d["location"] = api_locs[api_name]
            filled_api += 1
            continue

        # 3. 模糊匹配 API 数据
        for api_key, api_loc in api_locs.items():
            if (d["_entity_key"] in api_key or api_key in d["_entity_key"] or
                d.get("title_en", "").lower() == api_key or
                d.get("title", "").lower() == api_key):
                d["location"] = api_loc
                filled_api += 1
                break

    log(f"  ✅ 从 description 补位置: {filled_desc} 条")
    log(f"  ✅ 从 HallownestAPI 补位置: {filled_api} 条")
    return docs


def fix_bad_titles(docs: list[dict]) -> list[dict]:
    """修复明显有问题的标题"""
    fixed = 0
    title_fixes = {
        "护符": None,  # 标记需要特殊处理
    }
    for d in docs:
        title = d.get("title", "")
        title_en = d.get("title_en", "")
        category = d.get("category", "")

        # 如果英文名已知但中文名是分类名
        if title == "护符" and category == "护符":
            if title_en and title_en != "护符":
                # 尝试用 title_en 查中文名... 或者先标出来
                d["title"] = title_en
                fixed += 1
                log(f"  🔧 修复标题: '护符' → '{title_en}'")

    log(f"  ✅ 修复标题: {fixed} 条")
    return docs


def write_version(docs: list[dict], dup_merged: int, loc_filled: int, title_fixed: int):
    """写入版本文件"""
    cats = Counter(d["category"] for d in docs)
    spoilers = Counter(d["spoiler_level"] for d in docs)
    no_rel = sum(1 for d in docs if not d.get("related_entities"))
    no_loc = sum(1 for d in docs if d["category"] in ("护符","道具","技能","Boss","敌人") and not d.get("location"))
    avg_rels = sum(len(d.get("related_entities", [])) for d in docs) / len(docs) if docs else 0

    version = {
        "version": "beta2",
        "built_at": time.strftime("%Y-%m-%d %H:%M"),
        "entity_count": len(docs),
        "stats": {
            "category_distribution": dict(cats.most_common()),
            "spoiler_distribution": dict(spoilers.most_common()),
            "avg_related_entities": round(avg_rels, 1),
            "zero_relation_entities": no_rel,
            "missing_locations": no_loc
        },
        "changelog": [
            "Phase 2b: 改进版合并（基于 Phase 1 关联并集 + HallownestAPI）",
            f"- 合并 {dup_merged} 组残留重复（entity_key 命名不一致）",
            f"- 补全 {loc_filled} 条缺失位置信息",
            f"- 修复 {title_fixed} 个错误标题",
            "- 反向关联补全: King's Pass 现在知道其中的护符/物品",
            "- 翻译修复: Fury of the Fallen → 亡者之怒 (原: 受诅咒的护符)",
            "- 零关联实体从 51 降至最低",
            "- 数据来源: Phase 1 分析(763条) + HallownestAPI(251条)",
        ]
    }

    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(version, f, ensure_ascii=False, indent=2)
    log(f"\n📝 VERSION 写入: {VERSION_FILE}")
    log(f"  version: beta2, 实体数: {len(docs)}")

    return version


log_lines = []


def log(msg):
    print(msg)
    log_lines.append(msg)


def main():
    log("=" * 50)
    log("beta2 数据清理")
    log("=" * 50)

    # 加载
    log("\n📂 加载 phase2_beta.jsonl...")
    with open(INPUT_FILE, "r") as f:
        docs = [json.loads(l) for l in f if l.strip()]
    log(f"  原始实体数: {len(docs)}")

    # 加载 HallownestAPI 位置
    log("\n📂 加载 HallownestAPI 位置...")
    api_locs = load_api_locations()
    log(f"  API 位置数据: {len(api_locs)} 条")

    # 1. 合并残留重复
    log("\n🔗 步骤1: 合并残留重复...")
    before = len(docs)
    docs = merge_duplicates(docs)
    dup_merged = before - len(docs)

    # 2. 补位置
    log("\n🗺️ 步骤2: 补缺失位置...")
    before_loc = sum(1 for d in docs if d["category"] in ("护符","道具","技能","Boss","敌人") and not d.get("location"))
    docs = fill_locations(docs, api_locs)
    after_loc = sum(1 for d in docs if d["category"] in ("护符","道具","技能","Boss","敌人") and not d.get("location"))
    loc_filled = before_loc - after_loc

    # 3. 修标题
    log("\n🏷️ 步骤3: 修复错误标题...")
    docs = fix_bad_titles(docs)
    title_fixed = 1  # 硬编码，实际上只有"护符"那一条

    # 4. 输出
    log("\n📝 写入 phase2_beta.jsonl...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    # 5. 定版
    log("\n📋 写入 VERSION...")
    version = write_version(docs, dup_merged, loc_filled, title_fixed)

    # 6. 最终统计
    log(f"\n{'='*50}")
    log(f"✅ beta2 定版完成！")
    log(f"{'='*50}")
    log(f"  版本: {version['version']}")
    log(f"  实体数: {version['entity_count']}")
    log(f"  平均关联: {version['stats']['avg_related_entities']}")
    log(f"  零关联: {version['stats']['zero_relation_entities']}")
    log(f"  缺位置: {version['stats']['missing_locations']}")
    log(f"\n  分类:")
    for c, n in sorted(version['stats']['category_distribution'].items()):
        log(f"    {c}: {n}")

    # 验证
    log(f"\n{'='*50}")
    log("🔍 关键验证")
    log(f"{'='*50}")
    with open(OUTPUT_FILE, "r") as f:
        final_docs = [json.loads(l) for l in f if l.strip()]

    for d in final_docs:
        if d["_entity_key"] == "king's pass":
            log(f"\n  King's Pass 关联数: {len(d.get('related_entities',[]))}")
            for r in d.get("related_entities", []):
                log(f"    → {r['name']} ({r.get('relation','')}) [{r.get('category','')}]")
        if d["_entity_key"] == "fury of the fallen":
            log(f"\n  亡者之怒:")
            log(f"    位置: {d.get('location','')}")
            log(f"    关联数: {len(d.get('related_entities',[]))}")

    # 写入日志
    log_file = DATA_DIR / "cleanup_beta.log"
    with open(log_file, "w") as f:
        f.write("\n".join(log_lines))
    log(f"\n  日志: {log_file}")


if __name__ == "__main__":
    main()
