#!/usr/bin/env python3
"""
采集 hollowknight.wiki 上错过的护符独立页面。
每个护符页面有 Notch cost、效果、位置等信息。
"""

import re, time, subprocess
from pathlib import Path

BASE_URL = "https://hollowknight.wiki"
DATA_DIR = Path(__file__).parent.parent / "data"

CHARMS = [
    "Wayward_Compass", "Gathering_Swarm", "Stalwart_Shell", "Soul_Catcher", "Shaman_Stone",
    "Soul_Eater", "Dashmaster", "Sprintmaster", "Grubsong", "Grubberfly%27s_Elegy",
    "Hiveblood", "Quick_Focus", "Deep_Focus", "Lifeblood_Heart", "Lifeblood_Core",
    "Joni%27s_Blessing", "Mark_of_Pride", "Fury_of_the_Fallen", "Steady_Body", "Heavy_Blow",
    "Sharp_Shadow", "Spell_Twister", "Thorns_of_Agony", "Baldur_Shell", "Flukenest",
    "Dream_Wielder", "Dreamshield", "Longnail", "Quick_Slash", "Shape_of_Unn",
    "Spore_Shroom", "Weaversong", "Kingsoul", "Void_Heart", "Defender%27s_Crest",
    "Fragile_Greed", "Fragile_Heart", "Fragile_Strength", "Glowing_Womb", "Grimmchild",
    "Carefree_Melody", "Nailmaster%27s_Glory",
]


def curl(url):
    result = subprocess.run(["curl", "-s", "--max-time", "10", url],
                            capture_output=True, text=True, timeout=15)
    return result.stdout


def extract_infobox(raw_text):
    """Extract Infobox Charm template data."""
    data = {}
    m = re.search(r'(\{\{HK\s+Infobox\s+Charm[\s\S]*?\n\}\})', raw_text, re.DOTALL)
    if not m:
        # Try alternative template names
        m = re.search(r'(\{\{(?:HK\s+)?(?:Infobox\s+)?Charm[\s\S]*?\n\}\})', raw_text, re.DOTALL)
    if not m:
        return data
    
    content = m.group(1)
    data['template_type'] = 'Charm'
    
    fields = re.findall(r'^\|(\w+)\s*=\s*(.+?)(?=\n\||\n\})', content, re.MULTILINE | re.DOTALL)
    for key, val in fields:
        val = val.strip()
        val = re.sub(r'<!--.*?-->', '', val, flags=re.DOTALL).strip()
        val = re.sub(r'<gallery>.*?</gallery>', '', val, flags=re.DOTALL).strip()
        val = re.sub(r'<br\s*/?>', ', ', val, flags=re.IGNORECASE)
        val = re.sub(r'\n{2,}', '\n', val).strip()
        if val:
            data[key] = val
    return data


def clean_wikitext(wt):
    """Clean wikitext to plain text."""
    text = wt
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^>]*/>', '', text)
    text = re.sub(r'<gallery>.*?</gallery>', '', text, flags=re.DOTALL)
    text = re.sub(r'\[\[(?:File|Image):[^\]]*\]\]', '', text)
    text = re.sub(r'\[\[Category:[^\]]*\]\]', '', text)
    
    # Remove templates
    for _ in range(30):
        new_text, n = re.subn(r'\{\{[^{}]*?\}\}', '', text)
        if n == 0:
            break
        text = new_text
    text = re.sub(r'[\{\}]', '', text)
    
    text = re.sub(r'\[\[([^\]|]*?)\|([^\]]*?)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]*?)\]\]', r'\1', text)
    text = re.sub(r"'''(.*?)'''", r'\1', text)
    text = re.sub(r"''(.*?)''", r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


def format_data(infobox, description):
    """Format as structured entry."""
    parts = []
    
    if infobox:
        parts.append("【Charm 数据框】")
        priority = ['notch_cost', 'effect', 'location', 'essence_cost', 'geo_cost', 'dlc']
        other = [k for k in infobox if k not in priority + ['template_type']]
        
        labels = {
            'notch_cost': '槽位消耗',
            'effect': '效果',
            'location': '获取位置',
            'essence': '精华',
            'essence_cost': '精华消耗',
            'geo_cost': 'Geo消耗',
            'dlc': 'DLC',
            'id': 'ID',
            'image1': '图片',
        }
        
        for key in priority + other:
            if key not in infobox:
                continue
            val = infobox[key]
            label = labels.get(key, key)
            
            # Clean HTML
            val = re.sub(r'<[^>]+>', '', val).strip()
            if not val:
                continue
                
            if len(val) > 300:
                val = val[:300] + "..."
            parts.append(f"  {label}: {val}")
    
    if description:
        if parts:
            parts.append("")
        parts.append(description[:1500])
    
    return '\n'.join(parts)


def main():
    print("📦 采集护符独立页面...\n")
    
    docs = []
    for i, charm in enumerate(CHARMS, 1):
        import urllib.parse
        decoded = urllib.parse.unquote(charm)
        slug = decoded.lower().replace(" ", "-").replace("'", "").replace(",", "")
        
        url = f"{BASE_URL}/w/{charm}?action=raw"
        raw = curl(url)
        
        if not raw or len(raw) < 100:
            print(f"  [{i}/42] ❌ {decoded} — 获取失败")
            time.sleep(0.3)
            continue
        
        infobox = extract_infobox(raw)
        
        desc = raw
        m = re.search(r'(\{\{(?:HK\s+)?(?:Infobox\s+)?Charm[\s\S]*?\n\}\})', desc, re.DOTALL)
        if m:
            desc = desc.replace(m.group(1), '')
        
        description = clean_wikitext(desc)
        
        parts = [
            f"# 文档：{decoded} ({slug})",
            "",
            "- 类别：charms",
            f"- 标识：{slug}",
            f"- 来源：hollowknight.wiki/{charm}",
            "",
            format_data(infobox, description),
        ]
        
        docs.append('\n'.join(parts))
        print(f"  [{i}/42] ✅ {decoded}")
        time.sleep(0.4)
    
    if not docs:
        print("没有采集到任何护符数据")
        return
    
    # 追加到独立维基数据文件
    output = DATA_DIR / "indie_wiki_data.md"
    
    # 读取现有内容
    existing = ""
    if output.exists():
        existing = output.read_text(encoding='utf-8')
    
    # 在文档数统计中加上护符
    new_content = "\n\n---\n\n" + "\n\n---\n\n".join(docs) + "\n"
    
    with open(output, 'a', encoding='utf-8') as f:
        f.write(new_content)
    
    # 更新文档数
    total_before = len(re.findall(r'^# 文档：', existing, re.MULTILINE))
    
    print(f"\n✅ 完成！新增 {len(docs)} 个护符文档")
    print(f"📁 已追加到 {output}")


if __name__ == '__main__':
    main()
