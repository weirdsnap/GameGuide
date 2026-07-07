#!/usr/bin/env python3
"""空洞骑士 Wiki 数据采集器。

从 Hollow Knight Fandom Wiki 的 MediaWiki API 获取内容，
清洗后转为自然语言文档，合并入知识库。

用法：
  python scripts/collect_wiki_data.py          # 全量采集
  python scripts/collect_wiki_data.py --dry-run  # 试跑（不写文件）
  python scripts/collect_wiki_data.py --list-categories   # 查看分类
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("❌ 需要 requests 库：pip install requests")
    sys.exit(1)

# ── 配置 ──
API_BASE = "https://hollowknight.fandom.com/api.php"
USER_AGENT = "GameGuideBot/1.0 (knowledge collector for RAG agent)"

OUTPUT_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = "wiki_data.md"  # 不同于 hallownest_knowledge.md

# 要采集的 Wiki 分类及其对应的文档类别
WANTED_CATEGORIES = {
    "Bosses_(Hollow_Knight)": "bosses",
    "Charms": "charms",
    "Spells_and_Abilities_(Hollow_Knight)": "abilities",
    "NPCs_(Hollow_Knight)": "characters",
    "Areas_(Hollow_Knight)": "areas",
    "Quests_(Hollow_Knight)": "quests",
    "Items_(Hollow_Knight)": "items",
    "Lore": "lore",
    "Enemies_(Hollow_Knight)": "enemies",
}

# 当 API 没返回期望分类时，手写的备选页面列表
FALLBACK_PAGES = {
        "bosses": [
        "False_Knight", "Hollow_Knight", "Radiance",
        "Mantis_Lords", "Soul_Master", "Dung_Defender",
        "Broken_Vessel", "Traitor_Lord", "Nosk",
        "Nightmare_King_Grimm", "Watcher_Knight",
        "The_Collector", "Hive_Knight", "Grey_Prince_Zote",
    ],
    "charms": [
        "Sharp_Shadow", "Quick_Slash", "Mark_of_Pride",
        "Shaman_Stone", "Unbreakable_Strength",
        "Void_Heart", "Dashmaster", "Wayward_Compass",
        "Spell_Twister", "Grubsong",
    ],
    "abilities": [
        "Mantis_Claw", "Monarch_Wings", "Crystal_Heart",
        "Desolate_Dive", "Howling_Wraiths", "Isma's_Tear",
        "Dream_Nail", "King's_Brand", "Nail",
        "Vengeful_Spirit",
    ],
        "characters": [
        "Knight", "Hornet", "Pale_King", "White_Lady",
        "Herrah_the_Beast", "Monomon_the_Teacher",
        "Lurien_the_Watcher", "Hollow_Knight",
        "Bardoon", "Mister_Mushroom", "Tiso",
        "Nailsmith", "Quirrel", "Cornifer", "Seer",
        "Zote", "Elder_Hu", "Bretta",
        "Last_Stag", "Grimm_Troupe_(Quest)", "Snail_Shaman",
    ],
    "areas": [
        "Forgotten_Crossroads", "City_of_Tears",
        "Greenpath", "Fungal_Wastes", "Deepnest",
        "Kingdom's_Edge", "Crystal_Peak",
        "Resting_Grounds", "Queen's_Gardens",
        "Royal_Waterways", "Ancient_Basin",
        "White_Palace", "The_Hive",
    ],
        "quests": [
        "Grubfather", "Delicate_Flower",
        "Grimm_Troupe", "Hunter's_Journal",
        "Colosseum_of_Fools",
    ],
    "items": [
        "Arcane_Egg", "City_Crest", "Collector's_Map",
        "Delicate_Flower", "Elegant_Key", "Godtuner",
        "Hallownest_Seal", "King's_Brand", "King's_Idol",
        "Love_Key", "Lumafly_Lantern", "Map_and_Quill",
        "Pale_Ore", "Rancid_Egg", "Shopkeeper's_Key",
        "Simple_Key", "Tram_Pass", "Wanderer's_Journal",
        "Mask_Shard", "Vessel_Fragment", "Salubra's_Blessing",
        "Hunter's_Mark",
    ],
    "lore": [
        "Abyss_Creature", "Ancient_Civilisation",
        "Dreamers", "Bees",
        "Five_Great_Knights", "Godseekers",
        "Hallownest", "Higher_Beings",
        "Infection", "Mantis_Tribe",
        "Pale_King", "Seals",
        "Snail_Shamans", "Vessels", "Void",
        "Wyrms", "Weavers",
        "Dream_Realm", "Moth_Tribe",
        "Soul_Sanctum's_Scholars", "Spider_Tribe",
        "Grimm_Troupe_(Lore)",
    ],
    "enemies": [
        "Aspid_Hunter", "Aspid_Mother", "Baldur",
        "Belfly", "Brooding_Mawlek", "Crawlid",
        "Crystal_Crawler", "Crystal_Guardian",
        "Crystal_Hunter", "Deephunter", "Dirtcarver",
        "Duranda", "Flukemong", "Great_Hopper",
        "Gruzzer", "Husk_Guard", "Husk_Warrior",
        "Leaping_Husk", "Moss_Knight", "Mosquito",
        "Obble", "Primal_Aspid", "Shielded_Fool",
        "Soul_Twister", "Stalking_Devout", "Tiktik",
        "Uoma", "Vengefly", "Vengefly_King",
        "Volt_Twister", "Winged_Fool", "Wandering_Husk",
        "Armoured_Squit", "Boofly", "Bluggsac",
        "Carver_Hatcher", "Charged_Lumafly",
        "Cowardly_Husk", "Crystallised_Husk",
        "Death_Loodle", "Durandoo", "Battle_Obble",
    ],
}

# ── Wikitext 清洗 ──

def clean_wikitext(wt: str) -> str:
    """将 Wiki 标记文本转为纯文本。"""
    text = wt

    # 1. 移除 HTML 注释
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # 2. 移除 <ref> 引用
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^>]*/>', '', text)

    # 3. 移除 <gallery>, <nowiki>, <pre>, <code> 等块级标签
    for tag in ['gallery', 'nowiki', 'pre', 'code', 'poem', 'includeonly', 'noinclude']:
        text = re.sub(f'<{tag}[^>]*>.*?</{tag}>', '', text, flags=re.DOTALL)

    # 4. 移除占位图标签
    text = re.sub(r'\[\[File:[^\]]*\]\]', '', text)
    text = re.sub(r'\[\[Image:[^\]]*\]\]', '', text)

    # 5. 移除多媒体链接
    text = re.sub(r'\[\[Media:[^\]]*\]\]', '', text)

    # 6. 移除分类标签
    text = re.sub(r'\[\[Category:[^\]]*\]\]', '', text)

    # 7. 移除非英语的跨语言链接
    text = re.sub(r'\[\[[a-z]{2,3}:[^\]]*\]\]', '', text)

    # 8. 移除表格（截取表格内容但去掉符号）
    #    Wikitext 表格: {| ... |}  之间通常有大段文本
    #    保守处理：移除外层表格标记，保留内部文本行
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
            # 表格内的行，去掉 wikitext 标记但保留文字
            # |- 分割行, ! 表头, |  单元格
            if stripped.startswith('|') or stripped.startswith('!'):
                # 去掉开头的 | 或 !，保留文字
                content = stripped.lstrip('|!-').strip()
                if content:
                    cleaned_lines.append(content)
            continue
        cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines)

    # 9. 移除模板 {{...}}
    #    逐层展开嵌套模板
    #    安全措施：最多 100 轮，且如果一轮没替换掉任何东西则改用暴力清理
    _tmpl_loop = 0
    while '{{' in text:
        _tmpl_loop += 1
        if _tmpl_loop > 100:
            # 暴力清理剩余的 {{...}}（可能会误删嵌套内容里的括号，但总比死循环好）
            text = re.sub(r'{{|}}', '', text)
            break
        new_text, _n = re.subn(r'\{\{[^{}]*?\}\}', '', text)
        if _n == 0:
            # 正则无法匹配（可能嵌套了 { }），尝试逐层暴力剥离
            text = re.sub(r'{{|}}', '', text)
            break
        text = new_text
        if '{{' not in text:
            break

    # 10. 转换内部链接 [[Link|text]] → text；[[Link]] → Link
    text = re.sub(r'\[\[([^\]|]*?)\|([^\]]*?)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]*?)\]\]', r'\1', text)

    # 11. 转换外部链接 [url text] → text
    text = re.sub(r'\[https?://[^\s\[\]]+\s+([^\]]+)\]', r'\1', text)
    text = re.sub(r'\[https?://[^\s\[\]]+\]', '', text)

    # 12. 移除 <br>, <hr>, <div>, <span> 等内联标签及其属性
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<hr\s*/?>', '\n---\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(div|span|center|small|big|sup|sub|u|s|strike|abbr|tt|code|blockquote|cite|table|tr|td|th|tbody|thead|caption|colgroup|col)[^>]*>', '', text, flags=re.IGNORECASE)

    # 13. 加粗/斜体
    text = re.sub(r"'''(.*?)'''", r'\1', text)
    text = re.sub(r"''(.*?)''", r'\1', text)

    # 14. 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'^[ \t]+|[ \t]+$', '', text, flags=re.MULTILINE)

    # 15. 移除 <h2>, <h3> 等残余标签
    text = re.sub(r'</?h[23456][^>]*>', '', text, flags=re.IGNORECASE)

    return text.strip()


def wikitext_to_heading(text: str, level: int = 2) -> str:
    """将 wikitext 标题 ==h== 转为 markdown ## h"""
    return re.sub(r'^={2,6}\s*(.*?)\s*={2,6}\s*$',
                  lambda m: '#' * level + ' ' + m.group(1).strip(),
                  text, flags=re.MULTILINE)


def strip_list_markers(text: str) -> str:
    """处理列表标记 * # : ; 转为纯文本。"""
    text = re.sub(r'^[*#;:]+\s*', '', text, flags=re.MULTILINE)
    return text


# ── API 调用 ──

def api_call(params: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """调用 Fandom API。"""
    params['format'] = 'json'
    headers = {'User-Agent': USER_AGENT}
    try:
        resp = requests.get(API_BASE, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  ⚠️ API 请求失败: {e}")
        return None


def get_category_members(category: str, limit: int = 30) -> List[str]:
    """获取分类下部分页面标题。限制数量避免 API 压力。"""
    titles = []
    params = {
        'action': 'query',
        'list': 'categorymembers',
        'cmtitle': f'Category:{category}',
        'cmlimit': min(50, limit),
    }

    data = api_call(params)
    if not data:
        return titles

    for member in data.get('query', {}).get('categorymembers', []):
        if member.get('ns') == 0:  # 只取主命名空间
            titles.append(member['title'])

    return titles[:limit]


def get_page_wikitext(title: str) -> Optional[str]:
    """获取页面的 wikitext，自动跟随重定向。"""
    params = {
        'action': 'parse',
        'page': title,
        'prop': 'wikitext',
    }
    data = api_call(params)
    if not data:
        return None
    try:
        wt = data['parse']['wikitext']['*']
    except (KeyError, TypeError):
        return None

    # 跟随重定向
    if wt.strip().startswith('#REDIRECT'):
        m = re.search(r'\[\[([^\]]+)\]\]', wt)
        if m:
            target = m.group(1).split('|')[0].strip()
            if target != title:
                return get_page_wikitext(target)
    return wt


def get_pages_batch(titles: List[str]) -> Dict[str, str]:
    """批量获取多个页面的 wikitext，支持重定向跟随。"""
    if not titles:
        return {}

    result = {}
    batch_size = 20
    for batch_start in range(0, len(titles), batch_size):
        batch = list(set(titles[batch_start:batch_start + batch_size]))  # 去重
        params = {
            'action': 'query',
            'titles': '|'.join(batch),
            'redirects': '1',  # 自动跟随重定向
            'prop': 'revisions',
            'rvprop': 'content',
            'format': 'json',
        }
        data = api_call(params)
        if not data or 'query' not in data:
            continue

        query = data['query']

        # 构建原始标题 → 最终实际内容页的映射
        title_map = {}
        # 先加重定向
        if 'redirects' in query:
            for rd in query['redirects']:
                title_map[rd['from']] = rd['to']
        # 再加规范化（下划线→空格）
        if 'normalized' in query:
            for n in query['normalized']:
                if n['from'] not in title_map:
                    title_map[n['from']] = n['to']
        # 默认映射到自身
        for t in batch:
            if t not in title_map:
                title_map[t] = t

        # 收集内容
        pages = query.get('pages', {})
        content_by_title = {}
        for page_id, page_data in pages.items():
            if page_id == '-1':
                continue
            title = page_data.get('title', '')
            revisions = page_data.get('revisions', [])
            if revisions:
                content_by_title[title] = revisions[0].get('*', '')

        # 映射回原始标题（链式解析重定向）
        for orig in batch:
            target = orig
            seen = set()
            while target in title_map and target not in seen:
                seen.add(target)
                target = title_map[target]
            if target in content_by_title:
                result[orig] = content_by_title[target]

        time.sleep(1.5)  # 批次间隔，避免限速

    return result


# ── 文档生成 ──

def page_title_to_slug(title: str) -> str:
    """将 'Mantis Claw' → 'mantis-claw'"""
    return title.lower().replace(' ', '-').replace("'", '').replace('(', '').replace(')', '').replace(',', '')


def page_title_to_name(title: str) -> str:
    """将 'Hollow_Knight_(Boss)' → 'Hollow Knight'"""
    name = title.replace('_', ' ')
    # 去掉括号后缀但保留主要部分
    name = re.sub(r'\s*\(.*?\)\s*', '', name).strip()
    if not name:
        name = title.replace('_', ' ')
    return name


def render_document(title: str, category: str, wikitext: str) -> Optional[Dict[str, str]]:
    """将 wikitext 转为我们的文档格式。"""
    # 清洁文本
    text = clean_wikitext(wikitext)

    # 跳过太短的内容（可能是重定向页或 stub）
    if len(text) < 50:
        return None

    # 跳过重定向
    if text.startswith('#REDIRECT') or text.startswith('#redirect'):
        return None

    # 格式化标题行
    text = wikitext_to_heading(text)
    text = strip_list_markers(text)

    # 移除 __TOC__, __NOTOC__, __NOEDITSECTION__ 等行为控制
    text = re.sub(r'__\w+__', '', text)

    # 清理多余换行前缀
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


def collect_category(category_name: str, doc_category: str, limit: int = 10) -> List[Dict[str, str]]:
    """采集一个 Wiki 分类下的所有页面。"""
    print(f"\n📂 分类：{category_name} → {doc_category}")
    titles = get_category_members(category_name, limit=limit)
    print(f"  找到 {len(titles)} 个页面")

    docs = []
    for i, title in enumerate(titles, 1):
        # 跳过特定噪音页面
        if title.startswith('User:') or title.startswith('Template:') or title.startswith('File:'):
            continue
        # 跳过 Silksong 相关内容
        if 'Silksong' in title or title.endswith('(Silksong)'):
            continue

        print(f"  [{i}/{len(titles)}] {title}...", end=' ', flush=True)
        wt = get_page_wikitext(title)
        if not wt:
            print("⚠️ 无内容")
            continue

        doc = render_document(title, doc_category, wt)
        if doc:
            docs.append(doc)
            print(f"✅ {len(doc['text'])} chars")
        else:
            print("⏭️ 跳过（太短/重定向）")

        time.sleep(0.3)  # 礼貌间隔

    return docs


def collect_fallback(doc_category: str, pages: List[str]) -> List[Dict[str, str]]:
    """从备选列表批量采集页面。"""
    print(f"\n📂 备选：{doc_category} ({len(pages)} 篇)", end=' ', flush=True)
    all_wt = get_pages_batch(pages)
    print(f"获取到 {len(all_wt)}/{len(pages)} 篇", flush=True)

    docs = []
    for title, wt in all_wt.items():
        doc = render_document(title, doc_category, wt)
        if doc:
            docs.append(doc)
            print(f"  ✅ {doc['metadata']['title']}: {len(doc['text'])} chars", flush=True)

    return docs


def save_documents(all_docs: List[Dict[str, str]], output_path: Path):
    """将文档以 Markdown 格式保存。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 按类别分组
    by_cat: Dict[str, List[Dict[str, str]]] = {}
    for doc in all_docs:
        cat = doc['metadata']['category']
        by_cat.setdefault(cat, []).append(doc)

    lines = [
        "# Hollow Knight Wiki 数据",
        "",
        f"自动采集自 Hollow Knight Fandom Wiki",
        f"采集时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"文档总数：{len(all_docs)}",
        "",
        "---",
        "",
    ]

    for cat in ['abilities', 'charms', 'bosses', 'characters', 'areas', 'quests', 'items', 'lore', 'enemies']:
        docs = by_cat.get(cat, [])
        if not docs:
            continue
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
    print(f"\n✅ 已保存到 {output_path}（{len(all_docs)} 篇，{len(text)} chars）")


# ── 入口 ──

def list_categories():
    """列出所有 Wiki 分类。"""
    data = api_call({
        'action': 'query',
        'list': 'allcategories',
        'aclimit': 500,
    })
    if not data:
        return
    cats = [c['*'] for c in data['query']['allcategories']]
    for cat in sorted(cats):
        if 'Hollow Knight' in cat and 'Silksong' not in cat:
            print(f"  {cat}")
    print(f"\n共 {len(cats)} 个分类，显示的是含 'Hollow Knight' 的")


def main():
    parser = argparse.ArgumentParser(description="空洞骑士 Wiki 数据采集器")
    parser.add_argument("--dry-run", action="store_true", help="试跑（不写文件）")
    parser.add_argument("--list-categories", action="store_true", help="列出分类")
    parser.add_argument("--category", "-c", help="只跑指定分类（如 bosses）")
    args = parser.parse_args()

    if args.list_categories:
        list_categories()
        return

    # 采集
    all_docs: List[Dict[str, str]] = []

    # 策略：用手选备选页列表（更可靠、更快）
    for doc_cat, pages in FALLBACK_PAGES.items():
        if args.category and doc_cat != args.category:
            continue
        print(f"\n📂 {doc_cat}: {len(pages)} 篇")
        docs = collect_fallback(doc_cat, pages)
        all_docs.extend(docs)

    if not all_docs:
        print("\n❌ 没有采集到任何文档")
        sys.exit(1)

    # 按类别统计
    by_cat = {}
    for d in all_docs:
        cat = d['metadata']['category']
        by_cat[cat] = by_cat.get(cat, 0) + 1

    print("\n" + "=" * 50 + "\n")
    print("📊 采集结果：")
    for cat, count in sorted(by_cat.items()):
        print(f"  {cat}: {count} 篇")
    print(f"  总计: {len(all_docs)} 篇")
    total_chars = sum(len(d['text']) for d in all_docs)
    print(f"  总字数: ~{total_chars:,} chars")

    # 保存
    if not args.dry_run:
        output_path = OUTPUT_DIR / OUTPUT_FILE
        save_documents(all_docs, output_path)
    else:
        print("\n(dry-run 模式，未写入文件)")


if __name__ == "__main__":
    main()
