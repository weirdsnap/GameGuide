#!/usr/bin/env python3
"""
采集 hollowknight.wiki（独立维基）的数据。

重点采集：敌人/Boss/物品/NPC 页面的 Infobox 结构化数据
（HP、伤害、掉落等数值信息）+ 页面描述文本。
"""

import re
import time
import json
from pathlib import Path
from urllib.parse import unquote
import subprocess
import sys

BASE_URL = "https://hollowknight.wiki"
DATA_DIR = Path(__file__).parent.parent / "data"

CATEGORIES = {
    "Areas_(Hollow_Knight)":       "area",
    "Bosses_(Hollow_Knight)":      "boss",
    "Enemies_(Hollow_Knight)":     "enemy",
    "Items_(Hollow_Knight)":       "item",
    "NPCs_(Hollow_Knight)":        "npc",
    "Points_of_Interest_(Hollow_Knight)": "poi",
    "Spells_and_Abilities_(Hollow_Knight)": "ability",
}

# 额外独立页面
EXTRA_PAGES = [
    "Damage_Values_and_Enemy_Health_(Hollow_Knight)",
    "Charms",
    "Nail",
    "Soul",
    "Dream_Nail",
    "Map_and_Quill_(Hollow_Knight)",
]


def curl(url: str, timeout: int = 15) -> str:
    """用 curl 抓取 URL（因为 requests 可能被防火墙限制）。"""
    result = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), url],
        capture_output=True, text=True, timeout=timeout + 5
    )
    return result.stdout


def fetch_category_pages(cat_name: str) -> list:
    """从分类页面提取所有页面名称。"""
    url = f"{BASE_URL}/w/Category:{cat_name}"
    html = curl(url)
    
    m = re.search(r'<div id="mw-pages">(.*?)</div>\s*</div>\s*<div class="printfooter"', html, re.DOTALL)
    if not m:
        print(f"  ⚠️ 无法解析分类 {cat_name}")
        return []
    
    section = m.group(1)
    links = re.findall(r'href="/w/([^"#]+)"', section)
    pages = []
    for l in links:
        decoded = unquote(l)
        if not any(decoded.startswith(p) for p in ['Category:', 'Hollow_Knight_Wiki:', 'Special:', 'File:', 'Template:']):
            pages.append(decoded)
    return sorted(set(pages))


def extract_infobox(raw_text: str) -> dict:
    """从 wikitext 中提取 HK Infobox 模板数据。"""
    infobox = {}
    
    # 匹配 {{HK Infobox XXX ... }}
    m = re.search(r'(\{\{HK\s+Infobox\s+\w+(?:[\s\S]*?)\n\}\})', raw_text, re.DOTALL)
    if not m:
        return infobox
    
    content = m.group(1)
    
    # 提取模板类型
    type_m = re.match(r'\{\{HK\s+Infobox\s+(\w+)', content)
    if type_m:
        infobox['template_type'] = type_m.group(1)
    
    # 提取字段
    # 字段格式: |fieldname = value
    fields = re.findall(r'^\|(\w+)\s*=\s*(.+?)(?=\n\||\n\})', content, re.MULTILINE | re.DOTALL)
    for key, val in fields:
        val = val.strip()
        # 清理 HTML 评论
        val = re.sub(r'<!--.*?-->', '', val, flags=re.DOTALL).strip()
        # 清理 gallery 标签
        val = re.sub(r'<gallery>.*?</gallery>', '', val, flags=re.DOTALL).strip()
        # 清理图片引用
        val = re.sub(r'\[\[File:[^\]]*\]\]', '', val).strip()
        val = re.sub(r'\[\[Image:[^\]]*\]\]', '', val).strip()
        # 清理多余空行
        val = re.sub(r'\n{2,}', '\n', val).strip()
        
        if val:  # 只保留非空字段
            infobox[key] = val
    
    return infobox


def clean_wikitext(wt: str) -> str:
    """将 wikitext 清洗为纯文本（移除模板调用，只保留内容）。"""
    text = wt
    
    # 移除注释
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    
    # 移除 ref 标签
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^>]*/>', '', text)
    
    # 移除 gallery 等特殊标签
    for tag in ['gallery', 'nowiki', 'pre', 'code', 'poem', 'includeonly', 'noinclude']:
        text = re.sub(f'<{tag}[^>]*>.*?</{tag}>', '', text, flags=re.DOTALL)
    
    # 移除文件/图片引用
    text = re.sub(r'\[\[(?:File|Image):[^\]]*\]\]', '', text)
    
    # 移除分类
    text = re.sub(r'\[\[Category:[^\]]*\]\]', '', text)
    
    # 移除语言链接
    text = re.sub(r'\[\[[a-z]{2,3}:[^\]]*\]\]', '', text)
    
    # 处理表格
    lines = text.split('\n')
    cleaned = []
    in_table = False
    for line in lines:
        s = line.strip()
        if s.startswith('{|'):
            in_table = True
            continue
        if s == '|}':
            in_table = False
            continue
        if in_table:
            if s.startswith('|') or s.startswith('!'):
                content = s.lstrip('|!-+').strip()
                if content:
                    cleaned.append(content)
            continue
        cleaned.append(line)
    text = '\n'.join(cleaned)
    
    # 移除模板调用 {{...}}
    for _ in range(50):
        new_text, n = re.subn(r'\{\{[^{}]*?\}\}', '', text)
        if n == 0:
            break
        text = new_text
    # 清理残留的 {{ }}
    text = re.sub(r'[\{\}]', '', text)
    
    # 处理链接 [[target|display]] → display 或 [[target]] → target
    text = re.sub(r'\[\[([^\]|]*?)\|([^\]]*?)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]*?)\]\]', r'\1', text)
    
    # 处理外部链接 [url text] → text
    text = re.sub(r'\[https?://[^\s\[\]]+\s+([^\]]+)\]', r'\1', text)
    text = re.sub(r'\[https?://[^\s\[\]]+\]', '', text)
    
    # HTML 标签清理
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(div|span|center|small|big|sup|sub|u|s|strike|abbr|tt|code|blockquote|cite|table|tr|td|th|tbody|thead|caption|colgroup|col)[^>]*>', '', text, flags=re.IGNORECASE)
    
    # wikitext 标记
    text = re.sub(r"'''(.*?)'''", r'\1', text)
    text = re.sub(r"''(.*?)''", r'\1', text)
    text = re.sub(r'<h[23456][^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</h[23456]>', '', text, flags=re.IGNORECASE)
    
    # 清理空行过多
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # 标题转换 ===== → ##
    text = re.sub(r'^={2,6}\s*(.*?)\s*={2,6}\s*$',
                  lambda m: '## ' + m.group(1).strip(),
                  text, flags=re.MULTILINE)
    
    return text.strip()


def format_infobox(infobox: dict) -> str:
    """将 infobox 格式化为 YAML-like 块。"""
    if not infobox:
        return ""
    
    lines = []
    template_type = infobox.pop('template_type', 'Unknown')
    lines.append(f"【{template_type} 数据框】")
    
    # 最重要的字段排在前面
    priority_keys = ['health', 'drops', 'numbers_required', 'gender', 'area', 'connection']
    other_keys = [k for k in infobox if k not in priority_keys]
    sorted_keys = [k for k in priority_keys if k in infobox] + other_keys
    
    for key in sorted_keys:
        val = infobox[key]
        if key == 'drops':
            val = re.sub(r'\{\{G\|([^}]+)\}\}', r'\1 Geo', val)
            val = re.sub(r'<br\s*/?>', ', ', val)
        elif key == 'health':
            val = val.replace('<br/>', ' | ')
            val = re.sub(r'<br\s*/?>', ' | ', val)
        
        label = {
            'health': 'HP',
            'drops': '掉落',
            'numbers_required': '猎人日志要求击杀',
            'gender': '性别',
            'area': '区域',
            'connection': '连接区域',
            'va': '配音',
            'theme': '主题音乐',
        }.get(key, key)
        
        # 多行值缩进处理
        if '\n' in val:
            val_lines = val.split('\n')
            lines.append(f"  {label}:")
            for vl in val_lines:
                vl = vl.strip()
                if vl:
                    lines.append(f"    {vl}")
        else:
            # 太长就截断一行
            max_len = 200
            if len(val) > max_len:
                val = val[:max_len] + "..."
            lines.append(f"  {label}: {val}")
    
    return '\n'.join(lines)


def render_document(title: str, infobox: dict, description: str) -> str:
    """渲染为知识文档。"""
    if not description and not infobox:
        return None
    
    # 生成简短名称
    short_name = title.replace('_', ' ')
    short_name = re.sub(r'\s*\(.*?\)\s*', '', short_name).strip()
    
    slug = title.lower().replace(' ', '-').replace("'", "").replace("(", "").replace(")", "").replace(",", "")
    
    # 判断类别
    ttype = infobox.get('template_type', '').lower()
    type_map = {
        'enemy': 'enemies',
        'boss': 'bosses',
        'npc': 'npcs',
        'area': 'areas',
        'item': 'items',
        'ability': 'abilities',
    }
    category = type_map.get(ttype, 'guide')
    
    parts = [f"# 文档：{short_name} ({slug})",
             "",
             f"- 类别：{category}",
             f"- 标识：{slug}",
             f"- 来源：hollowknight.wiki/{title.replace(' ', '_')}",
             ""]
    
    # 数据框
    if infobox:
        info_text = format_infobox(infobox)
        if info_text:
            parts.append(info_text)
            parts.append("")
    
    # 描述文本
    if description:
        # 截取前 2000 字
        desc = description[:2000]
        if len(description) > 2000:
            desc += "\n\n...（内容截断）"
        parts.append(desc)
    
    return '\n'.join(parts)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--resume', type=str, help='续传文件路径')
    args = parser.parse_args()
    
    # 获取所有分类页面
    all_pages = {}
    all_page_names = []
    
    for cat_name, cat_type in CATEGORIES.items():
        print(f'\n📂 获取分类 {cat_name}...')
        pages = fetch_category_pages(cat_name)
        print(f'   共 {len(pages)} 页')
        for p in pages:
            if p not in all_pages:
                all_pages[p] = cat_type
                all_page_names.append(p)
        time.sleep(1)
    
    # 添加额外页面
    for p in EXTRA_PAGES:
        if p not in all_pages:
            all_pages[p] = 'guide'
            all_page_names.append(p)
    
    print(f'\n📊 总计 {len(all_page_names)} 个页面待采集')
    print(f'   Enemy: {sum(1 for v in all_pages.values() if v == "enemy")}')
    print(f'   Boss: {sum(1 for v in all_pages.values() if v == "boss")}')
    print(f'   NPC: {sum(1 for v in all_pages.values() if v == "npc")}')
    print(f'   Item: {sum(1 for v in all_pages.values() if v == "item")}')
    print(f'   Area: {sum(1 for v in all_pages.values() if v == "area")}')
    print(f'   Ability: {sum(1 for v in all_pages.values() if v == "ability")}')
    print(f'   POI: {sum(1 for v in all_pages.values() if v == "poi")}')
    print(f'   Guide: {sum(1 for v in all_pages.values() if v == "guide")}')
    
    if args.dry_run:
        return
    
    # 续传支持
    done_titles = set()
    if args.resume and Path(args.resume).exists():
        content = Path(args.resume).read_text(encoding='utf-8')
        docs = re.split(r'(?=^#\s*文档)', content, flags=re.MULTILINE)
        for d in docs:
            m = re.search(r'# 文档：(.+?)\s*\(', d)
            if m:
                done_titles.add(m.group(1).strip())
        print(f'续传模式：已存在 {len(done_titles)} 篇文档')
    
    output_path = DATA_DIR / "indie_wiki_data.md"
    
    # 记录日志
    stats = {'success': 0, 'skip': 0, 'fail': 0, 'empty': 0}
    
    documents = []
    for i, title in enumerate(all_page_names):
        # 跳过已有
        short_name = title.replace('_', ' ')
        short_name_clean = re.sub(r'\s*\(.*?\)\s*', '', short_name).strip()
        if short_name_clean in done_titles or short_name in done_titles:
            stats['skip'] += 1
            continue
        
        url = f"{BASE_URL}/w/{title}?action=raw"
        raw = curl(url)
        
        if not raw or len(raw) < 50:
            print(f'  [{i+1}/{len(all_page_names)}] ⚠️ {title} (空内容)')
            stats['empty'] += 1
            time.sleep(0.3)
            continue
        
        # 提取 infobox
        infobox = extract_infobox(raw)
        
        # 清洗描述文本（移除 infobox 部分）
        desc = raw
        if infobox:
            m = re.search(r'(\{\{HK\s+Infobox\s+\w+(?:[\s\S]*?)\n\}\})', desc, re.DOTALL)
            if m:
                desc = desc.replace(m.group(1), '')
        
        # 移除导航模板
        desc = re.sub(r'\{\{Navbar[^}]*\}\}', '', desc)
        desc = re.sub(r'\{\{HK Nav[^}]*\}\}', '', desc)
        
        description = clean_wikitext(desc)
        
        # 生成文档
        doc = render_document(title, infobox, description)
        if doc:
            documents.append(doc)
            stats['success'] += 1
            if stats['success'] % 20 == 0:
                print(f'  [{i+1}/{len(all_page_names)}] ✅ 已采集 {stats["success"]} 篇...')
        else:
            stats['empty'] += 1
            print(f'  [{i+1}/{len(all_page_names)}] ⏭️ {title} (跳过，内容不足)')
        
        # 礼貌延迟
        time.sleep(0.5)
    
    print(f'\n📊 采集完成：')
    print(f'   ✅ 成功: {stats["success"]}')
    print(f'   ⏭️ 跳过(已有): {stats["skip"]}')
    print(f'   ⚠️ 空内容: {stats["empty"]}')
    print(f'   ❌ 失败: {stats["fail"]}')
    
    # 保存
    if not documents:
        print('没有新文档需要保存。')
        return
    
    # 追加到文件
    header = (
        "# 独立维基数据\n\n"
        f"> 来源：https://hollowknight.wiki\n"
        f"> 采集时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"> 文档总数：{len(documents)}\n\n"
    )
    
    new_content = header + "\n\n---\n\n".join(documents) + "\n"
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f'\n✅ 已保存到 {output_path}')
    print(f'📊 共 {len(documents)} 篇新文档')


if __name__ == '__main__':
    main()
