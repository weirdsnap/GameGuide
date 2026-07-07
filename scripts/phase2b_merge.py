#!/usr/bin/env python3
"""
Phase 2b: 实体合并与整合（改进版）

核心改动 vs Phase 2 原版:
1. related_entities 不做 LLM 生成 → 直接取 Phase 1 分析的 UNION
2. description/summary 由 LLM 合并（已有 Phase 1 分析结果作为上下文）
3. post-processing: 反向关联补全 + 位置交叉验证

输出: data/phase2_beta.jsonl
"""

import json, re, time, urllib.request
from pathlib import Path
from collections import defaultdict, Counter

DATA_DIR = Path(__file__).parent.parent / "data"
PHASE1_FILE = DATA_DIR / "phase1_results.jsonl"
OUTPUT_FILE = DATA_DIR / "phase2_beta.jsonl"
LOG_FILE = DATA_DIR / "phase2b_merge.log"

API_KEY = "sk-67ee213b42df477dbe204035222bcc5a"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

log_lines = []


def log(msg):
    print(msg)
    log_lines.append(msg)


# ========= 1. 加载数据 =========

def load_phase1(path: Path) -> list[dict]:
    """加载 Phase 1 分析结果"""
    docs = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                if 'error' not in d:
                    docs.append(d)
    log(f"  Phase 1 分析结果: {len(docs)} 条")
    return docs


def load_hallownest_api(data_dir: Path) -> dict:
    """加载 HallownestAPI 结构化数据"""
    api_data = {}
    for subdir in ['areas', 'bosses', 'characters', 'charms', 'skills']:
        path = data_dir / subdir
        if not path.exists():
            continue
        for fpath in sorted(path.glob("*.json")):
            if fpath.name.startswith('_'):
                continue
            try:
                with open(fpath) as f:
                    data = json.load(f)
                name = data.get('name', data.get('slug', fpath.stem)).lower()
                data['_source_dir'] = subdir
                api_data[name] = data
            except Exception as e:
                log(f"  ⚠️ 加载 {fpath.name} 失败: {e}")
    return api_data


# ========= 2. 分组 =========

def normalize_name(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r'[_-]', ' ', n)
    n = re.sub(r'\s+', ' ', n)
    return n.strip()


def group_entities(docs: list[dict]) -> dict:
    """按实体名称分组，返回 {key: [doc, ...]}"""
    groups = defaultdict(list)
    seen = set()
    skip_patterns = ['areas ', 'bosses ', 'charms ', 'enemies ', 'npcs ',
                     'items ', 'skills ', 'abilities ', 'spells ', 'locations ',
                     'guides ', 'walkthrough']

    for d in docs:
        title = d.get('title', '').lower()
        if any(title.startswith(p) for p in skip_patterns):
            continue

        name = d.get('title_en', d.get('title', d.get('id', '')))
        key = normalize_name(name)
        source = d.get('source', '?')

        dedup_key = (key, source)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        groups[key].append(d)

    return dict(groups)


# ========= 3. 机械合并 related_entities =========

def merge_related_entities(docs: list[dict]) -> list[dict]:
    """取所有来源的 related_entities 的并集（按 name+relation 去重）"""
    seen = set()
    merged = []
    for d in docs:
        for rel in d.get('related_entities', []):
            key = (rel.get('name', ''), rel.get('relation', ''))
            if key not in seen:
                seen.add(key)
                merged.append(rel)
    return merged


def merge_keywords(docs: list[dict]) -> list[str]:
    """取所有来源的 keywords 的并集"""
    seen = set()
    merged = []
    for d in docs:
        for kw in d.get('keywords', []):
            if kw not in seen:
                seen.add(kw)
                merged.append(kw)
    return merged


def merge_meta(docs: list[dict]) -> dict:
    """合并元数据（category, spoiler_level, title）"""
    # category: 取出现最多的
    cats = Counter(d.get('category', '?') for d in docs)
    category = cats.most_common(1)[0][0]

    # spoiler_level: 取最高
    levels = {'early': 0, 'mid': 1, 'late': 2, 'endgame': 3}
    max_level = max(d.get('spoiler_level', 'early') for d in docs)
    spoiler_level = max_level

    # title: 取中文最长的
    titles_cn = [d.get('title', '') for d in docs if d.get('title', '')]
    title = max(titles_cn, key=len) if titles_cn else docs[0].get('title', '?')

    # title_en: 取非空第一个
    title_en = next((d.get('title_en', '') for d in docs if d.get('title_en')), '')

    return {
        'category': category,
        'spoiler_level': spoiler_level,
        'title': title,
        'title_en': title_en
    }


# ========= 4. LLM 合并描述内容 =========

MERGE_PROMPT = """你是空洞骑士(Hollow Knight)维基数据整合专家。合并同一实体的多来源信息为一条结构化数据。

输入的每个来源都已由AI分析过，包含：标题、分类、概要、关键词、位置描述、关联实体等。
你的任务是将多个来源的信息整合为一条精炼、准确的实体数据。

输出 JSON（只输出 JSON，不要其他文字）：
{
    "title": "实体名（中文优先）",
    "title_en": "英文唯一标识",
    "category": "区域|Boss|敌人|角色|护符|道具|技能|剧情|任务|机制|引导",
    "summary": "2-4句中文概述，综合所有来源信息",
    "keywords": ["关键词1", "关键词2", ...],
    "description": "详细描述，综合来源的具体信息",
    "stats": {"血量": "xxx", "伤害": "xxx", "掉落": "xxx", ...} 或 null,
    "location": "获取位置/所在区域/出现地点（最准确的一条）",
    "spoiler_level": "early|mid|late|endgame"
}

剧透等级规则（严格遵循）：
- early: 泪城前可接触（遗忘十字路、苍绿之径、真菌荒野、灵魂圣殿、泪水之城外层）
- mid: 需要梦钉或中级能力（水晶山峰、皇家水道、深邃巢穴、呼啸悬崖、三个梦魇守卫）
- late: 需要暗影披风/王之徽章（深渊、白色宫殿、王后花园、王国边缘）
- endgame: 终局/隐藏（神居、痛苦之路、真结局、虚空之心相关）

关键：只整合文本描述内容。关联实体和关键词我会单独合并。"""


def merge_content_with_llm(docs: list[dict]) -> dict:
    """用 LLM 合并描述内容（summary, description, location, stats）"""
    inputs = []
    for i, d in enumerate(docs):
        source = d.get('source', '?')
        inputs.append(
            f"--- 来源{i+1} ({source}) ---\n"
            f"标题: {d.get('title', '?')} / {d.get('title_en', '')}\n"
            f"分类: {d.get('category', '?')}\n"
            f"摘要: {d.get('summary', '')[:500]}\n"
            f"关键词: {d.get('keywords', [])[:5]}\n"
            f"关联实体: {[r.get('name','') for r in d.get('related_entities', [])[:8]]}\n"
            f"剧透等级: {d.get('spoiler_level', 'early')}\n"
            f"位置: {d.get('location', '')}\n"
        )

    user_prompt = "合并以下同一实体的不同来源信息：\n\n" + "\n\n".join(inputs)

    for attempt in range(3):
        try:
            payload = json.dumps({
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": MERGE_PROMPT},
                    {"role": "user", "content": user_prompt[:4000]}
                ],
                "temperature": 0.1,
                "max_tokens": 800,
                "response_format": {"type": "json_object"}
            }).encode('utf-8')

            req = urllib.request.Request(API_URL, data=payload, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}"
            })
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(json.loads(resp.read())['choices'][0]['message']['content'])

            # 确保必要字段
            for field in ['title', 'category', 'summary', 'spoiler_level']:
                if field not in result:
                    result[field] = docs[0].get(field, '')
            if 'keywords' not in result:
                result['keywords'] = []
            if 'location' not in result:
                result['location'] = ''

            return result

        except Exception as e:
            if attempt < 2:
                time.sleep(2)
                continue
            # fallback
            log(f"  ⚠️ LLM 合并失败 ({str(e)[:50]})，使用第一条数据")
            d = docs[0]
            return {
                'title': d.get('title', '?'),
                'title_en': d.get('title_en', ''),
                'category': d.get('category', '?'),
                'summary': d.get('summary', ''),
                'keywords': d.get('keywords', []),
                'description': d.get('summary', ''),
                'stats': None,
                'location': d.get('location', ''),
                'spoiler_level': d.get('spoiler_level', 'early')
            }


# ========= 5. 反向关联补全 =========

def build_reverse_links(docs: list[dict]) -> list[dict]:
    """为每个实体补全被引用关系"""
    # 索引: name → entity
    name_to_key = {}
    for d in docs:
        name_to_key[d['title']] = d['_entity_key']
        if d.get('title_en'):
            name_to_key[d['title_en'].lower()] = d['_entity_key']
            name_to_key[d['title_en'].lower().replace('-', ' ')] = d['_entity_key']

    # 收集所有引用
    referrers = defaultdict(list)
    for d in docs:
        for rel in d.get('related_entities', []):
            ref_name = rel.get('name', '')
            referrers[ref_name].append((d['title'], rel.get('relation', ''), d.get('category', '')))

    added = 0
    for d in docs:
        existing = {(r.get('name', ''), r.get('relation', '')) for r in d.get('related_entities', [])}
        title_data = d['title']

        new_rels = []
        for ref_name, refs in referrers.items():
            # 检查引用名是否指向当前实体
            if ref_name == d['title'] or ref_name == d.get('title_en', ''):
                for ref_title, relation, ref_cat in refs:
                    pair = (ref_title, f"被{relation}")
                    if pair not in existing and ref_title != d['title']:
                        existing.add(pair)
                        new_rels.append({
                            "name": ref_title,
                            "relation": f"被{relation}",
                            "category": ref_cat
                        })

        if new_rels:
            d.setdefault('related_entities', []).extend(new_rels)
            added += len(new_rels)

    log(f"\n  补反向关联: 添加 {added} 条")
    return docs


# ========= 6. 主流程 =========

def main():
    log("=" * 50)
    log("Phase 2b: 实体合并与整合（改进版）")
    log("=" * 50)

    # 1. 加载数据
    log("\n📂 加载 Phase 1 分析结果...")
    docs = load_phase1(PHASE1_FILE)
    if not docs:
        log("❌ 未加载到数据")
        return

    # 2. 加载 HallownestAPI
    log("\n📂 加载 HallownestAPI 数据...")
    api_data = load_hallownest_api(DATA_DIR)
    log(f"  HallownestAPI: {len(api_data)} 条")

    # 3. 分组
    log("\n🔗 按实体分组...")
    groups = group_entities(docs)
    size_dist = Counter(len(v) for v in groups.values())
    log(f"  总实体数: {len(groups)}")
    for size in sorted(size_dist):
        log(f"  {size}个来源: {size_dist[size]} 个实体")

    # 4. 合并
    log("\n🔄 开始合并...")
    results = []
    single_count = 0
    multi_count = 0

    for entity_key, entries in sorted(groups.items()):
        # --- 机械合并（不调用 LLM） ---
        related_entities = merge_related_entities(entries)
        keywords = merge_keywords(entries)
        meta = merge_meta(entries)

        if len(entries) == 1:
            # 单来源：直接用 Phase 1 数据
            e = entries[0]
            entity = {
                'title': meta['title'],
                'title_en': meta['title_en'],
                'category': meta['category'],
                'summary': e.get('summary', ''),
                'keywords': keywords,
                'description': '',
                'stats': None,
                'location': e.get('location', ''),
                'related_entities': related_entities,
                'spoiler_level': meta['spoiler_level']
            }
            single_count += 1
        else:
            # 多来源：LLM 合并描述内容，但 related_entities 直接用机械合并
            log(f"  🔄 {meta['title']} ({entity_key}) — {len(entries)}个来源")
            merged = merge_content_with_llm(entries)
            # 保留机械合并的关联实体（覆盖 LLM 生成的）
            entity = {
                'title': merged.get('title', meta['title']),
                'title_en': merged.get('title_en', meta['title_en']),
                'category': merged.get('category', meta['category']),
                'summary': merged.get('summary', ''),
                'keywords': list(set(keywords + merged.get('keywords', []))),
                'description': merged.get('description', ''),
                'stats': merged.get('stats', None),
                'location': merged.get('location', '') or meta.get('location', ''),
                'related_entities': related_entities,  # ← 关键：用机械合并结果！
                'spoiler_level': merged.get('spoiler_level', meta['spoiler_level'])
            }
            multi_count += 1
            time.sleep(0.3)  # API 限流

        # 补上 HallownestAPI 数据
        api_name = normalize_name(entity.get('title', ''))
        api_match = api_data.get(api_name)
        if not api_match:
            for k, v in api_data.items():
                if normalize_name(k) == api_name:
                    api_match = v
                    break
        entity['api_data'] = api_match if api_match else None
        entity['_entity_key'] = entity_key

        results.append(entity)

        if len(results) % 50 == 0:
            log(f"  ✅ {len(results)}/{len(groups)}")

    # 5. 补反向关联
    log("\n🔄 补反向关联...")
    results = build_reverse_links(results)

    # 6. 输出
    log(f"\n📝 写入 {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    # 7. 统计
    cats = Counter(r['category'] for r in results)
    spoilers = Counter(r['spoiler_level'] for r in results)
    no_rel = sum(1 for r in results if not r.get('related_entities'))
    loc_missing = sum(1 for r in results if r['category'] in ('护符', '道具', '技能', 'Boss', '敌人') and not r.get('location'))

    log(f"\n✅ 合并完成！")
    log(f"  总实体数: {len(results)}")
    log(f"  单来源: {single_count} | 多来源合并: {multi_count}")
    log(f"\n  分类分布:")
    for c, n in cats.most_common():
        log(f"    {c}: {n}")
    log(f"\n  剧透等级:")
    for l, n in spoilers.most_common():
        log(f"    {l}: {n}")
    log(f"\n  零关联实体: {no_rel}")
    log(f"  缺位置实体(应含): {loc_missing}")

    # 8. 写日志
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(log_lines))

    log(f"\n  日志: {LOG_FILE}")


if __name__ == '__main__':
    main()
