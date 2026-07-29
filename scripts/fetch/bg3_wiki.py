#!/usr/bin/env python3
"""
博德之门3 数据采集脚本（v2 — 使用 action=parse 获取完整渲染内容）。

数据来源：https://bg3.wiki（非 Fandom 社区维基，标准 MediaWiki API）
用法：
  python scripts/fetch/bg3_wiki.py
  python scripts/fetch/bg3_wiki.py --max-pages 100   # 限速测试
  python scripts/fetch/bg3_wiki.py --resume           # 断点续传
  python scripts/fetch/bg3_wiki.py --workers 8        # 8线程并发（默认4）

输出：
  games/baldurs_gate3/data/wiki_data.md
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Dict

try:
    import requests
except ImportError:
    print("❌ 需要 requests 库：pip install requests")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GAMES_DIR = PROJECT_ROOT / "games"
OUTPUT_DIR = GAMES_DIR / "baldurs_gate3" / "data"
OUTPUT_FILE = OUTPUT_DIR / "wiki_data.md"

API_URL = "https://bg3.wiki/w/api.php"
USER_AGENT = "GameGuideBot/2.0 (Baldur's Gate 3 wiki fetcher; contact: agent@weirdsnap.top)"
DELAY = 0.3  # 请求间隔（秒）

# ── 内容分类（只拉取文章，跳过文件/分类页） ──
CONTENT_CATEGORIES = [
    # 角色
    "Characters", "Companions", "Camp_Followers", "Bosses",
    "Merchants", "Traders", "Quest_givers",
    # 地点
    "Areas", "Act_One_Locations", "Act_Two_Locations", "Act_Three_Locations",
    "Dungeons", "Campsites",
    # 职业与种族
    "Classes", "Archetypes", "Races", "Subraces", "Backgrounds",
    # 法术与动作
    "Spells", "Cantrips", "Actions", "Bonus_actions",
    "Class_actions", "Channel_Divinity_actions", "Reactions",
    # 装备
    "Amulets", "Armour", "Boots", "Cloaks", "Clothing",
    "Gloves", "Helmets", "Rings", "Shields", "Weapons",
    "Camp_Clothing", "Camp_Shoes",
    # 武器子类
    "Battleaxes", "Clubs", "Daggers", "Greatswords", "Longswords",
    "Maces", "Quarterstaves", "Rapiers", "Scimitars",
    "Shortswords", "Spears", "Warhammers",
    # 弓箭弹药
    "Arrows", "Ammunition",
    # 道具
    "Books", "Potions", "Scrolls", "Elixirs", "Camp_Supplies",
    "Alchemical_Ingredients", "Alchemical_Extracts", "Ingredients", "Grenades",
    # 任务
    "Quests", "Side_quests", "Companion_quests",
    # 其他
    "Achievements", "Status_effects", "Conditions", "Passive_features", "Feats",
]


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def api_call(session: requests.Session, params: dict) -> dict:
    """调用 API，自动处理错误和重试。"""
    for attempt in range(3):
        try:
            resp = session.get(API_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                print(f"  ⚠️ API error: {data['error'].get('info', data['error'])}")
                return {}
            return data
        except requests.exceptions.Timeout:
            print(f"  ⚠️ 超时(第{attempt+1}次), 等待后重试...")
            time.sleep(2)
        except requests.exceptions.RequestException as e:
            print(f"  ⚠️ 请求失败: {e}")
            time.sleep(1)
    return {}


def get_category_members(session: requests.Session, category: str, limit: int = 500) -> List[dict]:
    """获取分类下所有页面。"""
    members = []
    cmcontinue = None
    cat_title = f"Category:{category}"

    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "categorymembers",
            "cmtitle": cat_title,
            "cmlimit": min(limit, 500),
            "cmtype": "page",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        data = api_call(session, params)
        if not data:
            break

        batch = data.get("query", {}).get("categorymembers", [])
        members.extend(batch)

        cont = data.get("continue", {})
        cmcontinue = cont.get("cmcontinue")
        if not cmcontinue:
            break

        time.sleep(DELAY)

    return members


def fetch_page_parse(session: requests.Session, title: str) -> Optional[dict]:
    """
    使用 action=parse 获取单页面完整渲染内容。
    比 prop=extracts 慢但能获取到模板展开后的文本。
    """
    params = {
        "action": "parse",
        "format": "json",
        "page": title,
        "prop": "text",
    }
    data = api_call(session, params)
    if not data:
        return None

    parse = data.get("parse", {})
    html = parse.get("text", {}).get("*", "")
    if not html:
        return {"title": title, "extract": ""}

    # HTML → 纯文本
    text = html
    # 移除脚本和样式
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    # 替换标签为空格
    text = re.sub(r'<[^>]+>', ' ', text)
    # 压缩空白
    text = re.sub(r'\s+', ' ', text)
    # 移除导航/元信息行（页面底部常见）
    text = re.sub(r'(Retrieved from|Categories|This page was last edited).*$', '', text, flags=re.IGNORECASE)

    text = text.strip()
    pageurl = f"https://bg3.wiki/wiki/{title.replace(' ', '_')}"

    return {
        "title": title,
        "extract": text,
        "url": pageurl,
    }


def fetch_pages_concurrent(titles: List[str], workers: int = 4) -> Dict[str, dict]:
    """并发获取页面内容。"""
    results = {}
    total = len(titles)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        # 每个线程创建独立 session
        futures = []
        for title in titles:
            sess = make_session()
            futures.append(executor.submit(fetch_page_parse, sess, title))

        done = 0
        for future in as_completed(futures):
            done += 1
            if total > 0 and done % 50 == 0:
                pct = done * 100 // total
                print(f"    ⏳ 进度: {done}/{total} ({pct}%)")
            try:
                result = future.result()
                if result:
                    results[result["title"]] = result
            except Exception as e:
                print(f"    ⚠️ 线程异常: {e}")

    return results


def markdown_from_page(title: str, data: dict, used_categories: List[str]) -> str:
    """将页面数据格式化为 markdown 条目。"""
    categories_md = "、".join(used_categories)
    md = f"## {title}\n\n"
    md += f"- **来源**: [{title}]({data['url']})\n"
    md += f"- **分类**: {categories_md}\n\n"
    extract = data.get("extract", "").strip()
    if extract:
        md += extract + "\n\n"
    else:
        md += "（无内容摘要）\n\n"
    return md


def load_progress() -> set:
    """加载已拉取完成的页面标题。"""
    if not OUTPUT_FILE.exists():
        return set()
    done = set()
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^## (.+)$", line.strip())
            if m:
                done.add(m.group(1))
    return done


def save_incremental(md_content: str):
    """追加内容到输出文件。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(md_content)


def main():
    parser = argparse.ArgumentParser(description="采集博德之门3维基数据")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="最多拉取页面数（0=不限制）")
    parser.add_argument("--resume", action="store_true",
                        help="断点续传（跳过已存在的条目）")
    parser.add_argument("--workers", type=int, default=4,
                        help="并发线程数（默认4）")
    parser.add_argument("--categories", nargs="*",
                        help="指定分类列表（默认所有配置的分类）")
    args = parser.parse_args()

    categories = args.categories or CONTENT_CATEGORIES

    print(f"🎯 目标分类: {len(categories)} 个")
    print(f"📁 输出: {OUTPUT_FILE}")
    print(f"⚙️  并发线程: {args.workers}")

    if args.resume:
        done_titles = load_progress()
        print(f"📋 已存在 {len(done_titles)} 个条目，将跳过")
    else:
        done_titles = set()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        open(OUTPUT_FILE, "w", encoding="utf-8").close()

    session = make_session()
    total_pages = 0
    cat_stats: dict[str, int] = {}
    all_page_titles: dict[str, list[str]] = {}  # title → [categories]
    all_category_members: dict[str, list[str]] = {}  # category → [titles]

    # ── 第1轮：遍历分类收集页面标题（不拉内容） ──
    for cat in categories:
        print(f"\n📂 分类: {cat}")
        members = get_category_members(session, cat)
        if not members:
            print(f"  ⚠️ 分类为空或不存在")
            continue

        page_titles = []
        for m in members:
            title = m.get("title", "")
            if title in done_titles:
                continue
            if title not in all_page_titles:
                all_page_titles[title] = []
            if cat not in all_page_titles[title]:
                all_page_titles[title].append(cat)
            page_titles.append(title)

        all_category_members[cat] = page_titles
        print(f"  页面数: {len(members)}（新: {len(page_titles)}）")
        cat_stats[cat] = len(members)

    # ── 收集全部待拉取标题 ──
    all_titles = list(all_page_titles.keys())
    total_new = len(all_titles)
    print(f"\n📊 待拉取总数: {total_new} 页")

    if args.max_pages and total_new > args.max_pages:
        all_titles = all_titles[:args.max_pages]
        print(f"⛔ 限制为 {args.max_pages} 页")
        total_new = args.max_pages

    if total_new == 0:
        print("✅ 没有新页面需要拉取")
        return

    # ── 第2轮：并发拉取内容 ──
    print(f"\n⬇️  正在拉取 {total_new} 页内容 (并发{args.workers}线程)...")
    start_time = time.time()
    pages_content = fetch_pages_concurrent(all_titles, workers=args.workers)
    elapsed = time.time() - start_time
    print(f"⏱️  拉取完成，耗时 {elapsed:.0f} 秒，{total_new/elapsed:.1f} 页/秒")

    # ── 写入文件 ──
    print(f"\n💾 写入到 {OUTPUT_FILE}...")
    saved = 0
    for title in all_titles:
        if title not in pages_content:
            continue
        content = pages_content[title]
        used_cats = all_page_titles.get(title, ["Uncategorized"])
        md = markdown_from_page(title, content, used_cats)
        save_incremental(md)
        saved += 1

    total_pages = saved

    # ── 统计 ──
    print(f"\n{'='*50}")
    print(f"📊 采集完成")
    print(f"  总分类数: {len(categories)}")
    print(f"  拉取尝试: {total_new} 页")
    print(f"  成功保存: {saved} 页")
    print(f"  输出文件: {OUTPUT_FILE}")

    # 空内容统计
    empty_count = sum(1 for p in pages_content.values() if not p.get("extract", "").strip())
    print(f"  空内容: {empty_count} 页")

    print(f"\n  分类统计（只显示 > 0 的）:")
    for cat, count in sorted(cat_stats.items(), key=lambda x: -x[1]):
        if count > 0:
            print(f"    {cat}: {count} 页")

    file_size = OUTPUT_FILE.stat().st_size if OUTPUT_FILE.exists() else 0
    print(f"\n  文件大小: {file_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
