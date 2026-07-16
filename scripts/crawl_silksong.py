#!/usr/bin/env python3
"""Silksong Wiki 重新爬取 — 快速版"""
import json, os, re, sys, time, urllib.request, urllib.parse, threading
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

API_URL = "https://hollowknight.fandom.com/api.php"
HEADERS = {"User-Agent": "SilksongCrawler/4.0"}
DLY = 0.4

GAME_DIR = os.path.join(os.path.dirname(__file__), '..', 'games', 'silksong')
DATA_DIR = os.path.join(GAME_DIR, 'data')
WIKI_FILE = os.path.join(DATA_DIR, 'wiki_data.md')
os.makedirs(DATA_DIR, exist_ok=True)

def api(params):
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(f"{API_URL}?{qs}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def find_titles():
    found = set()
    
    # 策略1: 搜索 "Silksong" 全文
    print(f"  搜索 'Silksong' ...", end=" ", flush=True)
    try:
        data = api({"action":"query","list":"search","srsearch":"Silksong",
                     "srlimit":500,"format":"json"})
        for r in data.get("query",{}).get("search",[]):
            t = r["title"]
            if not re.match(r'(File|Template|Category|User|Thread):', t):
                found.add(t)
        print(f"{len(data['query']['search'])} 条")
    except Exception as e:
        print(f"失败: {e}")
    
    # 策略2: 按分类遍历
    cats = ["Silksong"]
    seen_cats = set()
    while cats:
        cat = cats.pop(0)
        if cat in seen_cats: continue
        seen_cats.add(cat)
        try:
            data = api({"action":"query","list":"categorymembers",
                        "cmtitle":f"Category:{cat}","cmlimit":"max",
                        "cmtype":"page|subcat","format":"json"})
            for m in data.get("query",{}).get("categorymembers",[]):
                t = m["title"]
                if m["type"] == "subcat" and t.replace("Category:","") not in seen_cats:
                    cats.append(t.replace("Category:",""))
                elif not re.match(r'(File|Template|Category|User|Thread):', t):
                    found.add(t)
        except:
            pass
        time.sleep(0.2)
    print(f"  分类遍历: {len(seen_cats)} 分类, {sum(1 for _ in [])}...")
    
    # 策略3: 关键词搜索
    for kw in ["Silksong area","Silksong enemy","Silksong boss",
               "Silksong character","Silksong item","Silksong weapon",
               "Silksong charm","Silksong skill","Silksong quest","Silksong lore"]:
        try:
            data = api({"action":"query","list":"search","srsearch":kw,
                        "srlimit":100,"format":"json"})
            for r in data.get("query",{}).get("search",[]):
                t = r["title"]
                if not re.match(r'(File|Template|Category|User|Thread):', t):
                    found.add(t)
        except:
            pass
        time.sleep(0.3)
    
    return sorted(found)

def fetch(title):
    data = api({"action":"parse","page":title,"prop":"text","format":"json","redirects":"1"})
    html = data["parse"]["text"]["*"]
    html = re.sub(r'<script[^>]*>.*?</script>','',html,flags=re.DOTALL)
    html = re.sub(r'<style[^>]*>.*?</style>','',html,flags=re.DOTALL)
    html = re.sub(r'<table class="infobox[^>]*>.*?</table>','',html,flags=re.DOTALL)
    text = re.sub(r'<[^>]+>',' ',html)
    text = re.sub(r'&[#a-zA-Z]+;',' ',text)
    text = re.sub(r'\s+',' ',text).strip()
    return text

def main():
    print(f"🐈 【丝之歌 Wiki 重爬】{datetime.now()}")
    
    print(f"\n1️⃣  查找页面...")
    titles = find_titles()
    print(f"   共 {len(titles)} 个页面")
    
    print(f"\n2️⃣  爬取内容...")
    ok, err, sections = 0, 0, []
    n = len(titles)
    for i, t in enumerate(titles, 1):
        try:
            txt = fetch(t)
            if len(txt) >= 80:
                sections.append(f"# {t}\n\n{txt}\n")
                ok += 1
            else:
                err += 1
        except:
            err += 1
        if i % 30 == 0:
            pct = int(i/n*100) if n else 100
            print(f"   {i}/{n} ({pct}%): ✓{ok} ✗{err}")
        time.sleep(DLY)
    
    print(f"\n3️⃣  写入 wiki_data.md...")
    header = f"# Silksong Wiki Data\n\n*爬取: {datetime.now()} | {ok} 页*\n\n"
    with open(WIKI_FILE, 'w', encoding='utf-8') as f:
        f.write(header + "".join(sections))
    kb = os.path.getsize(WIKI_FILE) / 1024
    print(f"   完成: {kb:.0f}K, {ok} 页")
    
    # 更新 DB
    print(f"\n4️⃣  数据库...")
    import sqlite3
    db = os.path.join(GAME_DIR, "silksong_data.db")
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    for t in [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        c = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"   {t}: {c}")
    conn.close()
    
    print(f"\n{'='*50}")
    print(f"✅ 完成: {ok} 页 / {kb:.0f}K")
    print(f"   下一步: Mac 上 python3 scripts/ingest_game.py --game silksong")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()
