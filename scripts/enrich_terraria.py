#!/usr/bin/env python3
"""
分批补充泰拉瑞亚 Wiki 数据。
每次运行补充一个分类，避免超时。
"""

import re, sys, time, json
from pathlib import Path
from typing import Set, List
import requests

WIKI_PATH = Path("games/terraria/data/wiki_data.md")
API = "https://terraria.fandom.com/api.php"

CATEGORIES = [
    "Crafting_station_items",  # 59 pages
    "Boss_summon_items",       # 20 pages
    "Buffs",                   # 150 pages
    "Debuffs",                 # 15 pages
    "Dye_items",               # 46 pages
    "Vanity_items",            # 252 pages
]

REQUEST_DELAY = 0.3  # seconds between API requests


def load_existing_titles() -> Set[str]:
    if not WIKI_PATH.exists():
        return set()
    text = WIKI_PATH.read_text(encoding="utf-8")
    titles = set()
    for m in re.finditer(r"^#\s*文档[：:]\s*(.+)", text, re.MULTILINE):
        titles.add(m.group(1).strip())
    return titles


def get_category_pages(cat: str) -> List[str]:
    pages = []
    cmcont = None
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{cat}",
            "cmlimit": "max",
            "format": "json",
        }
        if cmcont:
            params["cmcontinue"] = cmcont
        r = requests.get(API, params=params, timeout=15)
        data = r.json()
        for m in data.get("query", {}).get("categorymembers", []):
            if m.get("ns") == 0 and not m["title"].startswith("Category:"):
                pages.append(m["title"])
        cont = data.get("continue", {})
        cmcont = cont.get("cmcontinue")
        if not cmcont:
            break
        time.sleep(0.2)
    return sorted(set(pages))


def clean_wikitext(raw: str) -> str | None:
    text = raw
    # infobox
    for pat in [
        r"(?i)\{\{item\s+infobox.*?(?:\{\{.*?\}\}.)*?\}\}",
        r"(?i)\{\{infobox\s+\w+.*?(?:\{\{.*?\}\}.)*?\}\}",
        r"(?i)\{\{infobox.*?(?:\{\{.*?\}\}.)*?\}\}",
    ]:
        text = re.sub(pat, "", text, flags=re.DOTALL)
    # generic templates
    while "{{" in text and "}}" in text:
        new = re.sub(r"\{\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\}", "", text)
        if new == text:
            break
        text = new
    # categories, language links
    text = re.sub(r"\[\[Category:[^\]]*\]\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\[[a-z]+:[^\]]*\]\]", "", text)
    text = re.sub(r"__[A-Z_]+__", "", text)
    # wiki links
    text = re.sub(r"\[\[([^\]|]*)\|([^\]]*)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    # bold/italic
    text = re.sub(r"'''(.*?)'''", r"\1", text)
    text = re.sub(r"''(.*?)''", r"\1", text)
    # refs
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*/>", "", text)
    # special tags
    for tag in ["nowiki", "noinclude", "includeonly", "onlyinclude"]:
        text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL)
    # comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # files
    text = re.sub(r"\[\[(?:File|Image|Media)[^\]]*\]\]", "", text, flags=re.IGNORECASE)
    # whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()
    if len(text) < 50:
        return None
    return text


def fetch_page(title: str) -> str | None:
    params = {
        "action": "parse",
        "page": title,
        "prop": "wikitext",
        "format": "json",
        "redirects": 1,
        "disablelimitreport": 1,
    }
    r = requests.get(API, params=params, timeout=15)
    data = r.json()
    pd = data.get("parse", {})
    redirects = pd.get("redirects", [])
    if redirects:
        actual = redirects[0].get("to", "")
        if actual:
            params["page"] = actual
            r = requests.get(API, params=params, timeout=15)
            data = r.json()
            pd = data.get("parse", {})
    wt = pd.get("wikitext", {}).get("*", "")
    if not wt:
        return None
    return clean_wikitext(wt)


def format_doc(title: str, content: str, category: str) -> str:
    slug = re.sub(r"[^a-z0-9\-]", "", title.lower().replace(" ", "-"))
    if not slug:
        slug = "unknown"
    return (
        f"# 文档：{title}\n"
        f"- 类别：{category}\n"
        f"- 标识：{slug}\n"
        f"- 来源：wiki/{title.replace(' ', '_')}\n"
        f"\n{content}"
    )


def update_doc_count():
    text = WIKI_PATH.read_text(encoding="utf-8")
    titles = re.findall(r"^#\s*文档[：:]\s*(.+)", text, re.MULTILINE)
    text = re.sub(r"(文档总数：)(\d+)", f"\\g<1>{len(titles)}", text)
    WIKI_PATH.write_text(text, encoding="utf-8")


def run_category(cat: str):
    existing = load_existing_titles()
    print(f"\n{'='*50}")
    print(f"📂 {cat}")
    print(f"  已有文档总数: {len(existing)}")

    pages = get_category_pages(cat)
    print(f"  分类 {cat}: {len(pages)} 页")

    new_count = 0
    for title in pages:
        if title in existing:
            continue

        # Skip certain problematic pages
        low = title.lower()
        if "unobtainable" in low or "unused" in low:
            continue

        print(f"  ⬇️  {title} ...", end="", flush=True)

        try:
            content = fetch_page(title)
        except Exception as e:
            print(f" ❌ {e}")
            time.sleep(2)
            continue

        if not content:
            print(" ⏭️")
            continue

        if len(content) > 8000:
            content = content[:8000] + "\n\n...(内容截断)"

        doc = format_doc(title, content, cat.lower())
        
        # 追加到文件（即时写入，避免超时丢失）
        with open(WIKI_PATH, "a", encoding="utf-8") as f:
            f.write("\n" + doc + "\n")

        existing.add(title)
        new_count += 1
        print(f" {len(content):,}字符 ✅")

        time.sleep(REQUEST_DELAY)

    if new_count > 0:
        update_doc_count()
        print(f"\n  ✅ {cat}: 新增 {new_count} 篇")
    else:
        print(f"\n  ✅ {cat}: 无新增")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--cat", choices=CATEGORIES, help="分类名（不指定则列出可用分类）")
    parser.add_argument("--list", action="store_true", help="列出可用分类及预估页数")
    args = parser.parse_args()

    if args.list or not args.cat:
        print("可用分类:")
        for cat in CATEGORIES:
            pages = get_category_pages(cat)
            print(f"  {cat}: ~{len(pages)} 页")
        sys.exit(0)

    run_category(args.cat)
