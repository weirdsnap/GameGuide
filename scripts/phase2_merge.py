#!/usr/bin/env python3
"""
Phase 2: 实体合并与整合
1. 补召回 Phase 1 失败的文档
2. 按实体分组（Fandom + 独立维基 + HallownestAPI）
3. LLM 合并为一条结构化数据
4. 输出为 RAG 友好格式

输出: data/phase2_merged.jsonl
输出分类摘要: data/phase2_categories.json
"""

import json, re, time, sys, urllib.request, os
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data"
API_KEY = "sk-67ee213b42df477dbe204035222bcc5a"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

# ========= 1. 加载数据 =========

def load_phase1(path):
    """加载 Phase 1 结果。用列表保留所有记录，让后续分组做去重"""
    results, failures, seen_ids = [], [], set()
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            d = json.loads(line)
            if 'error' in d:
                failures.append(d)
            else:
                # 同 ID 只保留最后一条（覆盖旧的重试结果等）
                key = (d['id'], d.get('source', ''))
                results.append(d)
    return results, failures


def load_hallownest_api(data_dir):
    """加载所有 HallownestAPI JSON 数据"""
    api_data = {}
    api_dirs = ['areas', 'bosses', 'characters', 'charms', 'skills']
    
    for subdir in api_dirs:
        path = data_dir / subdir
        if not path.exists():
            continue
        for fpath in sorted(path.glob("*.json")):
            if fpath.name.startswith('_'):
                continue  # 跳过 _all.json
            try:
                with open(fpath) as f:
                    data = json.load(f)
                name = data.get('name', data.get('slug', fpath.stem)).lower()
                data['_source_dir'] = subdir
                api_data[name] = data
            except Exception as e:
                print(f"   ⚠️ 加载 {fpath.name} 失败: {e}")
    
    return api_data


# ========= 2. 按实体分组 =========

def normalize_name(name):
    n = name.lower().strip()
    n = re.sub(r'[_-]', ' ', n)
    n = re.sub(r'\s+', ' ', n)
    return n.strip()


def group_entities(results):
    """按实体名称分组，同一实体的不同来源合并（results 是列表）"""
    groups = defaultdict(list)
    skip_patterns = ['areas ', 'bosses ', 'charms ', 'enemies ', 'npcs ',
                     'items ', 'skills ', 'abilities ', 'spells ', 'locations ',
                     'guides ', 'walkthrough']
    seen = set()
    
    for res in results:
        doc_id = res.get('id', '?')
        title = res.get('title', '').lower()
        # 跳过维基列表/模板页面
        if any(title.startswith(p) for p in skip_patterns):
            continue
        
        name = res.get('title_en', res.get('title', doc_id))
        key = normalize_name(name)
        
        # 同一来源的同一实体去重
        source = res.get('source', '?')
        dedup_key = (key, source)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        
        groups[key].append({
            'source': source,
            'doc_id': doc_id,
            'title': res.get('title', '?'),
            'category': res.get('category', '?'),
            'summary': res.get('summary', ''),
            'keywords': res.get('keywords', []),
            'related_entities': res.get('related_entities', []),
            'spoiler_level': res.get('spoiler_level', 'early'),
            'content_snippet': res.get('content_snippet', '')
        })
    
    return dict(groups)


# ========= 3. 补召回失败文档 =========

RETRY_PROMPT = """你是空洞骑士维基数据分析助手。分析文档内容并输出 JSON：

{"title": "文档标题（中文优先）",
 "category": "区域|Boss|敌人|角色|护符|道具|技能|剧情|任务|机制|引导",
 "title_en": "英文标题或唯一标识",
 "summary": "2-3句中文总结",
 "keywords": ["核心关键词1", "核心关键词2", "核心关键词3"],
 "related_entities": [{"name": "关联实体名", "relation": "关系描述", "category": "实体分类"}],
 "spoiler_level": "early|mid|late|endgame"}

只输出 JSON。"""


def retry_failures(failures, output_path):
    """补召回调失败的文档（截短内容）"""
    if not failures:
        return 0
    
    print(f"\n🔄 补召回 {len(failures)} 篇失败文档...")
    success = 0
    
    for fail in failures:
        doc_id = fail.get('id', '?')
        snippet = fail.get('content_snippet', '')[:1200]
        if not snippet:
            continue
        
        try:
            data = json.dumps({
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": RETRY_PROMPT},
                    {"role": "user", "content": f"分析：\n\n{snippet}"}
                ],
                "temperature": 0.1,
                "max_tokens": 500,
                "response_format": {"type": "json_object"}
            }).encode('utf-8')
            
            req = urllib.request.Request(API_URL, data=data, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}"
            })
            resp = urllib.request.urlopen(req, timeout=30)
            parsed = json.loads(json.loads(resp.read())['choices'][0]['message']['content'])
            
            parsed['id'] = doc_id
            parsed['source'] = fail.get('source', '?')
            parsed['content_snippet'] = snippet
            
            with open(output_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(parsed, ensure_ascii=False) + '\n')
            success += 1
            print(f"  ✅ {doc_id}")
        except Exception as e:
            print(f"  ❌ {doc_id}: {str(e)[:60]}")
        
        time.sleep(0.5)
    
    return success


# ========= 4. LLM 合并 =========

MERGE_PROMPT = """你是空洞骑士(Hollow Knight)维基数据整合专家。合并同一实体的多来源信息为一条结构化数据。

输出 JSON：
{
    "title": "实体名的中文名（优先）或英文名",
    "title_en": "英文唯一标识",
    "category": "区域|Boss|敌人|角色|护符|道具|技能|剧情|任务|机制|引导",
    "summary": "中文概述，2-4句话",
    "keywords": ["关键词1", "关键词2", ...],
    "description": "详细描述（综合所有来源信息）",
    "stats": {"血量": "xxx", "伤害": "xxx", "掉落": "xxx", ...} 或 null,
    "location": "获取位置/所在区域/出现地点",
    "related_entities": [
        {"name": "关联实体名", "relation": "关系描述", "category": "实体分类"}
    ],
    "spoiler_level": "early|mid|late|endgame"
}

剧毒等级规则：
- early: 泪城之前可接触（十字路/苍绿/真菌/灵魂圣殿）
- mid: 需要梦钉或中级能力（水晶/水道/深巢上层/梦魇守卫）
- late: 需要暗影披风/王之徽章（深渊/白宫/王后花园/王国边缘）
- endgame: 终局/隐藏（神居/痛苦之路/真结局/虚空之心相关）

只输出 JSON。"""


def merge_entity(entries):
    """合并同一实体的多个来源"""
    if len(entries) == 1:
        # 单来源直接格式化
        e = entries[0]
        return {
            'title': e['title'],
            'title_en': e.get('title_en', e.get('doc_id', '')),
            'category': e.get('category', '?'),
            'summary': e.get('summary', ''),
            'keywords': e.get('keywords', []),
            'description': e.get('summary', ''),
            'stats': None,
            'location': '',
            'related_entities': e.get('related_entities', []),
            'spoiler_level': e.get('spoiler_level', 'early')
        }
    
    # 多来源：用 LLM 合并
    inputs = []
    for i, e in enumerate(entries):
        snippet = e.get('content_snippet', '')[:1000]
        inputs.append(f"--- 来源{i+1} ({e['source']}) 分类:{e.get('category', '?')} ---\n{snippet}")
    
    user_prompt = "合并以下同一实体的不同来源信息：\n\n" + "\n\n".join(inputs)
    
    for attempt in range(3):
        try:
            data = json.dumps({
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": MERGE_PROMPT},
                    {"role": "user", "content": user_prompt[:4000]}
                ],
                "temperature": 0.1,
                "max_tokens": 800,
                "response_format": {"type": "json_object"}
            }).encode('utf-8')
            
            req = urllib.request.Request(API_URL, data=data, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}"
            })
            resp = urllib.request.urlopen(req, timeout=30)
            parsed = json.loads(json.loads(resp.read())['choices'][0]['message']['content'])
            
            # 确保必要字段存在
            for field in ['title', 'category', 'summary', 'keywords', 'spoiler_level']:
                if field not in parsed:
                    parsed[field] = entries[0].get(field, '')
            
            return parsed
        except Exception:
            if attempt < 2:
                time.sleep(2)
                continue
            # fallback: 用第一条数据
            e = entries[0]
            return {
                'title': e['title'],
                'title_en': e.get('title_en', e.get('doc_id', '')),
                'category': e.get('category', '?'),
                'summary': e.get('summary', ''),
                'keywords': e.get('keywords', []),
                'description': e.get('summary', ''),
                'stats': None,
                'location': '',
                'related_entities': e.get('related_entities', []),
                'spoiler_level': e.get('spoiler_level', 'early')
            }


# ========= 5. 主流程 =========

def main():
    print("📊 Phase 2: 实体合并与整合")
    print("=" * 50)
    
    # 1. 加载数据
    phase1_path = DATA_DIR / "phase1_results.jsonl"
    results, failures = load_phase1(phase1_path)
    print(f"Phase 1: {len(results)} 条（含重复） + {len(failures)} 失败")
    
    # 2. 补召回
    if failures:
        n = retry_failures(failures, phase1_path)
        results, failures = load_phase1(phase1_path)
        print(f"补召回后: {len(results)} 条")
    
    # 3. 加载 HallownestAPI
    print("\n📂 加载 HallownestAPI 结构化数据...")
    api_data = load_hallownest_api(DATA_DIR)
    api_categories = defaultdict(int)
    for name, data in api_data.items():
        api_categories[data.get('_source_dir', '?')] += 1
    for cat, cnt in sorted(api_categories.items()):
        print(f"  {cat}: {cnt} 条")
    print(f"  总计: {len(api_data)} 条")
    
    # 4. 分组
    print("\n🔗 按实体分组...")
    groups = group_entities(results)
    group_sizes = defaultdict(int)
    for key, entries in groups.items():
        group_sizes[len(entries)] += 1
    
    print(f"总实体数: {len(groups)}")
    for size in sorted(group_sizes.keys()):
        print(f"  {size}个来源: {group_sizes[size]} 个实体")
    
    # 5. 合并
    merged_path = DATA_DIR / "phase2_merged.jsonl"
    existing = set()
    if merged_path.exists():
        with open(merged_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        d = json.loads(line)
                        existing.add(d.get('title_en', ''))
                    except:
                        pass
    
    print(f"\n🔄 开始合并（已有 {len(existing)} 个，还需合并 {len(groups) - len(existing)}）...")
    
    merged_count, single_count = 0, 0
    
    for entity_key, entries in sorted(groups.items()):
        en_name = entries[0].get('title_en', entries[0].get('doc_id', ''))
        if en_name in existing:
            continue
        
        # 合并
        merged = merge_entity(entries)
        
        # 补上 API 数据
        api_name = normalize_name(merged.get('title', ''))
        api_match = api_data.get(api_name)
        if not api_match:
            # 也试试直接匹配 slug
            for k, v in api_data.items():
                if normalize_name(k) == api_name:
                    api_match = v
                    break
        
        if api_match:
            merged['api_data'] = {k: v for k, v in api_match.items()
                                   if k not in ['description', 'summary']}
        else:
            merged['api_data'] = None
        
        merged['_entity_key'] = entity_key
        
        # 写入
        with open(merged_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(merged, ensure_ascii=False) + '\n')
        
        merged_count += 1
        if len(entries) > 1:
            pass  # 多来源合并计数
        else:
            single_count += 1
        
        if merged_count % 30 == 0:
            print(f"  🔄 {merged_count}/{len(groups)} 已合并...")
        
        if len(entries) > 1:
            time.sleep(0.3)  # API 控制
    
    # 6. 统计摘要
    print(f"\n\n📊 合并完成!")
    print(f"  总计: {merged_count} 个实体")
    print(f"  其中单来源: {single_count}")
    print(f"  多来源合并: {merged_count - single_count}")
    print(f"  输出: {merged_path}")
    
    # 分类统计
    cats = defaultdict(int)
    spoilers = defaultdict(int)
    with open(merged_path, 'r', encoding='utf-8') as f:
        for line in f:
            d = json.loads(line)
            cats[d.get('category', '?')] += 1
            spoilers[d.get('spoiler_level', '?')] += 1
    
    print("\n分类分布:")
    for c, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}")
    print("剧毒等级分布:")
    for l, n in sorted(spoilers.items(), key=lambda x: -x[1]):
        print(f"  {l}: {n}")
    
    stats = {'total': merged_count, 'categories': dict(cats), 'spoiler_levels': dict(spoilers)}
    with open(DATA_DIR / "phase2_stats.json", 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
