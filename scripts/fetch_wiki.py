#!/usr/bin/env python3
"""
通用 Fandom Wiki 文本抓取工具。
从指定分类获取所有页面的纯文本内容，保存为 wiki_data.md。

用法：
  python scripts/fetch_wiki.py cyberpunk2077
  python scripts/fetch_wiki.py va11halla
  python scripts/fetch_wiki.py --all
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional

try:
    import requests
except ImportError:
    print("❌ 需要 requests 库：pip install requests")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAMES_DIR = PROJECT_ROOT / "games"

# ── Wiki Configs ──

WIKI_CONFIGS = {
    "cyberpunk2077": {
        "api": "https://cyberpunk.fandom.com/api.php",
        "user_agent": "GameGuideBot/2.0 (Cyberpunk 2077 wiki fetcher)",
        "output": GAMES_DIR / "cyberpunk2077" / "data" / "wiki_data.md",
        "categories": [
            "Cyberpunk_2077_Characters",
            "Cyberpunk_2077_Locations",
            "Cyberpunk_2077_Weapons",
            "Cyberpunk_2077_Cyberware",
            "Cyberpunk_2077_Vehicles",
            "Cyberpunk_2077_Main_Jobs",
            "Cyberpunk_2077_Side_Jobs",
            "Cyberpunk_2077_Gigs",
            "Cyberpunk_2077_Enemies",
            "Cyberpunk_2077_Perks",
            "Cyberpunk_2077_Quickhacks",
            "Cyberpunk_2077_Quest_Items",
            "Cyberpunk_2077_Consumables",
            "Cyberpunk_2077_DLC",
            "Cyberpunk_2077_Phantom_Liberty",
        ],
    },
    "va11halla": {
        "api": "https://va11halla.fandom.com/api.php",
        "user_agent": "GameGuideBot/2.0 (VA-11 Hall-A wiki fetcher)",
        "output": GAMES_DIR / "va11halla" / "data" / "wiki_data.md",
        "categories": [
            "Characters",
            "Drinks",
            "Ingredients",
            "Events",
            "Places",
            "Bars",
            "Organisations",
        ],
    },
}


def api_request(api_url: str, params: dict, ua: str) -> Optional[dict]:
    """发送 MediaWiki API 请求"""
    params["format"] = "json"
    try:
        resp = requests.get(
            api_url, params=params, headers={"User-Agent": ua}, timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  ⚠️ API 请求失败: {e}")
        return None


def get_category_members(
    api_url: str, category: str, ua: str, recursive: bool = True
) -> List[str]:
    """获取分类下的所有页面标题（递归获取子分类中的页面）"""
    titles = []
    seen = set()
    retries = 3

    # Collect all page titles from this category AND subcategories
    def _collect(cat: str, depth: int = 0):
        if depth > 5:  # safety limit
            return
        cmcontinue = None
        while True:
            params = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": f"Category:{cat}",
                "cmlimit": "max",
                "cmtype": "page|subcat",
            }
            if cmcontinue:
                params["cmcontinue"] = cmcontinue

            data = None
            for attempt in range(retries):
                data = api_request(api_url, params, ua)
                if data is not None:
                    break
                time.sleep(3)
            if data is None:
                break

            query = data.get("query", {})
            members = query.get("categorymembers", [])
            for m in members:
                title = m.get("title", "")
                ns = m.get("ns", 0)
                if ns == 0:  # Regular page namespace
                    if title and title not in seen:
                        titles.append(title)
                        seen.add(title)
                elif ns == 14 and recursive:  # Category → recurse
                    # Strip "Category:" prefix to get subcategory name
                    subcat = title[len("Category:"):] if title.startswith("Category:") else title
                    indent = "  " * (depth + 1)
                    sys.stdout.write(f"{indent}📁 {subcat}\n")
                    sys.stdout.flush()
                    _collect(subcat, depth + 1)

            cont = data.get("continue", {})
            cmcontinue = cont.get("cmcontinue")
            if not cmcontinue:
                break
            time.sleep(0.5)

    _collect(category)
    return titles


WIKITEXT_REMOVE = re.compile(
    r"'''|''|\[\[File:[^\]]+\]\]|\[\[Category:[^\]]+\]\]|<ref[^>]*/>|<ref>[^<]*</ref>"
)
WIKITEXT_LINK = re.compile(r"\[\[([^|\]]+)\|?([^\]]*)\]\]")


def _clean_wikitext(text: str) -> str:
    """粗略清洗 wikitext 为纯文本"""
    # Remove files, categories, refs
    text = WIKITEXT_REMOVE.sub("", text)
    # Convert links: [[Page]] or [[Page|label]]
    text = WIKITEXT_LINK.sub(lambda m: m.group(2) or m.group(1), text)
    # Remove templates (everything between {{ and }})
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    # Remove heading markers
    text = re.sub(r"^=+", "", text, flags=re.MULTILINE)
    text = re.sub(r"=+$", "", text, flags=re.MULTILINE)
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def fetch_page_content(
    api_url: str, title: str, ua: str
) -> Optional[str]:
    """获取单页的原始 wikitext 并清洗"""
    params = {
        "action": "query",
        "prop": "revisions",
        "rvprop": "content",
        "titles": title,
        "redirects": 1,
    }
    data = api_request(api_url, params, ua)
    if data is None:
        return None

    pages = data.get("query", {}).get("pages", {})
    for pid, page in pages.items():
        if pid == "-1":
            return None
        revs = page.get("revisions", [])
        if revs:
            content = revs[0].get("*", "")
            if content:
                return _clean_wikitext(content)
    return None


def fetch_batch_content(
    api_url: str, titles: List[str], ua: str, batch: int = 50
) -> Dict[str, str]:
    """批量获取页面内容（raw wikitext）"""
    result = {}
    for i in range(0, len(titles), batch):
        batch_titles = titles[i : i + batch]
        params = {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "titles": "|".join(batch_titles),
            "redirects": 1,
        }
        data = api_request(api_url, params, ua)
        if data is None:
            continue

        pages = data.get("query", {}).get("pages", {})
        for pid, page in pages.items():
            if pid == "-1":
                continue
            title = page.get("title", "")
            revs = page.get("revisions", [])
            if revs:
                content = revs[0].get("*", "")
                if content:
                    cleaned = _clean_wikitext(content)
                    if cleaned:
                        result[title] = cleaned

        time.sleep(1.5)  # Rate limiting
        sys.stdout.write(f"\r  📄 {min(i + batch, len(titles))}/{len(titles)}")
        sys.stdout.flush()

    return result


def build_wiki_data(config: dict) -> int:
    """为单个游戏构建 wiki_data.md，返回文章数"""
    print(f"\n🌐 Wiki: {config['api']}")
    output_path = config["output"]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_titles = set()
    for cat in config["categories"]:
        print(f"  📂 分类: {cat}")
        titles = get_category_members(config["api"], cat, config["user_agent"])
        print(f"    → {len(titles)} 页")
        all_titles.update(titles)
        time.sleep(0.5)

    print(f"\n📊 总页面数（去重）: {len(all_titles)}")
    if not all_titles:
        print("  ⚠️ 没有页面，跳过")
        return 0

    # Fetch content
    print("📥 获取内容（批量 50 条）...")
    content_map = fetch_batch_content(config["api"], list(all_titles), config["user_agent"])
    print(f"\n✅ 成功获取: {len(content_map)} 页")

    # Write wiki_data.md
    print(f"💾 写入 {output_path}...")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# {output_path.parent.parent.name} Wiki Data\n\n")
        f.write(f"来源: {config['api']}\n")
        f.write(f"分类: {', '.join(config['categories'])}\n")
        f.write(f"总页数: {len(content_map)}\n\n")

        for title in sorted(content_map.keys()):
            f.write(f"## {title}\n\n")
            f.write(f"{content_map[title]}\n\n")
            f.write("---\n\n")

    print(f"  ✅ done: {len(content_map)} 篇文章\n")
    return len(content_map)


def main():
    parser = argparse.ArgumentParser(description="Fetch wiki text for game knowledge base")
    parser.add_argument("games", nargs="+", help="游戏名称或 --all")
    parser.add_argument("--all", action="store_true", help="抓取所有游戏")
    args = parser.parse_args()

    if args.all:
        games = list(WIKI_CONFIGS.keys())
    else:
        games = args.games

    total = 0
    for game in games:
        if game not in WIKI_CONFIGS:
            print(f"❌ 未知游戏: {game}，可用: {list(WIKI_CONFIGS.keys())}")
            continue
        total += build_wiki_data(WIKI_CONFIGS[game])
    print(f"\n🎉 全部完成！共 {total} 篇文章")


if __name__ == "__main__":
    main()
