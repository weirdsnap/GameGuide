#!/usr/bin/env python3
"""
Phase 1: 单文档分析
对每篇文档调用 DeepSeek，提取：分类、总结、关键词、关联实体、剧透等级

输出：data/phase1_results.jsonl （每行一条分析结果）
支持断点续传
"""

import json, re, os, time, sys
from pathlib import Path
import urllib.request
import urllib.error

DATA_DIR = Path(__file__).parent.parent / "data"
API_KEY = "sk-67ee213b42df477dbe204035222bcc5a"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"
RATE_LIMIT = 5  # 每秒最多请求数
OUTPUT = DATA_DIR / "phase1_results.jsonl"

CATEGORIES = [
    "区域", "Boss", "敌人", "角色", "护符", "道具",
    "技能", "剧情", "任务", "机制", "引导"
]

CATEGORY_DESC = """
- 区域：地图、场景、地点（如 Ancient Basin, City of Tears）
- Boss：主要首领战（如 The Radiance, Hornet）
- 敌人：普通敌人（如 Crawlid, Aspid Hunter）
- 角色：NPC、友好角色（如 Cornifer, Hornet 作为NPC）
- 护符：护符装备（如 Wayward Compass, Shaman Stone）
- 道具：物品、收集品、资源（如 Geo, Pale Ore, Arcane Egg）
- 技能：法术、移动能力、剑技（如 Vengeful Spirit, Monarch Wings）
- 剧情：背景故事、传说、角色背景（如 The Infection, The Radiance 传说）
- 任务：支线任务、成就（如 Grimm Troupe, The Eternal Ordeal）
- 机制：游戏系统、数值（如 Soul, Damage Values, Nail 升级）
- 引导：攻略提示、流程指引
"""

SYSTEM_PROMPT = f"""你是空洞骑士(Hollow Knight)维基数据分析助手。你的任务是对每一篇文档进行分析。

可选的分类有：{', '.join(CATEGORIES)}

各分类说明：
{CATEGORY_DESC}

请对每篇文档输出 JSON 格式分析结果：
{{"title": "文档标题（中文优先）",
 "category": "最匹配的分类",
 "title_en": "英文标题或唯一标识",
 "summary": "2-3句中文总结",
 "keywords": ["核心关键词1", "核心关键词2", "核心关键词3"],
 "related_entities": [{{"name": "关联实体名", "relation": "关系描述", "category": "实体分类"}}],
 "spoiler_level": "early|mid|late|endgame",
 "contains_stats": true/false}}

注意：
- 如果文档内容不足（仅标题无正文），category 设为 "unknown"
- related_entities 列出文档中提到的其他空洞骑士相关实体（Boss名、区域名、角色名、护符名等）
- spoiler_level: early(前期) / mid(中期) / late(后期) / endgame(终局/隐藏)
- contains_stats: 是否包含数值数据（血量、伤害、掉落等）
- 只输出 JSON，不要加额外的文字"""


def parse_docs_fandom(filepath):
    """解析 Fandom 格式的文档"""
    text = filepath.read_text(encoding='utf-8')
    docs = []
    # 按 # 文档 分割
    chunks = re.split(r'(?=^# 文档 \[)', text, flags=re.MULTILINE)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        # 提取标题
        title_match = re.search(r'^# 文档 \[(\d+)\]\s*(.+?)$', chunk, re.MULTILINE)
        # 提取类别
        cat_match = re.search(r'^- 类别：(.+)$', chunk, re.MULTILINE)
        # 提取标识
        id_match = re.search(r'^- 标识：(.+)$', chunk, re.MULTILINE)
        
        title = title_match.group(2).strip() if title_match else "?"
        cat = cat_match.group(1).strip() if cat_match else "?"
        doc_id = id_match.group(1).strip() if id_match else f"fandom_{title_match.group(1) if title_match else '?'}"
        
        docs.append({
            'id': doc_id,
            'title': title,
            'category_hint': cat,
            'content': chunk,
            'source': 'fandom',
            'index': len(docs)
        })
    return docs


def parse_docs_indie(filepath):
    """解析独立维基格式的文档"""
    text = filepath.read_text(encoding='utf-8')
    docs = []
    chunks = re.split(r'(?=^# 文档：)', text, flags=re.MULTILINE)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        # 提取标题行: "# 文档：Ancient Basin (ancient_basin)"
        title_match = re.search(r'^# 文档：(.+?)\((.+?)\)\s*$', chunk, re.MULTILINE)
        # 提取类别
        cat_match = re.search(r'- 类别：(.+)$', chunk, re.MULTILINE)
        
        if title_match:
            title = title_match.group(1).strip()
            doc_id = title_match.group(2).strip()
        else:
            # 尝试其他格式
            title_match2 = re.search(r'^# 文档：(.+?)$', chunk, re.MULTILINE)
            title = title_match2.group(1).strip() if title_match2 else "?"
            doc_id = f"indie_{len(docs)}"
        
        cat = cat_match.group(1).strip() if cat_match else (re.search(r'【(.*?)数据框】', chunk).group(1) if re.search(r'【(.*?)数据框】', chunk) else '?')
        
        docs.append({
            'id': doc_id,
            'title': title,
            'category_hint': cat.lower() if cat else '?',
            'content': chunk,
            'source': 'indie',
            'index': len(docs)
        })
    return docs


def call_deepseek(doc_text, max_retries=3):
    """调用 DeepSeek API 分析文档"""
    prompt = f"请分析以下空洞骑士文档：\n\n{doc_text[:3000]}"
    
    for attempt in range(max_retries):
        try:
            data = json.dumps({
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 500,
                "response_format": {"type": "json_object"}
            }).encode('utf-8')
            
            req = urllib.request.Request(
                API_URL,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {API_KEY}"
                }
            )
            
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
            content = result['choices'][0]['message']['content']
            
            # 解析 JSON
            parsed = json.loads(content)
            return parsed
            
        except (json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"error": f"JSON parse error: {e}", "raw": content if 'content' in locals() else str(e)}
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Rate limited
                wait = 2 ** (attempt + 2)
                print(f"  ⚠️ 速率限制，等待{wait}秒...")
                time.sleep(wait)
                continue
            elif e.code == 400:
                body = e.read().decode()
                return {"error": f"400: {body[:200]}"}
            else:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {"error": f"HTTP {e.code}: {str(e)[:200]}"}
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"error": str(e)[:200]}


def already_processed(output_path):
    """检查已处理的结果"""
    processed_ids = set()
    if output_path.exists():
        with open(output_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        d = json.loads(line)
                        processed_ids.add(d.get('id', ''))
                    except:
                        pass
    return processed_ids


def main():
    print("📖 加载文档...")
    
    # 解析 Fandom 数据
    fandom_path = DATA_DIR / "hallownest_knowledge.md"
    fandom_docs = parse_docs_fandom(fandom_path) if fandom_path.exists() else []
    print(f"  Fandom: {len(fandom_docs)} 篇")
    
    # 解析独立维基数据
    indie_path = DATA_DIR / "indie_wiki_data.md"
    indie_docs = parse_docs_indie(indie_path) if indie_path.exists() else []
    print(f"  独立维基: {len(indie_docs)} 篇")
    
    # wiki_data.md 是早期处理版，内容已包含在 hallownest_knowledge.md 中，跳过避免重复
    
    all_docs = fandom_docs + indie_docs
    print(f"\n📊 共 {len(all_docs)} 篇文档待分析")
    
    # 检查断点
    processed = already_processed(OUTPUT)
    print(f"  已处理: {len(processed)} 篇")
    print(f"  还需处理: {len(all_docs) - len(processed)} 篇")
    
    remaining = [d for d in all_docs if d['id'] not in processed]
    
    if not remaining:
        print("✅ 全部已处理完毕！")
        return
    
    # 开始处理
    total = len(remaining)
    start_time = time.time()
    success = 0
    fail = 0
    
    for i, doc in enumerate(remaining, 1):
        print(f"\n[{i}/{total}] {doc['source']}: {doc['title']} ({doc['id']})")
        
        result = call_deepseek(doc['content'][:3000])
        
        result['id'] = doc['id']
        result['title'] = doc['title']
        result['doc_index'] = doc['index']
        result['source'] = doc['source']
        result['content_snippet'] = doc['content'][:200]
        
        # 写入结果
        with open(OUTPUT, 'a', encoding='utf-8') as f:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')
        
        if 'error' in result:
            fail += 1
            print(f"  ❌ {result['error'][:80]}")
        else:
            success += 1
            print(f"  ✅ {result.get('category', '?')} | 关键词: {result.get('keywords', [])[:3]}")
        
        # 速率控制：每次请求间隔0.3秒
        time.sleep(0.3)
        # 每10篇报告一次进度
        if i % 10 == 0:
            elapsed = time.time() - start_time
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0
            print(f"  📊 进度: {i}/{total} | 成功率: {success}/{success+fail} | {rate:.1f}条/秒 | ETA: {eta:.0f}秒")
    
    elapsed = time.time() - start_time
    print(f"\n{'='*50}")
    print(f"✅ Phase 1 完成!")
    print(f"  处理: {total} 篇")
    print(f"  成功: {success}")
    print(f"  失败: {fail}")
    print(f"  耗时: {elapsed:.1f}秒")
    print(f"  输出: {OUTPUT}")


if __name__ == '__main__':
    main()
