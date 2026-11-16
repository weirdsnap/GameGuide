#!/usr/bin/env python3
"""重抓 Fandom/wiki.gg 正文 + 质量闸门（在能访问外网的机器上跑，一般是 Mac）。

和 scripts/fetch/wiki.py 的区别（wiki.py 保留不动，它抓的六个库是好的）：

1. 只取 `<div class="mw-parser-output">` 范围，页面侧栏/页脚一律不进来；
2. 用一个真正的 HTML 剥离器（HTMLParser + 跳过节栈）删 navbox/infobox/toc/
   reference/editsection，嵌套也删得干净——旧脚本用正则删 `<table class="navbox">`，
   遇到嵌套表格就只删掉一半，剩下的导航链接全部混进正文，这是丝之歌「单页 146KB、
   正文串页」的直接原因；
3. 抓完自动跑质量闸门（体积分布、导航污染、段落率、中文占比），不达标退出码 2；
4. 写入 `wiki_data.refetch.md`，不碰正式文件；确认要替换时加 --replace（先备份）。

用法：
  # 1. 先探源：只发现页面 + 抽样 10 页，打印报告，不写文件
  python3 scripts/fetch/refetch_wiki.py --game silksong --probe

  # 2. 正式抓（断点续传，中断了重跑同一条命令即可）
  python3 scripts/fetch/refetch_wiki.py --game silksong

  # 3. 中文源（泰拉瑞亚），显式指定 API 与分类
  python3 scripts/fetch/refetch_wiki.py --game terraria --lang zh \
      --api https://terraria.wiki.gg/zh/api.php \
      --categories "护甲物品,饰品,武器物品,Boss,敌怪"

  # 4. 质检报告在 games/<game>/data/refetch_report.json，满意后替换
  python3 scripts/fetch/refetch_wiki.py --game silksong --replace
"""

import argparse
import json
import random
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("需要 requests：pip install requests")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

UA = "GameGuideBot/2.0 (personal research; dlb96@qq.com)"
SKIP_PREFIX = ("File:", "Template:", "Category:", "User:", "Thread:", "MediaWiki:", "Help:")

# 这些 class 出现的元素整块丢掉（导航、信息框、目录、引用角标、编辑链接）
SKIP_CLASSES = {
    "navbox", "navbox-inner", "vertical-navbox", "nav-box", "navbar",
    "infobox", "toc", "mw-editsection", "reference", "references",
    "noprint", "metadata", "ambox", "messagebox", "sidebar", "catlinks",
    "mw-references-wrap", "printfooter", "mw-jump-link", "hidden",
}
# 这些 class 命中时才丢（wikitable 里可能有正文数据表，不能一律丢）
SKIP_TABLE_CLASSES = {"navbox", "infobox", "toc", "vertical-navbox", "nav-box"}

# 整节删除的标题（导航/纯图片/外链，对问答没有信息量）
DROP_SECTIONS = {
    "navigation", "site navigation", "nav", "contents", "gallery",
    "external links", "references", "see also", "notes",
}
BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "li", "tr", "br",
              "blockquote", "dd", "dt", "pre", "table", "ul", "ol"}
VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "source"}


class BodyExtractor(HTMLParser):
    """从 MediaWiki 渲染 HTML 里抽正文，按跳过节栈丢弃导航性元素。"""

    def __init__(self, start_anywhere: bool = False):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.in_body = start_anywhere
        self.skip_stack = []
        self.heading = None

    # ── 内部 ──
    def _push_skip(self, tag):
        if tag not in VOID_TAGS:
            self.skip_stack.append(tag)

    def _opening(self, tag):
        return f"\n\n## " if tag == "h2" else (f"\n\n### " if tag == "h3" else None)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = set((a.get("class") or "").split())
        if tag == "div" and "mw-parser-output" in cls:
            self.in_body = True
            return
        if not self.in_body:
            return
        if self.skip_stack:
            self._push_skip(tag)
            return
        if tag in ("table", "div", "aside", "span", "sup", "ul", "ol"):
            if cls & (SKIP_CLASSES | SKIP_TABLE_CLASSES):
                self._push_skip(tag)
                return
        mark = self._opening(tag)
        if mark:
            self.heading = tag
            self.out.append(mark)
            return
        if tag == "li":
            self.out.append("\n- ")
        elif tag == "tr":
            self.out.append("\n| ")
        elif tag in BLOCK_TAGS:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if not self.in_body:
            return
        if self.skip_stack:
            if tag in self.skip_stack:
                while self.skip_stack:
                    if self.skip_stack.pop() == tag:
                        break
            return
        if tag == self.heading:
            self.out.append("\n\n")
            self.heading = None
        elif tag in BLOCK_TAGS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.in_body or self.skip_stack:
            return
        self.out.append(data)

    def text(self):
        return "".join(self.out)


def parse_html(raw_html: str) -> str:
    """HTML → 纯文本正文。"""
    raw_html = re.sub(r"<script[^>]*>.*?</script>", "", raw_html, flags=re.S)
    raw_html = re.sub(r"<style[^>]*>.*?</style>", "", raw_html, flags=re.S)
    # 页脚/分类栏之后的东西一律不要
    raw_html = re.split(r'<div[^>]*id="catlinks"|<footer', raw_html)[0]
    p = BodyExtractor(start_anywhere="mw-parser-output" not in raw_html)
    p.feed(raw_html)
    p.close()
    text = p.text()

    text = text.replace("\u00a0", " ").replace("\u8201", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return drop_sections(text)


def drop_sections(text: str) -> str:
    """按 `## 标题` 切节，删掉导航类整节（### 子节跟着父节一起走）。"""
    parts = re.split(r"(?m)^(## .+)$", text)
    kept = [parts[0]]
    for i in range(1, len(parts) - 1, 2):
        title = parts[i].lstrip("#").strip().lower()
        if title in DROP_SECTIONS:
            continue
        kept.append(parts[i])
        kept.append(parts[i + 1])
    out = "".join(kept).strip()
    return re.sub(r"\n{3,}", "\n\n", out)


# ── API ──
class Wiki:
    def __init__(self, api: str, delay: float = 0.35):
        self.api = api
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})

    def api_get(self, params: dict, retries: int = 3):
        params = dict(params, format="json")
        for attempt in range(retries):
            try:
                r = self.s.get(self.api, params=params, timeout=30)
                if r.status_code in (403, 429):
                    time.sleep(3 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError) as e:
                if attempt == retries - 1:
                    print(f"    ⚠️ API 失败: {e}")
                    return None
                time.sleep(2 * (attempt + 1))
        print("    ⚠️ API 被限流（403/429），检查 UA 或加大 --delay")
        return None

    def category_titles(self, cats, depth=2):
        """分类遍历（含子分类），返回页面标题集合。"""
        found, seen_cats, queue = set(), set(), [(c, 0) for c in cats]
        while queue:
            cat, d = queue.pop(0)
            if cat in seen_cats:
                continue
            seen_cats.add(cat)
            cont = {}
            n = 0
            while True:
                data = self.api_get({"action": "query", "list": "categorymembers",
                                     "cmtitle": f"Category:{cat}", "cmlimit": "max",
                                     "cmtype": "page|subcat", **cont})
                if not data:
                    break
                for m in data.get("query", {}).get("categorymembers", []):
                    t, typ = m.get("title", ""), m.get("type")
                    if typ == "subcat":
                        sub = t.replace("Category:", "")
                        if d + 1 <= depth and sub not in seen_cats:
                            queue.append((sub, d + 1))
                    elif t and not t.startswith(SKIP_PREFIX):
                        found.add(t)
                        n += 1
                cont = {k: v for k, v in data.get("continue", {}).items() if k != "continue"}
                if not cont:
                    break
                time.sleep(self.delay)
            print(f"    📂 {cat}: +{n}（累计 {len(found)}）")
            time.sleep(self.delay)
        return found

    def allpages(self, limit=2000):
        found, cont = set(), {}
        while len(found) < limit:
            data = self.api_get({"action": "query", "list": "allpages", "apnamespace": 0,
                                 "aplimit": "max", **cont})
            if not data:
                break
            for p in data.get("query", {}).get("allpages", []):
                found.add(p["title"])
            cont = {k: v for k, v in data.get("continue", {}).items() if k != "continue"}
            if not cont:
                break
            time.sleep(self.delay)
        return found

    def page_text(self, title: str):
        data = self.api_get({"action": "parse", "page": title, "prop": "text",
                             "disablelimitreport": 1, "redirects": 1})
        if not data or data.get("error"):
            return None
        raw = data.get("parse", {}).get("text", {}).get("*", "")
        if not raw:
            return None
        return parse_html(raw)


# ── 质量闸门 ──
def audit(pages: dict, zh: bool) -> dict:
    lens = sorted(len(t) for t in pages.values())
    n = len(lens) or 1
    med = lens[len(lens) // 2] if lens else 0
    over = sum(1 for x in lens if x > 20000) / n
    under = sum(1 for x in lens if x < 100) / n
    para = sum(1 for t in pages.values() if "\n\n" in t) / n

    # 导航污染：一页正文里出现多少个「别的页面标题」
    titles = list(pages)
    sample = random.sample(titles, min(20, len(titles)))
    poll = []
    for t in sample:
        body = pages[t]
        hits = sum(1 for other in titles if other != t and other in body)
        poll.append(hits)
    poll.sort()
    poll_med = poll[len(poll) // 2] if poll else 0

    cjk = 0.0
    if zh:
        joined = "".join(pages.values())
        cjk = len(re.findall(r"[\u4e00-\u9fff]", joined)) / max(len(joined), 1)

    checks = {
        "median_chars": (med, 500 <= med <= 8000),
        "share_over_20k": (round(over, 3), over <= 0.05),
        "share_under_100": (round(under, 3), under <= 0.12),
        "paragraph_rate": (round(para, 3), para >= 0.70),
        "nav_pollution_median": (poll_med, poll_med <= 6),
    }
    if zh:
        checks["cjk_ratio"] = (round(cjk, 3), cjk >= 0.25)
    return {"pages": len(pages), "checks": checks,
            "ok": all(ok for _, ok in checks.values())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--lang", default="en", choices=["en", "zh"])
    ap.add_argument("--api", help="覆盖 API 地址（换源时用）")
    ap.add_argument("--categories", help="逗号分隔的分类名，覆盖 registry 配置")
    ap.add_argument("--suffix", help="标题必须包含的子串，如 '(Silksong)'")
    ap.add_argument("--delay", type=float, default=0.35)
    ap.add_argument("--limit", type=int, default=0, help="最多抓多少页（0=不限）")
    ap.add_argument("--probe", action="store_true", help="只发现+抽样，不写文件")
    ap.add_argument("--replace", action="store_true", help="质检通过后替换正式文件（先备份）")
    args = ap.parse_args()

    from rag_agent.game_registry import WIKI_CONFIGS

    cfg = WIKI_CONFIGS.get(args.game)
    if not cfg:
        sys.exit(f"未知游戏 {args.game}，可选：{', '.join(sorted(WIKI_CONFIGS))}")

    api = args.api or cfg["api"]
    if args.lang == "zh" and not args.api and "/zh/" not in api:
        api = api.replace("/api.php", "/zh/api.php")
    cats = [c.strip() for c in args.categories.split(",")] if args.categories \
        else (cfg.get("zh_categories") if args.lang == "zh" and cfg.get("zh_categories")
              else cfg["categories"])
    out = Path(cfg["output"])
    if args.lang == "zh":
        out = out.with_name(out.stem + "_zh" + out.suffix)
    out = out.with_name(out.stem + ".refetch" + out.suffix)

    print(f"\n🌐 {args.game} / {args.lang} → {api}")
    wiki = Wiki(api, args.delay)

    print("1️⃣  发现页面")
    titles = wiki.category_titles(cats) if cats else set()
    if len(titles) < 10:
        print(f"    分类只给 {len(titles)} 页，用 allpages 兜底")
        titles |= wiki.allpages()
    if args.suffix:
        before = len(titles)
        titles = {t for t in titles if args.suffix in t}
        print(f"    后缀过滤 {args.suffix}: {before} → {len(titles)}")
    titles = sorted(titles)
    if args.limit:
        titles = titles[: args.limit]
    print(f"    候选 {len(titles)} 页")
    if not titles:
        sys.exit("没有候选页面，检查 --api / --categories")

    state_path = out.with_suffix(".state.json")
    state = {"done": {}, "api": api}
    if state_path.exists() and not args.probe:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        print(f"    🔄 断点续传：已完成 {len(state['done'])} 页")

    sample = titles[:10] if args.probe else titles
    pages = {}
    ok = bad = skipped = 0
    for i, t in enumerate(sample, 1):
        if t in state["done"]:
            pages[t] = state["done"][t]
            skipped += 1
            continue
        text = wiki.page_text(t)
        if text and len(text) >= 80:
            pages[t] = text
            ok += 1
        else:
            bad += 1
        if i % 20 == 0 or args.probe:
            print(f"    {i}/{len(sample)} ✓{ok} ✗{bad} ↺{skipped}")
        time.sleep(args.delay)

    report = audit(pages, args.lang == "zh")
    print(f"\n2️⃣  质检：{report['pages']} 页")
    for k, (v, passed) in report["checks"].items():
        print(f"    {'✅' if passed else '❌'} {k} = {v}")
    print(f"    总体：{'通过' if report['ok'] else '不通过'}")

    if args.probe:
        print("\n（--probe 模式，未写文件）")
        for t in list(pages)[:5]:
            print(f"\n--- {t} ---\n{pages[t][:300]}")
        return 0 if report["ok"] else 2

    if not report["ok"]:
        print("\n❌ 质检未通过，不写文件。先看 refetch_report_*.json 里的失败项。")

    state["done"].update(pages)
    out.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    rep_path = out.with_name(f"refetch_report_{args.lang}.json")
    rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    header = (f"# {cfg.get('name', args.game)} Wiki Data"
              f"{' (中文)' if args.lang == 'zh' else ''}\n\n"
              f"来源: {api}\n分类: {', '.join(cats)}\n总页数: {len(pages)}\n"
              f"抓取方式: refetch_wiki.py（正文限定 mw-parser-output，导航/信息框整块剥离）\n\n")
    with open(out, "w", encoding="utf-8") as f:
        f.write(header)
        for t in sorted(pages):
            f.write(f"## {t}\n\n{pages[t]}\n\n---\n\n")
    kb = out.stat().st_size / 1024
    print(f"\n3️⃣  已写 {out}（{kb:.0f}KB，{len(pages)} 页）")
    print(f"    报告 {rep_path}")

    if args.replace:
        if not report["ok"]:
            sys.exit("质检未通过，拒绝替换")
        target = out.with_name(out.name.replace(".refetch", ""))
        if target.exists():
            bak = target.with_name(target.name + ".bak-refetch")
            bak.write_bytes(target.read_bytes())
            print(f"    备份旧文件 → {bak.name}")
        target.write_bytes(out.read_bytes())
        print(f"    ✅ 已替换 {target}")
    else:
        print(f"    确认语气后替换：python3 {sys.argv[0]} --game {args.game}"
              f"{' --lang zh' if args.lang == 'zh' else ''} ... --replace")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
