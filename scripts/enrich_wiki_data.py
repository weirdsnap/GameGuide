#!/usr/bin/env python3
"""
补充 Wiki 数据：为指定游戏抓取新分类的页面，追加到已有的 wiki_data.md。

用法：
  python scripts/enrich_wiki_data.py --game oni
  python scripts/enrich_wiki_data.py --game terraria
  python scripts/enrich_wiki_data.py --game silksong
  python scripts/enrich_wiki_data.py --game all
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests

# ===== 配置 =====
BASE_DIR = Path("/data/learning/agent")
GAMES_DIR = BASE_DIR / "games"

API_URLS = {
    "oni": "https://oxygennotincluded.fandom.com/api.php",
    "terraria": "https://terraria.fandom.com/api.php",
    "silksong": "https://hollowknight.fandom.com/api.php",
}

# 每个游戏需要补充的分类
NEW_CATEGORIES = {
    "oni": [
        "Automation_Buildings",
        "Automation_Gates",
        "Automation_Sensors",
        "Plumbing_Buildings",
        "Ventilation_Buildings",
        "Rocketry_Buildings",
        "Rocketry_Buildings_(Spaced_Out)",
        "Power_Buildings",
        "Wires",
    ],
    "terraria": [
        "Crafting_station_items",
        "Buffs",
        "Debuffs",
        "Boss_summon_items",
        "Dye_items",
        "Vanity_items",
    ],
    "silksong": [
        "Items_(Silksong)",
        "NPCs_(Silksong)",
    ],
}


def fetch_category_pages(api_url: str, category: str) -> List[str]:
    """获取分类下所有主命名空间页面标题。"""
    pages = []
    cmcont = None
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmlimit": "max",
            "format": "json",
        }
        if cmcont:
            params["cmcontinue"] = cmcont
        try:
            r = requests.get(api_url, params=params, timeout=15)
            data = r.json()
        except Exception as e:
            print(f"  ⚠️  请求失败: {e}")
            time.sleep(3)
            continue

        for member in data.get("query", {}).get("categorymembers", []):
            if member.get("ns") == 0:
                pages.append(member["title"])

        cont = data.get("continue", {})
        cmcont = cont.get("cmcontinue")
        if not cmcont:
            break
        time.sleep(0.3)
    return pages


def clean_wikitext(raw: str) -> Optional[str]:
    """将原始 wikitext 清洗为纯文本。"""
    text = raw

    # 移除 infobox 模板（从 {{ 到匹配的 }}）
    # 按常见 infobox 名称匹配
    infobox_patterns = [
        r'(?i)\{\{item\s+infobox.*?(?:\{\{.*?\}\}.)*?\}\}',
        r'(?i)\{\{infobox\s+\w+.*?(?:\{\{.*?\}\}.)*?\}\}',
        r'(?i)\{\{infobox.*?(?:\{\{.*?\}\}.)*?\}\}',
        r'(?i)\{\{buff\s+infobox.*?(?:\{\{.*?\}\}.)*?\}\}',
    ]
    for pat in infobox_patterns:
        text = re.sub(pat, '', text, flags=re.DOTALL)

    # 通用模板移除（简单非嵌套模板）
    # 嵌套模板需要递归移除
    while '{{' in text and '}}' in text:
        # 移除最内层的模板
        new_text = re.sub(r'\{\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\}', '', text)
        if new_text == text:
            break
        text = new_text

    # 移除分类和语言链接
    text = re.sub(r'\[\[Category:[^\]]*\]\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[\[[a-z]+:[^\]]*\]\]', '', text)

    # 移除 __TOC__, __NOTOC__ 等
    text = re.sub(r'__[A-Z_]+__', '', text)

    # 转换 wiki 链接 [[Page|text]] → text, [[Page]] → Page
    text = re.sub(r'\[\[([^\]|]*)\|([^\]]*)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]*)\]\]', r'\1', text)

    # 移除粗斜体标记
    text = re.sub(r"'''(.*?)'''", r'\1', text)
    text = re.sub(r"''(.*?)''", r'\1', text)

    # 移除 <ref> 标签
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^>]*/>', '', text)

    # 移除 <nowiki>、<noinclude>、<includeonly> 等
    for tag in ['nowiki', 'noinclude', 'includeonly', 'onlyinclude']:
        text = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', text, flags=re.DOTALL)

    # 移除注释
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # 移除文件/图片引用
    text = re.sub(r'\[\[(?:File|Image|Media)[^\]]*\]\]', '', text, flags=re.IGNORECASE)

    # 清理空白
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = text.strip()

    if len(text) < 50:
        return None

    return text


def fetch_page_wikitext(api_url: str, title: str) -> Optional[str]:
    """通过 parse API 获取页面 wikitext。"""
    params = {
        "action": "parse",
        "page": title,
        "prop": "wikitext",
        "format": "json",
        "redirects": 1,
        "disablelimitreport": 1,
    }
    try:
        r = requests.get(api_url, params=params, timeout=15)
        data = r.json()
    except Exception as e:
        print(f"  ⚠️  请求失败: {e}")
        return None

    parse_data = data.get("parse", {})
    # 检查重定向
    redirects = parse_data.get("redirects", [])
    if redirects:
        actual_title = redirects[0].get("to", "")
        if actual_title:
            print(f"  ↪ 重定向: {parse_data.get('title', title)} → {actual_title}")
            # 重新请求实际页面
            params["page"] = actual_title
            try:
                r = requests.get(api_url, params=params, timeout=15)
                data = r.json()
                parse_data = data.get("parse", {})
            except:
                pass

    wikitext = parse_data.get("wikitext", {}).get("*", "")
    if not wikitext:
        return None

    return clean_wikitext(wikitext)


def load_existing_titles(wiki_path: Path) -> Set[str]:
    """读取现有 wiki_data.md，返回所有已存在的页面标题集合。"""
    if not wiki_path.exists():
        return set()
    text = wiki_path.read_text(encoding="utf-8")
    titles = set()
    for match in re.finditer(r"^#\s*文档[：:]\s*(.+)", text, re.MULTILINE):
        titles.add(match.group(1).strip())
    return titles


def format_doc(title: str, content: str, category: str) -> str:
    """格式化为标准文档块。"""
    slug = title.lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        slug = "unknown"

    lines = [
        f"# 文档：{title}",
        f"- 类别：{category}",
        f"- 标识：{slug}",
        f"- 来源：wiki/{title.replace(' ', '_')}",
        "",
        content,
    ]
    return "\n".join(lines)


def update_doc_count(wiki_path: Path, new_count: int):
    """更新文档头部中的总文档数。"""
    text = wiki_path.read_text(encoding="utf-8")
    text = re.sub(
        r"(文档总数：)(\d+)",
        lambda m: f"{m.group(1)}{new_count}",
        text,
    )
    wiki_path.write_text(text, encoding="utf-8")


def ensure_section_block(wiki_path: Path, section_name: str, section_count: int):
    """确保文件末尾有分类区块标题。"""
    text = wiki_path.read_text(encoding="utf-8")
    header = f"## {section_name}"
    if header in text:
        return

    with open(wiki_path, "a", encoding="utf-8") as f:
        f.write(f"\n## {section_name}\n共 {section_count} 篇文档\n")


def enrich_game(game: str):
    """为指定游戏补充 wiki 数据。"""
    game_dir = GAMES_DIR / game
    wiki_path = game_dir / "data" / "wiki_data.md"

    if not wiki_path.exists():
        print(f"❌ {game}: 未找到 wiki_data.md")
        return

    api_url = API_URLS.get(game)
    categories = NEW_CATEGORIES.get(game, [])
    if not categories:
        print(f"  {game}: 无可补充的分类")
        return

    existing_titles = load_existing_titles(wiki_path)
    print(f"\n{'='*50}")
    print(f"📦 {game.upper()} — 现有 {len(existing_titles)} 篇")

    total_fetched = 0
    total_added = 0
    all_new_docs: List[str] = []
    section_counts: Dict[str, int] = {}

    for cat in categories:
        print(f"\n  📂 {cat}", end="", flush=True)
        pages = fetch_category_pages(api_url, cat)
        real_pages = [p for p in pages if not p.startswith("Category:")]
        print(f"  ({len(real_pages)} 页)")

        if not real_pages:
            continue

        added_in_cat = 0

        for page_title in real_pages:
            # 去重
            if page_title in existing_titles:
                continue

            slug = page_title.lower().replace(" ", "-")
            slug = re.sub(r"[^a-z0-9\-]", "", slug)
            if not slug:
                continue

            print(f"    ⬇️  {page_title} ...", end="", flush=True)
            content = fetch_page_wikitext(api_url, page_title)
            if not content:
                print(" ⏭️")
                continue

            if len(content) > 8000:
                content = content[:8000] + "\n\n...(内容截断)"

            doc = format_doc(page_title, content, cat.lower())
            all_new_docs.append(doc)
            existing_titles.add(page_title)
            added_in_cat += 1
            total_added += 1
            print(f" {len(content):,} 字符 ✅")

            time.sleep(0.5)  # 礼貌间隔

        if added_in_cat > 0:
            section_counts[cat.upper()] = added_in_cat

        total_fetched += len(real_pages)
        time.sleep(1)

    if total_added > 0:
        # 先追加分类区块标题
        for section, count in section_counts.items():
            ensure_section_block(wiki_path, section, count)

        # 追加文档
        with open(wiki_path, "a", encoding="utf-8") as f:
            f.write("\n".join(all_new_docs) + "\n")

        # 更新文档总数
        update_doc_count(wiki_path, len(existing_titles))
        print(f"\n  ✅ {game}: +{total_added} 篇 | 总计: {len(existing_titles)} 篇")
    else:
        print(f"\n  ✅ {game}: 无新增（数据已完整）")


def deduplicate_wiki_file(wiki_path: Path, game_name: str = ""):
    """按标题去重，保留首次出现。"""
    if not wiki_path.exists():
        return

    text = wiki_path.read_text(encoding="utf-8")
    chunks = re.split(r"(?=^# 文档[：:])", text, flags=re.MULTILINE)

    header = chunks[0] if not chunks[0].startswith("# 文档") else ""
    doc_chunks = [c for c in chunks if c.startswith("# 文档")]

    seen: Set[str] = set()
    kept: List[str] = []
    removed = 0

    for chunk in doc_chunks:
        m = re.search(r"^#\s*文档[：:]\s*(.+)", chunk.strip(), re.MULTILINE)
        if not m:
            kept.append(chunk)
            continue
        title = m.group(1).strip()
        if title in seen:
            removed += 1
            continue
        seen.add(title)
        kept.append(chunk)

    if removed > 0:
        output = header.rstrip() + "\n\n" + "\n".join(kept) + "\n"
        output = re.sub(r"(文档总数：)(\d+)", f"\\g<1>{len(kept)}", output)
        wiki_path.write_text(output, encoding="utf-8")
        print(f"    去重移除 {removed} 条 | 最终 {len(kept)} 篇")
    else:
        print(f"    无需去重")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="补充 Wiki 数据")
    parser.add_argument("--game", choices=["oni", "terraria", "silksong", "all"], default="all")
    args = parser.parse_args()

    games = ["oni", "terraria", "silksong"] if args.game == "all" else [args.game]

    for game in games:
        enrich_game(game)

    print(f"\n{'='*50}")
    print("🔄 去重检查")
    print(f"{'='*50}")
    for game in games:
        wiki_path = GAMES_DIR / game / "data" / "wiki_data.md"
        if wiki_path.exists():
            print(f"  {game}:")
            deduplicate_wiki_file(wiki_path, game)

    print(f"\n✅ 全部完成")


if __name__ == "__main__":
    main()
