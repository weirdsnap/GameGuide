#!/usr/bin/env python3
"""
LLM 智能合并脚本 — 使用 DeepSeek 将 HallownestAPI + Wiki 数据融合为高质量知识文档。

流程：
  1. 解析 hallownest_knowledge.md（现有知识库）和 wiki_data.md（爬取的 Wiki 数据）
  2. 按 slug 匹配重叠条目 → 逐条调用 DeepSeek 智能合并
  3. 保留非重叠条目（API 独有保持原样，Wiki 独有按标准格式化）
  4. 输出统一 hallownest_knowledge.md

用法：
  python scripts/llm_merge.py                          # 全量合并
  python scripts/llm_merge.py --dry-run                 # 试跑（不调 LLM，只打印要处理的条目）
  python scripts/llm_merge.py --overlap-only --dry-run  # 只看重叠条目
  python scripts/llm_merge.py --resume                  # 断点续跑（跳过已合并的）
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple

try:
    from openai import OpenAI
except ImportError:
    print("❌ 请先安装 openai: pip install openai")
    sys.exit(1)


DATA_DIR = Path("/data/learning/agent/data")
CACHE_DIR = DATA_DIR / ".llm_merge_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HK_FILE = DATA_DIR / "hallownest_knowledge.md"
WIKI_FILE = DATA_DIR / "wiki_data.md"
OUTPUT_FILE = DATA_DIR / "hallownest_knowledge.md"

# ============== 类别 → 文档标题前缀 映射 ==============
CATEGORY_PREFIX = {
    "areas": "# 区域：",
    "bosses": "# Boss：",
    "characters": "# 角色：",
    "charms": "# 护符：",
    "skills": "# 技能/法术：",
    "quests": "# 任务：",
    "abilities": "# 能力：",
}

# ============== 解析工具 ==============


def parse_meta_value(line: str, prefix: str) -> str:
    if line.startswith(prefix):
        val = line[len(prefix):].strip()
        while val.startswith("："):
            val = val[1:]
        return val
    return ""


def split_docs(text: str) -> List[str]:
    """按 # 文档 切割为独立的文档块。"""
    chunks = re.split(r"(?=^#\s*文档)", text, flags=re.MULTILINE)
    return [c.strip() for c in chunks if c.strip()]


def parse_doc(chunk: str) -> Optional[Dict]:
    """解析一个文档块为 {name, category, slug, source, body}"""
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
        elif line.startswith("- 路径："):
            meta["source"] = parse_meta_value(line, "- 路径：")
        elif line.startswith("- 来源："):
            meta["source"] = parse_meta_value(line, "- 来源：")

    # 提取正文（跳过标题行和 metadata 行 + 来源标记行）
    body_lines = []
    for line in lines:
        stripped = line.strip()
        if any(stripped.startswith(p) for p in ("- 类别：", "- 标识：", "- 路径：", "- 来源：", "# 文档", "---", "*补充来源：")):
            continue
        if stripped:
            body_lines.append(line)
    body = "\n".join(body_lines).strip()

    # 去掉可能残留的补充来源标记段
    body = re.sub(r'\n?\*补充来源：Wiki\*\n?', '', body)

    if not body:
        return None

    return {"content": body, "metadata": meta}


def load_all_docs(file_path: Path) -> Dict[str, Dict]:
    """加载所有文档，返回 {slug: doc}"""
    if not file_path.exists():
        print(f"⚠️  文件不存在: {file_path}")
        return {}
    text = file_path.read_text(encoding="utf-8")
    result = {}
    for chunk in split_docs(text):
        doc = parse_doc(chunk)
        if doc and doc["metadata"]["slug"]:
            result[doc["metadata"]["slug"]] = doc
    return result


def format_merged_doc(idx: int, slug: str, doc: Dict) -> str:
    """将合并后的文档格式化为标准输出。"""
    name = doc["metadata"]["name"]
    category = doc["metadata"]["category"] or "general"
    body = doc["content"]

    # 去掉 body 开头的标题行（LLM 有时会自己加 # 区域 / # Boss 等）
    body = re.sub(
        r'^#\s*(区域|Boss|角色|护符|技能/法术|任务|能力)[：:].*?\n',
        '',
        body,
        count=1,
        flags=re.MULTILINE
    ).strip()

    prefix = CATEGORY_PREFIX.get(category, f"# {category.title()}：")
    new_title = f"{prefix}{name}"

    lines = [
        f"# 文档 [{idx}] {name}",
        f"- 类别：{category}",
        f"- 标识：{slug}",
        f"- 路径：merged",
        "",
        new_title,
        body,
        "---",
    ]
    return "\n".join(lines)


def format_preserved_doc(idx: int, doc: Dict) -> str:
    """保留原有文档格式。"""
    name = doc["metadata"]["name"]
    category = doc["metadata"]["category"] or "general"
    slug = doc["metadata"]["slug"]
    source = doc["metadata"]["source"]
    body = doc["content"]

    # 加上类别标题行（如果在 body 里没有的话）
    prefix = CATEGORY_PREFIX.get(category, f"# {category.title()}：")
    if not re.search(rf'^{re.escape(prefix)}', body, re.MULTILINE):
        # 看看原来有没有标题
        existing_title = re.search(r'^#\s+(.*?)：.*', body, re.MULTILINE)
        if not existing_title:
            body = f"{prefix}{name}\n" + body

    lines = [
        f"# 文档 [{idx}] {name}",
        f"- 类别：{category}",
        f"- 标识：{slug}",
        f"- 路径：{source}",
        "",
        body,
        "---",
    ]
    return "\n".join(lines)


# ============== LLM 合并 ==============


def generate_category_hint(category: str) -> str:
    """根据类别给出 LLM 的输出格式提示。"""
    hints = {
        "areas": """
组织方式建议：
- ## Overview — 区域总览，地理位置，总体氛围
- ## Points of Interest — 重要地点、Boss战、NPC
- ## Connections — 连接区域 + 需要的技能/能力
- ## Lore — 背景故事
- ## How to Access — 如何进入""",
        "bosses": """
组织方式建议：
- ## Overview — 基本描述
- ## Location — 位置
- ## Attack Patterns — 攻击方式（阶段列表）
- ## Strategy — 战斗策略
- ## Lore — 背景故事
- ## Rewards — 击败后获得的奖励""",
        "characters": """
组织方式建议：
- ## Overview — 角色介绍
- ## Location — 位置/出现区域
- ## Interactions — 与玩家的互动
- ## Lore — 背景故事
- ## Trivia — 趣闻""",
        "charms": """
组织方式建议：
- ## Overview — 护符基本描述
- ## Effect — 具体效果
- ## Location — 获取位置
- ## Notch Cost — 槽位消耗
- ## Synergies — 配合其他护符的效果
- ## Trivia — 趣闻""",
        "skills": """
组织方式建议：
- ## Overview — 技能基本描述
- ## How to Obtain — 获取方式
- ## Upgrades — 升级版本
- ## Usage — 使用技巧""",
        "quests": """
组织方式建议：
- ## Overview — 任务总览
- ## Starting Location — 触发位置
- ## Objectives — 任务目标
- ## Rewards — 奖励
- ## Lore — 背景故事""",
        "abilities": """
组织方式建议：
- ## Overview — 能力描述
- ## How to Obtain — 获取方式
- ## Function — 功能说明""",
    }
    return hints.get(category, "")


def build_merge_prompt(hk_doc: Dict, wiki_doc: Dict) -> str:
    """构建 LLM 合并提示词。"""
    name = hk_doc["metadata"]["name"]
    category = hk_doc["metadata"]["category"]
    hk_body = hk_doc["content"]
    wiki_body = wiki_doc["content"]
    hint = generate_category_hint(category)

    prompt = f"""You are a Hollow Knight wiki editor. Merge the following two sources about **{name}** into ONE clean, comprehensive document.

## Rules:
- Keep ALL useful information from both sources — do NOT drop details, numbers, names, or locations
- Remove duplicate content
- Reorganize logically using sections{hint}
- Clean up HTML tags (convert <b> to **, <i> to *)
- Convert wiki table syntax (|key = value|) to readable text
- Write in clear English, game-guide style
- If the two sources contradict each other, note both versions with "（注：另一来源称...)"
- Be faithful to the original data — do NOT fabricate information

## Source 1 — HallownestAPI (structured game data):
{hk_body}

## Source 2 — Hollow Knight Wiki:
{wiki_body}

## Output format:
Start directly with the content. No preamble. Use markdown sections with ## headings. Make sure ALL original details (numbers, locations, names) are preserved.
IMPORTANT: Output ONLY the document body. No "Here is the merged document" type text."""
    return prompt


def merge_with_llm(hk_doc: Dict, wiki_doc: Dict, slug: str, client: OpenAI, model: str = "deepseek-chat") -> Optional[str]:
    """调用 DeepSeek 合并一条文档，返回合并后的正文内容。"""
    # 检查缓存
    cache_file = CACHE_DIR / f"{slug}.txt"
    if cache_file.exists():
        cached = cache_file.read_text(encoding="utf-8")
        if cached.strip():
            print(f"  📦 命中缓存: {slug}")
            return cached.strip()

    prompt = build_merge_prompt(hk_doc, wiki_doc)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=3000,
            )
            merged = resp.choices[0].message.content.strip()
            if not merged:
                print(f"  ⚠️  {slug}: 返回为空，重试中...")
                continue
            # 写入缓存
            cache_file.write_text(merged, encoding="utf-8")
            return merged
        except Exception as e:
            print(f"  ⚠️  {slug}: API 错误: {e}")
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"    等待 {wait}s 后重试...")
                time.sleep(wait)
            else:
                print(f"  ❌ {slug}: 重试耗尽，放弃")
                return None
    return None


# ============== 主逻辑 ==============


def main():
    parser = argparse.ArgumentParser(description="LLM 智能合并知识库")
    parser.add_argument("--dry-run", action="store_true", help="试跑模式（不调 LLM）")
    parser.add_argument("--overlap-only", action="store_true", help="只处理重叠条目")
    parser.add_argument("--resume", action="store_true", help="断点续跑（跳过缓存已存在的）")
    parser.add_argument("--model", default="deepseek-chat", help="DeepSeek 模型名")
    parser.add_argument("--batch-size", type=int, default=3, help="批处理大小（连续调用间加延迟）")
    args = parser.parse_args()

    print("=" * 60)
    print("🧠 LLM 智能合并 — Hollow Knight 知识库")
    print("=" * 60)

    # 1. 加载数据
    print("\n📖 加载 HallownestAPI 数据...")
    hk_docs = load_all_docs(HK_FILE)
    print(f"   → {len(hk_docs)} 篇")

    print("📖 加载 Wiki 数据...")
    wiki_docs = load_all_docs(WIKI_FILE)
    print(f"   → {len(wiki_docs)} 篇")

    # 2. 分类
    hk_slugs = set(hk_docs.keys())
    wiki_slugs = set(wiki_docs.keys())
    overlap = sorted(hk_slugs & wiki_slugs)
    only_hk = hk_slugs - wiki_slugs
    only_wiki = wiki_slugs - hk_slugs

    print(f"\n📊 分布统计：")
    print(f"  重叠（LLM 合并）：{len(overlap)} 个")
    print(f"  仅 Hallownest：{len(only_hk)} 个")
    print(f"  仅 Wiki：{len(only_wiki)} 个")

    if args.dry_run:
        print(f"\n🔍 试跑模式 — 将处理的条目：")
        if args.overlap_only:
            print(f"\n  重叠条目（共 {len(overlap)} 个）：")
            for s in overlap:
                h = hk_docs[s]
                print(f"    [{h['metadata']['category']:>12}] {h['metadata']['name']} ({s})")
        else:
            print(f"\n  重叠 → LLM 合并：")
            for s in overlap:
                h = hk_docs[s]
                print(f"    ✅ [{h['metadata']['category']:>12}] {h['metadata']['name']}")
            if only_wiki:
                print(f"\n  Wiki 独有 → 格式化为标准文档：")
                for s in sorted(only_wiki):
                    w = wiki_docs[s]
                    print(f"    ➕ [{w['metadata']['category']:>12}] {w['metadata']['name']}")
            print(f"\n  仅 Hallownest → 保持原样：{len(only_hk)} 篇")
        print("\n✅ 试跑完成。去掉 --dry-run 执行实际合并。")
        return

    # 3. 初始化 DeepSeek 客户端
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("\n❌ 未设置 DEEPSEEK_API_KEY 环境变量")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")

    # 4. 合并重叠条目
    print(f"\n🔄 开始 LLM 合并（共 {len(overlap)} 个重叠条目）...")
    merged_docs = {}  # slug -> doc

    for i, slug in enumerate(overlap, 1):
        hk_doc = hk_docs[slug]
        wiki_doc = wiki_docs[slug]
        name = hk_doc["metadata"]["name"]

        if args.resume and (CACHE_DIR / f"{slug}.txt").exists():
            print(f"\n  [{i}/{len(overlap)}] ⏭️  {name} — 跳过（缓存存在）")
            cached = (CACHE_DIR / f"{slug}.txt").read_text(encoding="utf-8").strip()
            merged_docs[slug] = {
                "content": cached,
                "metadata": hk_doc["metadata"].copy(),
            }
            continue

        print(f"\n  [{i}/{len(overlap)}] 🔄 {name}（{slug}）...", end=" ")
        merged_content = merge_with_llm(hk_doc, wiki_doc, slug, client, args.model)
        if merged_content:
            merged_docs[slug] = {
                "content": merged_content,
                "metadata": hk_doc["metadata"].copy(),
            }
            print(f"✅ 合并完成（{len(merged_content)} 字符）")
        else:
            print(f"❌ 合并失败，保留原 Hallownest 版本")
            merged_docs[slug] = hk_doc.copy()

        # 批处理间隔，避免限流
        if i % args.batch_size == 0 and i < len(overlap):
            print(f"    ⏳ 批处理暂停 1 秒...")
            time.sleep(1)

    print(f"\n✅ LLM 合并完成：成功 {len([s for s in merged_docs if merged_docs[s].get('content')])}/{len(overlap)}")

    # 5. 组合输出
    print("\n📝 组合最终知识库...")
    output_docs = []

    # 5a. LLM 合并的条目
    for slug in overlap:
        output_docs.append(merged_docs[slug])

    # 5b. 仅 HallownestAPI 的条目（保持原样）
    for slug in sorted(only_hk):
        output_docs.append(hk_docs[slug])

    # 5c. 仅 Wiki 的条目（用标准格式整理）
    if only_wiki:
        print(f"\n📝 格式化 {len(only_wiki)} 个 Wiki 独有条目...")
        for slug in sorted(only_wiki):
            w = wiki_docs[slug]
            # 简单清理 HTML 和 wiki 语法
            body = w["content"]
            body = re.sub(r'<b>(.*?)</b>', r'**\1**', body)
            body = re.sub(r'<i>(.*?)</i>', r'*\1*', body)
            body = re.sub(r'<br\s*/?>', '\n', body)
            body = re.sub(r'<[^>]+>', '', body)
            body = re.sub(r'\|(.+?)=(.+?)\|', r'\1: \2', body)
            body = re.sub(r'\[\[([^|]+?)\]\]', r'\1', body)
            body = re.sub(r'\[\[[^]]+?\|([^]]+?)\]\]', r'\1', body)
            body = body.strip()

            output_docs.append({
                "content": body,
                "metadata": w["metadata"].copy(),
            })

    # 6. 写出最终文件
    output_docs.sort(key=lambda d: (
        {"areas": 0, "bosses": 1, "characters": 2, "charms": 3, "skills": 4, "quests": 5, "abilities": 6}.get(
            d["metadata"]["category"], 9
        ),
        d["metadata"]["name"].lower(),
    ))

    all_lines = []
    for i, doc in enumerate(output_docs, 1):
        slug = doc["metadata"]["slug"]
        source = doc["metadata"].get("source", "")
        is_merged = slug in overlap
        if is_merged:
            all_lines.append(format_merged_doc(i, slug, doc))
        else:
            all_lines.append(format_preserved_doc(i, doc))

    output_text = "\n\n".join(all_lines) + "\n"
    OUTPUT_FILE.write_text(output_text, encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"✅ 完成！已写入：{OUTPUT_FILE}")
    print(f"  文档总数：{len(output_docs)} 篇")
    print(f"  文件大小：{len(output_text.encode('utf-8')) / 1024:.0f} KB")
    print(f"  LLM 合并条目：{len(overlap)}")
    print(f"  保留 HallownestAPI 条目：{len(only_hk)}")
    print(f"  新增 Wiki 独有条目：{len(only_wiki)}")
    print(f"\n💡 缓存目录：{CACHE_DIR}/ （断点续跑用 --resume）")
    print(f"💡 下一步：运行 validate_merge.py 进行校验")


if __name__ == "__main__":
    main()
