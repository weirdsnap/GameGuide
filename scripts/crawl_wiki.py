#!/usr/bin/env python3
"""
通用 Wiki 数据采集器。

从任意 MediaWiki/Fandom Wiki 获取页面内容，
清洗后转为统一格式的自然语言文档。

用法：
  python scripts/crawl_wiki.py --game oni
  python scripts/crawl_wiki.py --game silksong
  python scripts/crawl_wiki.py --game terraria
  python scripts/crawl_wiki.py --game oni --dry-run
  python scripts/crawl_wiki.py --game oni --category bosses
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("❌ 需要 requests 库：pip install requests")
    sys.exit(1)

# ── 游戏配置 ──

GAMES: Dict[str, Dict[str, Any]] = {
    "oni": {
        "name": "Oxygen Not Included",
        "output_dir": "games/oni/data",
        "api": "https://oxygennotincluded.fandom.com/api.php",
        "user_agent": "GameGuideBot/1.0 (ONI wiki collector)",
        "categories": {
            "Biomes": "biomes",
            "Buildings": "buildings",
            "Critters": "critters",
            "Creatures": "creatures",
            "Diseases": "diseases",
            "Duplicants": "duplicants",
            "Food": "food",
            "Game_Mechanics": "mechanics",
            "Geysers": "geysers",
            "Industrial_Ingredient": "materials",
            "Items": "items",
            "Liquid": "liquids",
            "Lore": "lore",
            "Medicine_Ingredients": "medicine",
            "Gas": "gases",
            "Plants": "plants",
            "Resources": "resources",
            "Rooms": "rooms",
        },
    },
    "silksong": {
        "name": "Hollow Knight Silksong",
        "output_dir": "games/silksong/data",
        "api": "https://hollowknight.fandom.com/api.php",
        "user_agent": "GameGuideBot/1.0 (Silksong wiki collector)",
        "categories": {
            "Abilities_(Silksong)": "abilities",
            "Areas_(Silksong)": "areas",
            "Bosses_(Silksong)": "bosses",
            "Combat_(Silksong)": "combat",
            "Enemies_(Silksong)": "enemies",
            "Exploration_(Silksong)": "exploration",
            "Items_(Silksong)": "items",
            "NPCs_(Silksong)": "characters",
            "Points_of_Interest_(Silksong)": "points_of_interest",
            "Quests_(Silksong)": "quests",
            "Skills_and_Tools": "skills",
        },
    },
    "terraria": {
        "name": "Terraria",
        "output_dir": "games/terraria/data",
        "api": "https://terraria.fandom.com/api.php",
        "user_agent": "GameGuideBot/1.0 (Terraria wiki collector)",
        "categories": {
            "Boss_NPCs": "bosses",
            "Enemy_NPCs": "enemies",
            "Armor_items": "armor",
            "Accessory_items": "accessories",
            "Weapon_items": "weapons",
            "Potion_items": "potions",
            "Block_items": "blocks",
            "Furniture_items": "furniture",
            "Crafting_material_items": "materials",
            "Ammunition_items": "ammo",
            "NPCs": "npcs",
            "Consumable_items": "consumables",
        },
    },
}


# ── 工具函数 ──

def clean_wikitext(wt: str) -> str:
    """将 Wiki 标记文本转为纯文本。"""
    text = wt

    # 1. 移除 HTML 注释
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # 2. 移除 <ref> 引用
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^>]*/>', '', text)

    # 3. 移除块级标签
    for tag in ['gallery', 'nowiki', 'pre', 'code', 'poem', 'includeonly', 'noinclude']:
        text = re.sub(f'<{tag}[^>]*>.*?</{tag}>', '', text, flags=re.DOTALL)

    # 4. 移除占位图
    text = re.sub(r'\[\[File:[^\]]*\]\]', '', text)
    text = re.sub(r'\[\[Image:[^\]]*\]\]', '', text)

    # 5. 移除多媒体链接
    text = re.sub(r'\[\[Media:[^\]]*\]\]', '', text)

    # 6. 移除分类标签
    text = re.sub(r'\[\[Category:[^\]]*\]\]', '', text)

    # 7. 移除非英语的跨语言链接
    text = re.sub(r'\[\[[a-z]{2,3}:[^\]]*\]\]', '', text)

    # 8. 处理表格（保留文字）
    lines = text.split('\n')
    cleaned_lines = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('{|'):
            in_table = True
            continue
        if stripped == '|}':
            in_table = False
            continue
        if in_table:
            if stripped.startswith('|') or stripped.startswith('!'):
                content = stripped.lstrip('|!-').strip()
                if content:
                    cleaned_lines.append(content)
            continue
        cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines)

    # 9. 移除模板 {{...}}
    _tmpl_loop = 0
    while '{{' in text:
        _tmpl_loop += 1
        if _tmpl_loop > 100:
            text = re.sub(r'{{|}}', '', text)
            break
        new_text, _n = re.subn(r'\{\{[^{}]*?\}\}', '', text)
        if _n == 0:
            text = re.sub(r'{{|}}', '', text)
            break
        text = new_text
        if '{{' not in text:
            break

    # 10. 转换内部链接 [[Link|text]] → text
    text = re.sub(r'\[\[([^\]|]*?)\|([^\]]*?)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]*?)\]\]', r'\1', text)

    # 11. 转换外部链接
    text = re.sub(r'\[https?://[^\s\[\]]+\s+([^\]]+)\]', r'\1', text)
    text = re.sub(r'\[https?://[^\s\[\]]+\]', '', text)

    # 12. 移除内联标签
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<hr\s*/?>', '\n---\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(div|span|center|small|big|sup|sub|u|s|strike|abbr|tt|code|blockquote|cite|table|tr|td|th|tbody|thead|caption|colgroup|col)[^>]*>', '', text, flags=re.IGNORECASE)

    # 13. 加粗/斜体
    text = re.sub(r"'''(.*?)'''", r'\1', text)
    text = re.sub(r"''(.*?)''", r'\1', text)

    # 14. 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'^[ \t]+|[ \t]+$', '', text, flags=re.MULTILINE)

    # 15. 移除残余标签
    text = re.sub(r'</?h[23456][^>]*>', '', text, flags=re.IGNORECASE)

    return text.strip()


def wikitext_to_heading(text: str, level: int = 2) -> str:
    """==h== → ## h"""
    return re.sub(r'^={2,6}\s*(.*?)\s*={2,6}\s*$',
                  lambda m: '#' * level + ' ' + m.group(1).strip(),
                  text, flags=re.MULTILINE)


def strip_list_markers(text: str) -> str:
    """* # : ; → 纯文本"""
    return re.sub(r'^[*#;:]+\s*', '', text, flags=re.MULTILINE)


def page_title_to_name(title: str) -> str:
    """处理页面标题：去掉括号后缀，下划线→空格"""
    name = title.replace('_', ' ')
    name = re.sub(r'\s*\(.*?\)\s*', '', name).strip()
    if not name:
        name = title.replace('_', ' ')
    return name


def page_title_to_slug(title: str) -> str:
    """'Mantis_Claw' → 'mantis-claw'"""
    slug = title.lower().replace('_', '-').replace("'", '').replace('(', '').replace(')', '')
    # 保留中文
    return slug


# ── Wiki API 交互 ──

class WikiCollector:
    """通用 Wiki 数据采集器。"""

    def __init__(self, api_base: str, user_agent: str, output_dir: str):
        self.api_base = api_base
        self.headers = {'User-Agent': user_agent}
        self.output_path = Path(__file__).parent.parent / output_dir
        self.output_path.mkdir(parents=True, exist_ok=True)

    def api_call(self, params: Dict[str, str]) -> Optional[Dict[str, Any]]:
        params['format'] = 'json'
        try:
            resp = requests.get(self.api_base, params=params, headers=self.headers, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"  ⚠️ API 请求失败: {e}")
            return None

    def get_category_members(self, category: str, limit: int = 200) -> List[str]:
        """获取分类下的所有页面标题。"""
        titles = []
        cmcontinue = None

        while True:
            params = {
                'action': 'query',
                'list': 'categorymembers',
                'cmtitle': f'Category:{category}',
                'cmlimit': min(50, limit),
            }
            if cmcontinue:
                params['cmcontinue'] = cmcontinue

            data = self.api_call(params)
            if not data:
                break

            for member in data.get('query', {}).get('categorymembers', []):
                if member.get('ns') == 0:
                    titles.append(member['title'])

            if len(titles) >= limit:
                break

            cont = data.get('continue', {})
            cmcontinue = cont.get('cmcontinue')
            if not cmcontinue:
                break

        return titles[:limit]

    def get_pages_wikitext(self, titles: List[str]) -> Dict[str, str]:
        """批量获取页面 wikitext，自动跟随重定向。"""
        if not titles:
            return {}

        result = {}
        batch_size = 20
        for batch_start in range(0, len(titles), batch_size):
            batch = list(set(titles[batch_start:batch_start + batch_size]))
            params = {
                'action': 'query',
                'titles': '|'.join(batch),
                'redirects': '1',
                'prop': 'revisions',
                'rvprop': 'content',
            }
            data = self.api_call(params)
            if not data or 'query' not in data:
                continue

            query = data['query']

            # 映射原始标题 → 实际标题
            title_map = {}
            if 'redirects' in query:
                for rd in query['redirects']:
                    title_map[rd['from']] = rd['to']
            if 'normalized' in query:
                for n in query['normalized']:
                    if n['from'] not in title_map:
                        title_map[n['from']] = n['to']
            for t in batch:
                if t not in title_map:
                    title_map[t] = t

            # 收集内容
            content_by_title = {}
            for page_id, page_data in query.get('pages', {}).items():
                if page_id == '-1':
                    continue
                title = page_data.get('title', '')
                revisions = page_data.get('revisions', [])
                if revisions:
                    content_by_title[title] = revisions[0].get('*', '')

            # 链式解析重定向
            for orig in batch:
                target = orig
                seen = set()
                while target in title_map and target not in seen:
                    seen.add(target)
                    target = title_map[target]
                if target in content_by_title:
                    result[orig] = content_by_title[target]

            time.sleep(1.5)

        return result

    def render_document(self, title: str, category: str, wikitext: str, source_url: str = "") -> Optional[Dict[str, str]]:
        """将 wikitext 转为统一格式文档。"""
        text = clean_wikitext(wikitext)

        # 跳过太短或重定向
        if len(text) < 50:
            return None
        if text.startswith('#REDIRECT') or text.startswith('#redirect'):
            return None

        text = wikitext_to_heading(text)
        text = strip_list_markers(text)
        text = re.sub(r'__\w+__', '', text)
        text = text.strip()

        if len(text) < 50:
            return None

        name = page_title_to_name(title)
        slug = page_title_to_slug(title)

        return {
            "text": f"# 文档：{name}\n\n"
                    f"- 类别：{category}\n"
                    f"- 标识：{slug}\n"
                    f"- 来源：wiki/{title.replace(' ', '_')}\n\n"
                    f"{text}",
            "metadata": {
                "source": f"wiki/{slug}",
                "category": category,
                "title": name,
            }
        }

    def collect_category(self, category_name: str, doc_category: str, limit: int = 200) -> List[Dict[str, str]]:
        """采集一个分类的所有页面。"""
        print(f"\n📂 分类：{category_name} → {doc_category}")
        titles = self.get_category_members(category_name, limit=limit)
        # 过滤掉非内容页面
        titles = [t for t in titles
                  if not t.startswith(('User:', 'Template:', 'File:', 'Category:', 'Module:'))
                  and not t.endswith('/doc')]
        print(f"  找到 {len(titles)} 个内容页面")

        if not titles:
            print("  ⏭️ 空分类，跳过")
            return []

        docs = []
        batch_all = self.get_pages_wikitext(titles)
        print(f"  获取到 {len(batch_all)} 篇内容", flush=True)

        for title in titles:
            wt = batch_all.get(title)
            if not wt:
                continue

            doc = self.render_document(title, doc_category, wt)
            if doc:
                docs.append(doc)
                print(f"  ✅ {doc['metadata']['title']}: {len(doc['text'])} chars", flush=True)
            else:
                print(f"  ⏭️ {title}: 跳过（太短）", flush=True)

        return docs

    def save_documents(self, all_docs: List[Dict[str, str]], game_name: str):
        """保存文档为 Markdown。"""
        output_path = self.output_path / "wiki_data.md"

        # 按类别分组
        by_cat: Dict[str, List[Dict[str, str]]] = {}
        for doc in all_docs:
            cat = doc['metadata']['category']
            by_cat.setdefault(cat, []).append(doc)

        import datetime
        lines = [
            f"# {game_name} Wiki 数据",
            "",
            f"自动采集自 Wiki",
            f"采集时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"文档总数：{len(all_docs)}",
            "",
            "---",
            "",
        ]

        for cat, docs in sorted(by_cat.items()):
            lines.append(f"## {cat.upper()}")
            lines.append(f"共 {len(docs)} 篇文档")
            lines.append("")
            for doc in docs:
                lines.append(doc['text'])
                lines.append("")
                lines.append("---")
                lines.append("")

        text = '\n'.join(lines) + '\n'
        output_path.write_text(text, encoding='utf-8')
        print(f"\n✅ 已保存到 {output_path}（{len(all_docs)} 篇，{len(text):,} chars）")


# ── 入口 ──

def list_categories(api_base: str, user_agent: str, prefix_filter: str = ""):
    """列出 Wiki 上的所有分类。"""
    headers = {'User-Agent': user_agent}
    params = {
        'action': 'query',
        'list': 'allcategories',
        'aclimit': 500,
    }
    try:
        resp = requests.get(api_base, params={**params, 'format': 'json'}, headers=headers, timeout=15)
        data = resp.json()
    except Exception as e:
        print(f"  ⚠️ 无法获取分类: {e}")
        return

    cats = [c['*'] for c in data['query']['allcategories']]
    if prefix_filter:
        cats = [c for c in cats if prefix_filter.lower() in c.lower()]
    for cat in sorted(cats):
        print(f"  {cat}")
    print(f"\n总分类数：{len(cats)}")


def main():
    parser = argparse.ArgumentParser(description="通用 Wiki 数据采集器")
    parser.add_argument("--game", "-g", required=True, choices=list(GAMES.keys()),
                        help="游戏名称")
    parser.add_argument("--dry-run", action="store_true", help="试跑（不写文件）")
    parser.add_argument("--category", "-c", help="只跑指定分类")
    parser.add_argument("--list-categories", action="store_true", help="列出 Wiki 分类")
    parser.add_argument("--limit", type=int, default=200, help="每分类最大采集量")
    args = parser.parse_args()

    config = GAMES[args.game]
    collector = WikiCollector(
        api_base=config["api"],
        user_agent=config["user_agent"],
        output_dir=config["output_dir"],
    )

    if args.list_categories:
        print(f"\n📋 {config['name']} Wiki 分类列表：")
        # 尝试列举该Wiki分类
        list_categories(config["api"], config["user_agent"])
        return

    # 采集
    all_docs: List[Dict[str, str]] = []

    for cat_name, doc_cat in config["categories"].items():
        if args.category and doc_cat != args.category:
            continue
        docs = collector.collect_category(cat_name, doc_cat, limit=args.limit)
        all_docs.extend(docs)

    if not all_docs:
        print("\n❌ 没有采集到任何文档")
        sys.exit(1)

    # 统计
    by_cat = Counter()
    for d in all_docs:
        by_cat[d['metadata']['category']] += 1

    print("\n" + "=" * 50 + "\n")
    print(f"📊 {config['name']} 采集结果：")
    for cat, count in sorted(by_cat.items()):
        print(f"  {cat}: {count} 篇")
    print(f"  总计: {len(all_docs)} 篇")
    total_chars = sum(len(d['text']) for d in all_docs)
    print(f"  总字数: ~{total_chars:,} chars")

    # 保存
    if not args.dry_run:
        collector.save_documents(all_docs, config["name"])
    else:
        print("\n(dry-run 模式，未写入文件)")


if __name__ == "__main__":
    main()
