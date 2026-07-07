#!/usr/bin/env python3
"""
知识库合并校验脚本 — 检查 LLM 合并结果的质量。

检查项：
  1. 🔍 实体保留检查 — 原数据的关键信息是否都出现在合并输出中
  2. 🚫 无幻觉检查 — 合并输出的专有名词是否都能在源数据中找到
  3. 📐 格式检查 — 元字段完整、文档分隔规范
  4. 📊 统计汇总

用法：
  python scripts/validate_merge.py                          # 校验所有 LLM 合并条目
  python scripts/validate_merge.py --slug greenpath          # 校验特定条目
  python scripts/validate_merge.py --summary-only            # 只输出汇总
  python scripts/validate_merge.py --detail                  # 输出详细检查结果
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DATA_DIR = Path("/data/learning/agent/data")
CACHE_DIR = DATA_DIR / ".llm_merge_cache"

HK_FILE = DATA_DIR / "hallownest_knowledge.md"
WIKI_FILE = DATA_DIR / "wiki_data.md"
# 如果合并输出是新的内容，默认校验缓存文件；也可用 --input 指定
DEFAULT_INPUT = DATA_DIR / "hallownest_knowledge.md"


# ============== 解析工具 ==============


def parse_meta_value(line: str, prefix: str) -> str:
    if line.startswith(prefix):
        val = line[len(prefix):].strip()
        while val.startswith("："):
            val = val[1:]
        return val
    return ""


def split_docs(text: str) -> List[str]:
    chunks = re.split(r"(?=^#\s*文档)", text, flags=re.MULTILINE)
    return [c.strip() for c in chunks if c.strip()]


def parse_doc(chunk: str) -> Optional[Dict]:
    lines = chunk.split("\n")
    title_m = re.search(r"^#\s*文档[^a-zA-Z]*\s*(.*)", lines[0])
    if not title_m:
        return None
    name = title_m.group(1).strip()

    meta = {"name": name, "category": "", "slug": "", "source": ""}
    for line in lines[1:]:
        if line.startswith("- 类别："):
            meta["category"] = parse_meta_value(line, "- 类别：")
        elif line.startswith("- 标识："):
            meta["slug"] = parse_meta_value(line, "- 标识：")

    body = "\n".join(lines).strip()
    return {"content": body, "metadata": meta}


def load_docs(file_path: Path) -> Dict[str, Dict]:
    if not file_path.exists():
        return {}
    text = file_path.read_text(encoding="utf-8")
    result = {}
    for chunk in split_docs(text):
        doc = parse_doc(chunk)
        if doc and doc["metadata"]["slug"]:
            result[doc["metadata"]["slug"]] = doc
    return result


# ============== 校验逻辑 ==============


def extract_key_entities(text: str) -> Dict[str, set]:
    """从文本中提取关键实体：数字、大写的专有名词、Boss名、地名等。"""
    # 数字（如 0.6, 4, 3等）
    numbers = set()
    for m in re.finditer(r'(?<![.\w])(\d+(?:\.\d+)?)(?![.\w%])', text):
        numbers.add(m.group(1))

    # 大写开头的专有名词（可能是地名、人名、物品名）
    proper_nouns = set()
    for m in re.finditer(r'(?<![-\w])[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*', text):
        word = m.group(0).strip()
        # 过滤掉句子开头的普通词、月份等常见非实体
        if word.lower() in {"the", "a", "an", "this", "that", "these", "those",
                            "it", "they", "he", "she", "we", "you", "i",
                            "and", "or", "but", "for", "nor", "yet", "so",
                            "is", "was", "are", "were", "be", "been", "being",
                            "have", "has", "had", "do", "does", "did",
                            "will", "would", "can", "could", "shall", "should",
                            "may", "might", "must", "need", "one", "two",
                            "three", "four", "five", "six", "seven", "eight",
                            "nine", "ten", "each", "all", "both", "some",
                            "any", "many", "much", "more", "most", "few",
                            "such", "no", "not", "only", "same", "very",
                            "just", "also", "well", "even", "still", "often",
                            "then", "now", "here", "there", "when", "where",
                            "how", "what", "why", "who", "which",
                            "when", "where", "while", "although", "because",
                            "since", "if", "whether", "though", "after",
                            "before", "above", "below", "over", "under",
                            "another", "every", "enough", "own", "else",
                            "than", "too", "almost", "quite", "first",
                            "second", "third", "note", "approximately", "via",
                            "january", "february", "march", "april", "june",
                            "july", "august", "september", "october", "november",
                            "december", "once", "upon", "inside", "outside",
                            "north", "south", "east", "west",
        }:
            continue
        # 过短的词忽略
        if len(word) < 3:
            continue
        proper_nouns.add(word)

    return {"numbers": numbers, "proper_nouns": proper_nouns}


def validate_entry(slug: str, hk_source: Dict, wiki_source: Dict, merged: Dict) -> Dict:
    """校验单个合并条目。"""
    hk_text = hk_source["content"]
    wiki_text = wiki_source["content"]
    merged_text = merged["content"]

    # 提取各来源的实体
    hk_entities = extract_key_entities(hk_text)
    wiki_entities = extract_key_entities(wiki_text)
    merged_entities = extract_key_entities(merged_text)

    # --- 1. 实体保留检查 ---
    missing_from_hk = {}
    for typ, label in [("numbers", "数字"), ("proper_nouns", "专有名词")]:
        in_hk = hk_entities[typ]
        in_merged = merged_entities[typ]
        missing = in_hk - in_merged
        if missing:
            # 只保留有价值的实体（过短的忽略、数字类的也保留）
            missing = {m for m in missing if len(str(m)) >= 1}
        missing_from_hk[typ] = missing

    missing_from_wiki = {}
    for typ, label in [("numbers", "数字"), ("proper_nouns", "专有名词")]:
        in_wiki = wiki_entities[typ]
        in_merged = merged_entities[typ]
        missing = in_wiki - in_merged
        missing_from_wiki[typ] = missing

    # --- 2. 无幻觉检查 ---
    all_source_entities = set()
    all_source_entities.update(hk_entities["numbers"])
    all_source_entities.update(wiki_entities["numbers"])
    all_source_entities.update(hk_entities["proper_nouns"])
    all_source_entities.update(wiki_entities["proper_nouns"])

    merged_unique = merged_entities["proper_nouns"] - all_source_entities
    # 过滤常见的非幻觉（markdown 标题词、通用词）
    common_words = {"Overview", "Location", "Effect", "Strategy", "Tips",
                    "Notes", "Trivia", "Lore", "Rewards", "Location",
                    "Attack", "Patterns", "How", "Access", "Points",
                    "Interest", "Connections", "Function", "Usage",
                    "Synergies", "Obtain", "Upgrades", "Objectives",
                    "Starting", "Interactions", "Details", "Description"}
    suspected_hallucinations = merged_unique - common_words
    # 过滤掉和名字相关的词
    name_words = set(hk_source["metadata"]["name"].split())
    suspected_hallucinations -= name_words

    # --- 3. 格式检查 ---
    format_issues = []
    meta_text = merged["content"]
    if "- 类别：" not in meta_text[:500]:
        format_issues.append("缺少 - 类别： 字段")
    if "- 标识：" not in meta_text[:500]:
        format_issues.append("缺少 - 标识： 字段")
    if not re.search(r'^---\s*$', meta_text, re.MULTILINE):
        format_issues.append("缺少 --- 文档结束标记")
    # 检查是否有 HTML 残留
    if re.search(r'<[a-z]+>', meta_text, re.IGNORECASE):
        format_issues.append("残留 HTML 标签")
    if re.search(r'\|[a-z_]+\s*=', meta_text, re.IGNORECASE):
        format_issues.append("残留 Wiki 表格语法")

    # --- 4. 长度检查 ---
    hk_len = len(hk_text)
    wiki_len = len(wiki_text)
    merged_len = len(merged_text)
    # 合并后不应比最短源还短太多（丢失太多内容）
    min_source = min(hk_len, wiki_len)
    if merged_len < min_source * 0.5:
        format_issues.append(f"合并后长度({merged_len})远小于源数据({min_source})，内容可能丢失")

    # 检查是否过于冗长（超过两倍总长）
    total_source = hk_len + wiki_len
    if merged_len > total_source * 1.5:
        format_issues.append(f"合并后长度({merged_len})远超过源数据总和({total_source})，可能有冗余")

    return {
        "name": hk_source["metadata"]["name"],
        "slug": slug,
        "hk_len": hk_len,
        "wiki_len": wiki_len,
        "merged_len": merged_len,
        "missing_numbers_hk": missing_from_hk.get("numbers", set()),
        "missing_nouns_hk": missing_from_hk.get("proper_nouns", set()),
        "missing_numbers_wiki": missing_from_wiki.get("numbers", set()),
        "missing_nouns_wiki": missing_from_wiki.get("proper_nouns", set()),
        "suspected_hallucinations": suspected_hallucinations,
        "format_issues": format_issues,
    }


def print_validation_result(result: Dict, detail: bool = False):
    """打印单个条目的校验结果。"""
    name = result["name"]
    slug = result["slug"]

    issues = []
    # 实体丢失
    total_missing = (
        len(result["missing_numbers_hk"]) +
        len(result["missing_nouns_hk"]) +
        len(result["missing_numbers_wiki"]) +
        len(result["missing_nouns_wiki"])
    )
    if total_missing > 0:
        issues.append(f"实体丢失({total_missing})")
    if result["suspected_hallucinations"]:
        issues.append(f"可疑幻觉({len(result['suspected_hallucinations'])})")
    if result["format_issues"]:
        issues.append(f"格式问题({len(result['format_issues'])})")

    status = "✅" if not issues else "⚠️"
    issue_desc = ", ".join(issues) if issues else "完好"

    print(f"  {status} {name:25s} [{result['merged_len']:4d}字] {issue_desc}")

    if detail and issues:
        if result["missing_nouns_hk"]:
            print(f"      HallownestAPI 专有名词丢失: {result['missing_nouns_hk']}")
        if result["missing_nouns_wiki"]:
            print(f"      Wiki 专有名词丢失: {result['missing_nouns_wiki']}")
        if result["missing_numbers_hk"]:
            print(f"      HallownestAPI 数字丢失: {result['missing_numbers_hk']}")
        if result["suspected_hallucinations"]:
            print(f"      可疑幻觉: {result['suspected_hallucinations']}")
        if result["format_issues"]:
            print(f"      格式问题: {result['format_issues']}")


# ============== 主入口 ==============


def main():
    parser = argparse.ArgumentParser(description="校验 LLM 合并结果")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="待校验的文件路径")
    parser.add_argument("--slug", default=None, help="校验特定条目")
    parser.add_argument("--detail", action="store_true", help="输出详细检查结果")
    parser.add_argument("--summary-only", action="store_true", help="只输出汇总")
    parser.add_argument("--auto-fix", action="store_true", help="自动修复格式问题（实验性）")
    args = parser.parse_args()

    input_path = Path(args.input)

    print("=" * 60)
    print("🧐 LLM 合并结果校验")
    print("=" * 60)

    # 加载数据
    hk_docs = load_docs(HK_FILE)
    wiki_docs = load_docs(WIKI_FILE)
    merged_docs = load_docs(input_path)

    # 确定要校验的 slug 列表（只校验有缓存的 LLM 合并条目）
    cached_slugs = set()
    if CACHE_DIR.exists():
        cached_slugs = {f.stem for f in CACHE_DIR.glob("*.txt") if f.is_file()}

    # 只校验缓存中存在的（即 LLM 合并过的）条目
    if args.slug:
        slugs_to_check = [args.slug]
    else:
        slugs_to_check = sorted(
            cached_slugs & set(hk_docs.keys()) & set(wiki_docs.keys()) & set(merged_docs.keys())
        )

    if not slugs_to_check:
        print("\n⚠️  没有找到待校验的条目。")
        print("   请先运行 llm_merge.py 生成合并结果。")
        print(f"   缓存目录: {CACHE_DIR}")
        return

    print(f"\n📊 待校验条目：{len(slugs_to_check)} 个")

    # 逐条校验
    print()
    results = []
    for slug in slugs_to_check:
        hk_source = hk_docs[slug]
        wiki_source = wiki_docs[slug]
        merged = merged_docs.get(slug)

        # 如果 merged 不在当前文件中，从缓存读取
        if not merged:
            cache_file = CACHE_DIR / f"{slug}.txt"
            if cache_file.exists():
                content = cache_file.read_text(encoding="utf-8").strip()
                merged = {"content": content, "metadata": hk_source["metadata"].copy()}
            else:
                print(f"  ⚠️  {slug}: 合并结果不存在，跳过")
                continue

        result = validate_entry(slug, hk_source, wiki_source, merged)
        results.append(result)

        if not args.summary_only:
            print_validation_result(result, args.detail)

    # 汇总
    print(f"\n{'=' * 60}")
    print("📊 校验汇总")
    print(f"{'=' * 60}")

    total = len(results)
    passed = sum(1 for r in results if not (
        r["missing_numbers_hk"] or r["missing_nouns_hk"] or
        r["missing_numbers_wiki"] or r["missing_nouns_wiki"] or
        r["suspected_hallucinations"] or r["format_issues"]
    ))
    has_issues = total - passed

    total_noun_loss = sum(len(r["missing_nouns_hk"]) + len(r["missing_nouns_wiki"]) for r in results)
    total_num_loss = sum(len(r["missing_numbers_hk"]) + len(r["missing_numbers_wiki"]) for r in results)
    total_hallucinations = sum(len(r["suspected_hallucinations"]) for r in results)
    total_format_issues = sum(len(r["format_issues"]) for r in results)

    avg_hk_len = sum(r["hk_len"] for r in results) / total if total else 0
    avg_wiki_len = sum(r["wiki_len"] for r in results) / total if total else 0
    avg_merged_len = sum(r["merged_len"] for r in results) / total if total else 0

    print(f"  校验条目：{total}")
    print(f"  完全通过：{passed}")
    print(f"  存在问题：{has_issues}")
    print(f"  专有名词丢失：共 {total_noun_loss} 处")
    print(f"  数字信息丢失：共 {total_num_loss} 处")
    print(f"  可疑幻觉：共 {total_hallucinations} 处")
    print(f"  格式问题：共 {total_format_issues} 处")
    print(f"\n  平均长度：HK={avg_hk_len:.0f} → Wiki={avg_wiki_len:.0f} → 合并={avg_merged_len:.0f}")

    # 详细列出有问题的条目
    problematic = [r for r in results if (
        r["missing_numbers_hk"] or r["missing_nouns_hk"] or
        r["missing_numbers_wiki"] or r["missing_nouns_wiki"] or
        r["suspected_hallucinations"] or r["format_issues"]
    )]
    if problematic:
        print(f"\n⚠️  待检查的条目（{len(problematic)} 个）：")
        for r in problematic:
            issues = []
            if r["missing_nouns_hk"]:
                issues.append(f"HK丢失: {r['missing_nouns_hk']}")
            if r["missing_nouns_wiki"]:
                issues.append(f"Wiki丢失: {r['missing_nouns_wiki']}")
            if r["missing_numbers_hk"] or r["missing_numbers_wiki"]:
                nums = r["missing_numbers_hk"] | r["missing_numbers_wiki"]
                issues.append(f"数字丢失: {nums}")
            if r["suspected_hallucinations"]:
                issues.append(f"可疑幻觉: {r['suspected_hallucinations']}")
            if r["format_issues"]:
                issues.append(f"格式: {r['format_issues']}")
            print(f"  • {r['name']:25s} — {'; '.join(issues)}")

    print(f"\n💡 查看详情: python scripts/validate_merge.py --slug <name> --detail")
    print(f"💡 查看缓存: ls {CACHE_DIR}/")


if __name__ == "__main__":
    main()
