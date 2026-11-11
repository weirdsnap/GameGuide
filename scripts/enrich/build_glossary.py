#!/usr/bin/env python3
"""
构建中英专名对照表（术语表）— 供跨语检索的 query 扩展使用。

思路：
  1. 从结构化 SQLite 的 name 字段抽出全部实体名；
  2. 规范化出「基名」（去引号、去括号/冒号变体），变体挂到基名下；
  3. 按 wiki 原文中的出现次数排序（出现多 = 用户更可能提到）；
  4. 批量调 LLM（DeepSeek）翻成中文，带 confidence；
  5. 输出 JSONL（可断点续跑）+ 汇总统计。

产物：games/<game>/data/<game>_glossary.jsonl
      {"name_en","base_en","kind","name_zh","conf","variants","model","ts"}

用法：
  # 先看看会抽哪些名字、抽多少（不调 API）
  python scripts/enrich/build_glossary.py --game baldurs_gate3 --limit 50 --dry-run

  # 抽样跑 50 条，看译名质量
  python scripts/enrich/build_glossary.py --game baldurs_gate3 --limit 50

  # 跑全量（已翻过的会跳过）
  python scripts/enrich/build_glossary.py --game baldurs_gate3

注意：
  - 只调远程 LLM API，不加载本地模型，服务器可跑；不要在服务器上做 embedding。
  - LLM 的中文译名仅供参考，conf 低的务必人工过一遍。
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 各游戏的结构化库 + wiki 路径（按需扩充）
GAME_DB = {
    "baldurs_gate3": ("games/baldurs_gate3/data/baldurs_gate3_data.db",
                      "games/baldurs_gate3/data/wiki_data.md"),
}

# 参与抽取的表 → 术语类别
TABLE_KINDS = {
    "characters": "character",
    "bosses": "boss",
    "spells": "spell",
    "items": "item",
    "armor": "armor",
    "weapons": "weapon",
    "locations": "location",
    "quests": "quest",
}

SURROUND_QUOTES = re.compile(r"^[\"'“”‘’\s]+|[\"'“”‘’\s]+$")
PAREN_SUFFIX = re.compile(r"\s*[\(\[][^\(\)\[\]]*[\)\]]\s*$")

# 表头/通用词，永远不当术语翻
STOPWORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "first", "second", "third", "fourth", "fifth",
    "quests", "quest", "characters", "character", "items", "item", "spells", "spell",
    "actions", "action", "attacks", "attack", "reactions", "reaction", "passives", "passive",
    "equipment", "misc", "other", "none", "all", "you", "your", "they", "them",
    "here", "there", "this", "that", "self", "target", "targets",
}

# 这些类别里的单字通用词基本是噪声；法术/物品里的 Light、Aid 是合法名称，不按此卡
STRICT_KINDS = {"character", "boss", "location", "quest"}


def is_noise(name: str, kind: str, total: int, titled: int) -> bool:
    """过滤掉不是专名的条目：纯数字、表头词、几乎从不大写开头的常见词。"""
    low = name.lower().strip()
    if not low or low.isdigit():
        return True
    if low in STOPWORDS:
        return True
    if len(low) <= 1:
        return True
    # 单字且 wiki 里主要小写出现 → 是普通词，不是专名
    if kind in STRICT_KINDS and " " not in low and total >= 5 and titled / total < 0.5:
        return True
    return False


def normalize(raw: str) -> str:
    """去首尾引号/空白，统一撇号。"""
    if not raw:
        return ""
    s = raw.replace("’", "'").replace("‘", "'")
    s = SURROUND_QUOTES.sub("", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def base_candidates(name: str):
    """给出由窄到宽的候选基名：原名 → 去尾部括号 → 冒号前半 → 两者叠加。"""
    cands = [name]
    cur = name
    for _ in range(3):
        cur = PAREN_SUFFIX.sub("", cur)
        if cur != cands[-1]:
            cands.append(cur)
    if ":" in name:
        cands.append(name.split(":", 1)[0].strip())
    if ":" in cur:
        cands.append(cur.split(":", 1)[0].strip())
    out, seen = [], set()
    for c in cands:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


TOKEN_RE = re.compile(r"[A-Za-z0-9']+")


def count_mentions(wiki_path: Path, names, max_ngram: int = 5):
    """一次扫描统计各名字在 wiki 原文中的出现次数，并区分「大写开头命中」次数。

    返回 {小写名: (总命中, 大写开头命中)}——后者用来判断它是不是真的当专名用。
    """
    if not wiki_path.exists() or not names:
        return {}
    raw = wiki_path.read_text(encoding="utf-8", errors="ignore")
    exact_tokens = TOKEN_RE.findall(raw)
    lower_tokens = [t.lower() for t in exact_tokens]
    del raw

    name_set = {n.lower() for n in names}
    starts = {n.split()[0] for n in name_set}
    total, titled = Counter(), Counter()
    n_tok = len(lower_tokens)
    for i, tok in enumerate(lower_tokens):
        if tok not in starts:
            continue
        for n in range(1, max_ngram + 1):
            if i + n > n_tok:
                break
            cand = " ".join(lower_tokens[i:i + n])
            if cand in name_set:
                total[cand] += 1
                head = exact_tokens[i] if i < len(exact_tokens) else ""
                if head[:1].isupper():
                    titled[cand] += 1
    return {k: (v, titled.get(k, 0)) for k, v in total.items()}


def collect_entities(db_path: Path, wiki_path: Path):
    """返回 [{kind, name, base, variants}]，base 优先取库里真实存在的同名字段。"""
    conn = sqlite3.connect(db_path)
    known = {}                       # 全部规范化名 → kind（第一个出现的表为准）
    for table in TABLE_KINDS:
        try:
            rows = conn.execute(f"SELECT name FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        for (raw,) in rows:
            n = normalize(raw)
            if n:
                known.setdefault(n, TABLE_KINDS[table])

    # 基名归属：候选里第一个「本身也在库里」的名字
    groups = defaultdict(list)       # base → [full name]
    kind_of = {}
    for table, kind in TABLE_KINDS.items():
        try:
            rows = conn.execute(f"SELECT name FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        for (raw,) in rows:
            n = normalize(raw)
            if not n:
                continue
            base = next((c for c in base_candidates(n) if c != n and c in known), n)
            groups[base].append(n)
            kind_of[base] = kind
    conn.close()

    freq_map = count_mentions(wiki_path, list(groups))
    ents = []
    for base, members in groups.items():
        kind = kind_of[base]
        total, titled = freq_map.get(base.lower(), (0, 0))
        if is_noise(base, kind, total, titled):
            continue
        ents.append({
            "kind": kind,
            "name": base,
            "base": base,
            "variants": sorted({m for m in members if m != base}),
            "freq": total,
        })
    return ents


def pick_entities(ents, limit):
    """按表规模开方分配配额，表内按 wiki 频率取 top，保证类别多样性。"""
    by_kind = defaultdict(list)
    for e in ents:
        by_kind[e["kind"]].append(e)
    for k in by_kind:
        by_kind[k].sort(key=lambda x: (-x["freq"], x["name"]))

    if not limit:
        picked = [e for k in by_kind for e in by_kind[k]]
        return sorted(picked, key=lambda x: (-x["freq"], x["name"]))

    weights = {k: len(v) ** 0.5 for k, v in by_kind.items()}
    total_w = sum(weights.values()) or 1
    quota = {k: max(1, round(limit * w / total_w)) for k, w in weights.items()}

    picked = []
    for k, v in by_kind.items():
        picked.extend(v[: quota[k]])
    picked.sort(key=lambda x: (-x["freq"], x["name"]))
    return picked[:limit]


SYSTEM_PROMPT = """你是《博德之门3》(Baldur's Gate 3) 中文资料库的术语校对员。
任务：把给定的英文专有名词翻译成简体中文名称，优先使用游戏官方中文版的译名；
若官方译名不确定，用中文玩家社区最通行的叫法；人名/地名用常见音译。

严格只输出 JSON 对象，不要解释，不要 markdown 代码块。格式：
{"<英文原名>": {"zh": "<中文名>", "conf": <0.0-1.0 的数字>}}

conf 含义：你对这个中文名是官方/通行译名的把握程度。
0.9 以上 = 确定的官方译名；0.6-0.9 = 常见译法；0.6 以下 = 猜测。"""


def translate_batch(client, model, items):
    """items: [{"name","kind"}] → {name: {"zh","conf"}}"""
    listing = "\n".join(f"- {it['name']}  [{it['kind']}]" for it in items)
    user_msg = f"待翻译的专有名词（共 {len(items)} 条）：\n{listing}"
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": user_msg}],
    )
    text = resp.choices[0].message.content or "{}"
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    data = json.loads(text)
    out = {}
    for it in items:
        v = data.get(it["name"])
        if isinstance(v, str):
            out[it["name"]] = {"zh": v, "conf": 0.5}
        elif isinstance(v, dict) and v.get("zh"):
            try:
                conf = float(v.get("conf", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            out[it["name"]] = {"zh": str(v["zh"]).strip(), "conf": max(0.0, min(1.0, conf))}
    return out, getattr(resp, "usage", None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="baldurs_gate3")
    ap.add_argument("--limit", type=int, default=0, help="0=全量；小数字用于抽样试跑")
    ap.add_argument("--batch-size", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true", help="只列出会跑哪些名字，不调 API")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.game not in GAME_DB:
        sys.exit(f"未登记的游戏：{args.game}（先在 GAME_DB 里加路径）")
    db_rel, wiki_rel = GAME_DB[args.game]
    db_path, wiki_path = PROJECT_ROOT / db_rel, PROJECT_ROOT / wiki_rel
    if not db_path.exists():
        sys.exit(f"找不到数据库：{db_path}")

    out_path = Path(args.out) if args.out else db_path.parent / f"{args.game}_glossary.jsonl"

    ents = collect_entities(db_path, wiki_path)
    picked = pick_entities(ents, args.limit)

    kind_count = Counter(e["kind"] for e in picked)
    print(f"实体基名合计 {len(ents)} 条；本次处理 {len(picked)} 条")
    print("类别分布：" + "，".join(f"{k} {v}" for k, v in sorted(kind_count.items())))
    print("变体归并：共 " + str(sum(len(e['variants']) for e in ents)) + " 个变体挂到基名下")
    if args.dry_run:
        print("\n（dry-run，不调 API）前 20 条：")
        for e in picked[:20]:
            v = f"  ← 变体 {len(e['variants'])} 个" if e["variants"] else ""
            print(f"  [{e['kind']:<10}] {e['name']}  (wiki 出现 {e['freq']} 次){v}")
        return

    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["name_en"])
            except (json.JSONDecodeError, KeyError):
                continue
        print(f"续跑：{out_path.name} 已有 {len(done)} 条，跳过")

    todo = [e for e in picked if e["name"] not in done]
    if not todo:
        print("没有待翻译的条目。")
        return

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or None
    model = os.getenv("CHAT_MODEL", "deepseek-v4-flash")
    if not api_key:
        sys.exit("缺少 OPENAI_API_KEY（.env）")
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)

    rows, usage_total = [], Counter()
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 逐批落盘：长跑中途掉了也不丢已完成部分（续跑靠文件去重）
    with out_path.open("a", encoding="utf-8") as sink:
        for i in range(0, len(todo), args.batch_size):
            batch = todo[i:i + args.batch_size]
            payload = [{"name": e["name"], "kind": e["kind"]} for e in batch]
            try:
                tr, usage = translate_batch(client, model, payload)
            except Exception as exc:                  # 网络/JSON 异常 → 拆小重试一次
                print(f"  批 {i//args.batch_size+1} 失败（{type(exc).__name__}: {exc}），拆半重试")
                tr, usage = {}, None
                for it in payload:
                    try:
                        sub, u = translate_batch(client, model, [it])
                        tr.update(sub)
                        if u:
                            usage_total["tokens"] += getattr(u, "total_tokens", 0) or 0
                    except Exception as exc2:
                        print(f"    跳过 {it['name']}：{exc2}")
                    time.sleep(0.3)
            if usage:
                usage_total["tokens"] += getattr(usage, "total_tokens", 0) or 0

            new_rows = []
            for e in batch:
                t = tr.get(e["name"])
                if not t:
                    continue
                new_rows.append({
                    "name_en": e["name"],
                    "base_en": e["base"],
                    "kind": e["kind"],
                    "name_zh": t["zh"],
                    "conf": round(t["conf"], 2),
                    "variants": e["variants"],
                    "wiki_freq": e["freq"],
                    "model": model,
                    "ts": ts,
                })
            for r in new_rows:
                sink.write(json.dumps(r, ensure_ascii=False) + "\n")
            sink.flush()
            rows.extend(new_rows)
            print(f"  批 {i//args.batch_size+1}: {len(new_rows)}/{len(batch)} 条成功")
            time.sleep(0.2)

    print(f"\n写入 {out_path}（新增 {len(rows)} 条），tokens ≈ {usage_total['tokens']}")
    low = [r for r in rows if r["conf"] < 0.7]
    if low:
        print(f"低置信（<0.7）{len(low)} 条，建议人工过：")
        for r in low[:10]:
            print(f"  [{r['kind']:<10}] {r['name_en']} → {r['name_zh']}  (conf {r['conf']})")

    print("\n全部译名预览：")
    for r in sorted(rows, key=lambda x: -x["wiki_freq"])[:60]:
        print(f"  [{r['kind']:<10}] {r['name_en']:<38} → {r['name_zh']}  ({r['conf']})")


if __name__ == "__main__":
    main()
