#!/usr/bin/env python3
"""
通用 Fandom Wiki 文本抓取工具。
支持中文（--lang zh）和英文（默认）Wiki。

用法：
  python scripts/fetch_wiki.py cyberpunk2077
  python scripts/fetch_wiki.py --lang zh va11halla
  python scripts/fetch_wiki.py --all
  python scripts/fetch_wiki.py --lang zh --all
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
#
# 每个游戏可以定义：
#   api            — Fandom API URL
#   user_agent     — User-Agent 头部
#   output         — 输出文件（会根据 --lang zh 自动加 _zh）
#   categories     — 英文分类名列表
#   zh_categories  — [可选] 中文分类名列表，当 --lang zh 时优先使用
#
# 如未定义 zh_categories，--lang zh 时会尝试使用英文分类名
# （部分中文 wiki 如 怪物猎人荒野 仍使用英文分类）

WIKI_CONFIGS = {
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
        "zh_categories": [
            "VA-11 Hall-A 员工",
            "VA-11 Hall-A 顾客",
            "饮品",
            "配料",
            "人类",
            "动物",
            "地点",
            "组织",
        ],
    },
    "hollow_knight": {
        "api": "https://hollowknight.fandom.com/api.php",
        "user_agent": "GameGuideBot/2.0 (Hollow Knight wiki fetcher)",
        "output": GAMES_DIR / "hollow_knight" / "data" / "wiki_data.md",
        "categories": [
            "Bosses",
            "Charms_(Hollow_Knight)",
            "Characters",
            "Enemies_(Hollow_Knight)",
            "Items_(Hollow_Knight)",
        ],
        "zh_categories": [
            "Boss",
            "护符",
            "技能",
            "法术",
            "物品",
            "敌人",
        ],
    },
    "oni": {
        "api": "https://oxygennotincluded.fandom.com/api.php",
        "user_agent": "GameGuideBot/2.0 (ONI wiki fetcher)",
        "output": GAMES_DIR / "oni" / "data" / "wiki_data.md",
        "categories": [
            "Animals",
            "Buildings",
            "Critters",
            "Food",
            "Geysers",
            "Plants",
            "Resources",
            "Technology",
        ],
        "zh_categories": [
            "小动物",
            "复制人技能",
            "可食用物",
            "房间",
            "功能性植物",
            "工业性植物",
            "娱乐建筑",
            "医疗建筑",
            "传感器",
            "发电机",
        ],
    },
    "terraria": {
        "api": "https://terraria.fandom.com/api.php",
        "user_agent": "GameGuideBot/2.0 (Terraria wiki fetcher)",
        "output": GAMES_DIR / "terraria" / "data" / "wiki_data.md",
        "categories": [
            "Armor_items",
            "Accessory_items",
            "Weapon_items",
            "Bosses",
            "NPCs",
            "Enemies",
        ],
    },
    "mhw": {
        "api": "https://monsterhunter.fandom.com/api.php",
        "user_agent": "GameGuideBot/2.0 (MHWilds wiki fetcher)",
        "output": GAMES_DIR / "mhw" / "data" / "wiki_data.md",
        "categories": [
            "Monsters_in_Monster_Hunter_Wilds",
            "Weapons_in_Monster_Hunter_Wilds",
            "Armor_in_Monster_Hunter_Wilds",
            "Skills_in_Monster_Hunter_Wilds",
            "Locations_in_Monster_Hunter_Wilds",
        ],
        # 中文 MH Wiki 是全系列通用 Wiki，无法按 MHWilds 过滤，暂不抓取
        # 如需手动探索中文分类：list_zh_categories('https://monsterhunter.fandom.com/api.php')
        "zh_categories": [],
    },
    "silksong": {
        "api": "https://hollowknight.fandom.com/api.php",
        "user_agent": "GameGuideBot/2.0 (Silksong wiki fetcher)",
        "output": GAMES_DIR / "silksong" / "data" / "wiki_data.md",
        "categories": [
            "Additional_Content_(Silksong)",
            "Areas_(Silksong)",
            "Bosses_(Silksong)",
            "Combat_(Silksong)",
            "Enemies_(Silksong)",
            "Exploration_(Silksong)",
            "Hollow_Knight:_Silksong",
            "Items_(Silksong)",
            "NPCs_(Silksong)",
            "Points_of_Interest_(Silksong)",
        ],
        # 丝之歌没有独立的中文 Wiki（与空洞骑士共用），暂不抓取
        "zh_categories": [],
    },
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
        # 中文 Wiki 几乎没有实质内容（仅 2 个内容分类，各 1 页）
        # 运行看详情：list_zh_categories('https://cyberpunk.fandom.com/api.php')
        "zh_categories": [],
    },
}

# ── 用户代理池 ──
USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def make_zh_url(api_url: str) -> str:
    """
    将英文 Fandom API URL 转换为中文版本。
    例: https://cyberpunk.fandom.com/api.php → https://cyberpunk.fandom.com/zh/api.php
    """
    return api_url.replace("/api.php", "/zh/api.php", 1)


def api_request(api_url: str, params: dict, ua: str) -> Optional[dict]:
    """发送 MediaWiki API 请求（自动重试 3 次）"""
    params["format"] = "json"
    for attempt in range(3):
        try:
            resp = requests.get(
                api_url, params=params, headers={"User-Agent": ua}, timeout=30
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt < 2:
                wait = 3 * (attempt + 1)
                print(f"    ⏳ 重试 {attempt + 1}/3（{wait}s）...")
                time.sleep(wait)
                continue
            print(f"    ⚠️ API 请求失败: {e}")
            return None
    return None


def iter_api(api_url: str, params: dict, ua: str, limit: int = 500):
    """
    迭代 MediaWiki API，自动翻页（list 查询）。
    params 必须包含 action 和 list 键，limit 参数会自动设置。
    """
    params = dict(params)
    params["format"] = "json"
    params.setdefault("aplimit", min(limit, 500))

    while True:
        data = api_request(api_url, params, ua)
        if data is None:
            break

        yield data

        # 检查继续标记
        cont = data.get("continue", {})
        if not cont:
            break

        # 更新翻页参数（可能是 apcontinue / cmcontinue 等）
        for key, val in cont.items():
            if key != "continue":
                params[key] = val


def get_category_members(api_url: str, category: str, ua: str) -> List[str]:
    """获取分类下所有页面的标题，包括子分类"""
    titles = []
    seen = set()

    cmcontinue = None
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmlimit": "max",
            "cmprop": "title",
            "cmtype": "page",  # 只取页面，不取子分类
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        data = api_request(api_url, params, ua)
        if data is None:
            break

        for m in data.get("query", {}).get("categorymembers", []):
            title = m.get("title", "")
            if title and title not in seen:
                seen.add(title)
                titles.append(title)

        cont = data.get("continue", {})
        cmcontinue = cont.get("cmcontinue")
        if not cmcontinue:
            break

        time.sleep(0.5)

    return titles


def fetch_page_text(api_url: str, title: str, ua: str) -> Optional[str]:
    """
    获取单个页面的纯文本内容（使用 Fandom 的 Parse API）。
    返回去掉 infobox、导航、引用后的干净文本。
    """
    params = {
        "action": "parse",
        "page": title,
        "prop": "text",
        "disablelimitreport": 1,
        "wrapoutputclass": "",
    }
    data = api_request(api_url, params, ua)
    if data is None:
        return None

    error = data.get("error")
    if error:
        print(f"    ⚠️ {title}: API error {error.get('code', '?')}")
        return None

    raw_html = data.get("parse", {}).get("text", {}).get("*", "")
    if not raw_html:
        return None

    return clean_wiki_text(raw_html)


def fetch_batch_content(api_url: str, titles: List[str], ua: str, batch_size: int = 50) -> Dict[str, str]:
    """批量获取页面内容，返回 {title: text}"""
    results = {}
    total = len(titles)

    for i in range(0, total, batch_size):
        batch = titles[i : i + batch_size]
        print(f"  [{i + 1}-{min(i + batch_size, total)}/{total}]...", end=" ", flush=True)

        params = {
            "action": "query",
            "titles": "|".join(batch),
            "prop": "info",
            "format": "json",
        }
        data = api_request(api_url, params, ua)

        if data is None:
            print("❌")
            continue

        # 提取有效的页面标题
        pages = data.get("query", {}).get("pages", {})
        valid_titles = []
        for pid, page in pages.items():
            if pid == "-1":
                continue
            valid_titles.append(page.get("title", ""))

        if not valid_titles:
            print("⚠️ 跳过（无有效页面）")
            continue

        # 逐个获取内容
        for title in valid_titles:
            text = fetch_page_text(api_url, title, ua)
            if text:
                results[title] = text
            time.sleep(0.3)

        print(f"✅ (本批 {len(valid_titles)} 页)")

    return results


def clean_wiki_text(raw_html: str) -> str:
    """
    从 Fandom 的解析 HTML 中提取干净文本。
    规则：
    - 移除 <style>, <script>, <aside>, <nav>, 表格 (.wikitable), 信息框 (.infobox)
    - 将 <br> <p> <li> 等转为换行
    - 移除多余空白
    """
    # 移除脚本样式
    raw_html = re.sub(r"<script[^>]*>.*?</script>", "", raw_html, flags=re.DOTALL)
    raw_html = re.sub(r"<style[^>]*>.*?</style>", "", raw_html, flags=re.DOTALL)

    # 移除特定区块
    raw_html = re.sub(r"<aside[^>]*>.*?</aside>", "", raw_html, flags=re.DOTALL)
    raw_html = re.sub(r"<nav[^>]*>.*?</nav>", "", raw_html, flags=re.DOTALL)
    raw_html = re.sub(r'<table[^>]*class="[^"]*infobox[^"]*"[^>]*>.*?</table>', "", raw_html, flags=re.DOTALL)
    raw_html = re.sub(r'<table[^>]*class="[^"]*wikitable[^"]*"[^>]*>.*?</table>', "", raw_html, flags=re.DOTALL)

    # 块级元素转换行
    raw_html = re.sub(r"</?(?:p|div|h[1-6]|li|br|tr|blockquote)[^>]*>", "\n", raw_html)
    raw_html = re.sub(r"<[^>]+>", " ", raw_html)

    # 清理冗余空白
    raw_html = re.sub(r"&nbsp;", " ", raw_html)
    raw_html = re.sub(r"\n{3,}", "\n\n", raw_html)
    raw_html = re.sub(r"[ \t]{2,}", " ", raw_html)
    raw_html = re.sub(r"^\s+", "", raw_html, flags=re.MULTILINE)

    text = raw_html.strip()
    return text if len(text) > 30 else None  # 太短的忽略


def filter_relevant_pages(api_url: str, ua: str, max_pages: int = 200) -> set:
    """
    兜底方案：当分类抓取不到足够内容时，使用 allpages 列出页面。
    仅返回主命名空间的页面，最多返回 max_pages 条。
    """
    titles = set()
    apcontinue = None
    while len(titles) < max_pages:
        params = {
            "action": "query",
            "list": "allpages",
            "apnamespace": 0,
            "aplimit": "max",
        }
        if apcontinue:
            params["apcontinue"] = apcontinue
        data = api_request(api_url, params, ua)
        if data is None:
            break
        query = data.get("query", {})
        for p in query.get("allpages", []):
            title = p.get("title", "")
            if title:
                titles.add(title)
                if len(titles) >= max_pages:
                    break
        cont = data.get("continue", {})
        apcontinue = cont.get("apcontinue")
        if not apcontinue:
            break
        time.sleep(0.5)
    if apcontinue:
        print(f"    ⚠️ 达到上限 {max_pages} 页，省略更多页面（可通过 --max-pages 增加）")
    return titles


def build_wiki_data(config: dict, lang: str = "en", max_pages: int = 200) -> int:
    """为单个游戏构建 wiki_data[_zh].md，返回文章数"""
    api_url = config["api"]
    if lang == "zh":
        api_url = make_zh_url(api_url)
        print(f"\n🌐 中文 Wiki: {api_url}")
    else:
        print(f"\n🌐 Wiki: {api_url}")

    # 输出路径（中文加 _zh）
    output_path = config["output"]
    if lang == "zh":
        stem = output_path.stem  # wiki_data
        output_path = output_path.with_name(f"{stem}_zh.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 确定分类
    if lang == "zh" and "zh_categories" in config:
        categories = config["zh_categories"]
        if len(categories) == 0:
            print(f"  ⏭️ zh_categories 为空，该游戏无可用中文数据，跳过")
            return 0
    else:
        categories = config["categories"]

    # 按分类抓取
    all_titles = set()
    total_pages_found = 0
    for cat in categories:
        print(f"  📂 分类: {cat}")
        titles = get_category_members(api_url, cat, config["user_agent"])
        print(f"    → {len(titles)} 页")
        all_titles.update(titles)
        total_pages_found += len(titles)
        time.sleep(0.5)

    # 兜底：分类太少则使用 allpages
    if total_pages_found < 10:
        print(f"  ⚠️ 分类返回 {total_pages_found} 页，较少，尝试 allpages 兜底（上限 {max_pages} 页）...")
        allpage_titles = filter_relevant_pages(api_url, config["user_agent"], max_pages=max_pages)
        print(f"  → allpages 返回 {len(allpage_titles)} 页")
        all_titles.update(allpage_titles)    
    
    # 如果仍然太少，提示用户定义 zh_categories
    if len(all_titles) < 10 and lang == "zh":
        print(f"  💡 提示: 中文分类未返回足够页面。")
        print(f"     可以为该游戏添加 zh_categories 字段，或使用 --max-pages 增加上限。")
        print(f"     先用以下命令查看中文 wiki 有哪些分类可用：")
        print(f"       python3 -c \"from scripts.fetch_wiki import list_zh_categories; list_zh_categories('{config['api']}')\"")
        return 0

    print(f"\n📊 总页面数（去重）: {len(all_titles)}")
    if not all_titles:
        print("  ⚠️ 没有页面，跳过")
        return 0

    # 获取内容（分批写入，支持中断恢复）
    already_fetched = set()
    checkpoint_path = output_path.with_suffix(".checkpoint")  # 如 wiki_data_zh.checkpoint
    if checkpoint_path.exists():
        with open(checkpoint_path, "r") as cf:
            already_fetched = set(line.strip() for line in cf if line.strip())
        print(f"  🔄 发现检查点，已获取 {len(already_fetched)} 页，跳过继续...")
        remaining_titles = [t for t in all_titles if t not in already_fetched]
    else:
        remaining_titles = list(all_titles)

    if not remaining_titles:
        print(f"  ✅ 所有页面已获取完毕（{len(already_fetched)} 页）")
        total_pages = len(already_fetched)
    else:
        # 初次写入时写 header
        game_name = output_path.parent.parent.name
        lang_label = "中文" if lang == "zh" else "English"
        header = (f"# {game_name} Wiki Data ({lang_label})\n\n"
                  f"来源: {api_url}\n"
                  f"语言: {lang_label}\n"
                  f"分类: {', '.join(categories)}\n"
                  f"总页数: ???（估算 {len(all_titles)}）\n\n")

        if not already_fetched:
            # 全新写入
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(header)

        first_time = not already_fetched
        total_fetched_in_this_run = 0
        batch_size = 50
        print(f"📥 获取内容（批量 {batch_size} 条）...")
        for i in range(0, len(remaining_titles), batch_size):
            batch = remaining_titles[i:i+batch_size]
            print(f"  [{i+1}-{min(i+batch_size, len(remaining_titles))}/{len(remaining_titles)}]... ", end="", flush=True)
            results = fetch_batch_content(api_url, batch, config["user_agent"])
            if not results:
                print("⚠️ 跳过空结果")
                continue

            # 追加写入
            with open(output_path, "a", encoding="utf-8") as f:
                for title in sorted(results.keys()):
                    f.write(f"## {title}\n\n")
                    f.write(f"{results[title]}\n\n")
                    f.write("---\n\n")

            # 更新检查点
            with open(checkpoint_path, "a", encoding="utf-8") as cf:
                for title in batch:
                    cf.write(f"{title}\n")

            total_fetched_in_this_run += len(results)
            print(f"✅ (本批 {len(results)} 页)")

        total_pages = len(already_fetched) + total_fetched_in_this_run

        # 更新总页数
        print(f"  🔄 更新总页数 → {total_pages}")
        with open(output_path, "r+", encoding="utf-8") as f:
            content = f.read()
            content = content.replace(
                f"总页数: ???（估算 {len(all_titles)}）",
                f"总页数: {total_pages}"
            )
            f.seek(0)
            f.write(content)
            f.truncate()

        # 清理检查点
        checkpoint_path.unlink(missing_ok=True)

    print(f"  ✅ done: {total_pages} 篇文章\n")
    return len(content_map)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch wiki text for game knowledge base"
    )
    parser.add_argument("games", nargs="*", default=[], help="游戏名称（留空配合 --all 抓取全部）")
    parser.add_argument("--all", action="store_true", help="抓取所有游戏")
    parser.add_argument(
        "--lang", default="en", choices=["en", "zh"],
        help="语言: en（默认英文）, zh（中文）"
    )
    parser.add_argument(
        "--max-pages", type=int, default=200,
        help="allpages 兜底时的最大页数（默认 200，仅 --lang zh 且分类无效时使用）"
    )
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
        total += build_wiki_data(WIKI_CONFIGS[game], lang=args.lang, max_pages=args.max_pages)
    print(f"\n🎉 全部完成！共 {total} 篇文章")


if __name__ == "__main__":
    main()


def list_zh_categories(en_api_url: str):
    """列出中文 wiki 上所有有内容的分类（辅助发现 zh_categories）"""
    zh_url = make_zh_url(en_api_url)
    params = {
        "action": "query",
        "list": "allcategories",
        "acprop": "size|pages",
        "aclimit": 100,
        "format": "json",
    }
    data = api_request(zh_url, params, "GameGuideBot/2.0 (category explorer)")
    if not data:
        print("❌ 无法访问 API")
        return
    cats = data.get("query", {}).get("allcategories", [])
    # 过滤：至少有 1 个页面（不仅是子分类）
    content_cats = [c for c in cats if c.get("pages", 0) >= 1]
    print(f"\n📂 中文 Wiki 分类（含页码数，共 {len(content_cats)} 个）:\n")
    for c in sorted(content_cats, key=lambda x: -x.get("pages", 0)):
        name = c["*"]
        pages = c.get("pages", 0)
        print(f"  {name:30s} {pages} 页")


# ── 快速测试 ──
# python -c 'from scripts.fetch_wiki import test_zh; test_zh()'
def test_zh():
    """测试中文 API 是否可用"""
    for game, cfg in WIKI_CONFIGS.items():
        zh_url = make_zh_url(cfg["api"])
        params = {
            "action": "query",
            "meta": "siteinfo",
            "siprop": "general",
            "format": "json",
        }
        try:
            r = requests.get(
                zh_url,
                params=params,
                headers={"User-Agent": cfg["user_agent"]},
                timeout=10,
            )
            data = r.json()
            gen = data.get("query", {}).get("general", {})
            print(f"{game:20s} ✅ {gen.get('sitename', '?')}  lang={gen.get('lang', '?')}")
        except Exception as e:
            print(f"{game:20s} ❌ {e}")
