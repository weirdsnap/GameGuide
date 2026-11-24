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
    # Fandom 新版便携信息框（aside.portable-infobox 及其内部 pi-* 部件）
    "portable-infobox", "pi-section", "pi-title", "pi-data", "pi-group",
    "pi-caption", "pi-header", "pi-image", "pi-item",
}
# 这些 class 前缀命中也丢（Fandom 的 pi-* 部件名不固定，逐个列举必漏）
# dialogue-*：Fandom 在渲染层注入的随机对话/名言模块（wikitext 里不存在，
# 如 Hornet (Silksong) 页的 dialogue-silksong div 渲染出 Zote 的条目简介），
# 与页面主题无关，属于噪声
SKIP_CLASS_PREFIXES = ("pi-", "dialogue-")
# 注意：不再区分「表格专用名单」——navbox/infobox 一律整块丢；正文数据表用的是
# wikitable/article-table 这类 class，不在跳过名单里，不会误删。
# 正文里的 aside 基本都是信息框/侧栏，整块丢
SKIP_TAGS = {"aside"}

# 整节删除的标题（导航/纯图片/外链，对问答没有信息量）
DROP_SECTIONS = {
    "navigation", "site navigation", "nav", "contents", "gallery",
    "external links", "references", "see also", "notes",
}
BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "li", "tr", "br",
              "blockquote", "dd", "dt", "pre", "table", "ul", "ol"}
VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "source"}


def is_skip_class(cls: set) -> bool:
    """这个元素的 class 是否属于「整块丢掉」一类。

    除了精确名，还认两类变体：pi-* 前缀（Fandom 便携信息框部件）与
    名里带 navbox/infobox 的（navbox-small、infobox-table 这类拼写）。
    """
    if cls & SKIP_CLASSES:
        return True
    for c in cls:
        if c.startswith(SKIP_CLASS_PREFIXES):
            return True
        if "navbox" in c or "infobox" in c:
            return True
    return False


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
        if tag in SKIP_TAGS:
            self._push_skip(tag)
            return
        if tag in ("table", "div", "span", "sup", "ul", "ol") and is_skip_class(cls):
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


# ── 跨页共享模板（幽灵文本）清理 ──
def _para_key(p: str) -> str:
    return re.sub(r"\s+", " ", p).strip()


def looks_like_nav_table(p: str) -> bool:
    """连续短表格行 + 没有句读 → 当作导航/数据模板渲染出来的链接墙。

    导航模板渲染成表格时，正文层看到的就是几十行 `| 短词`；真正的正文数据表
    （属性/价格表）行里带数值与单位，长度不会这么短。
    """
    lines = [ln.strip() for ln in p.split("\n") if ln.strip()]
    if len(lines) < 12:
        return False
    nav = [ln for ln in lines if ln.startswith("|")]
    if len(nav) < 10 or len(nav) / len(lines) < 0.7:
        return False
    # 导航链接墙没有数值；带数字的多半是属性/价格表，不能当导航删
    if sum(1 for ln in nav if re.search(r"\d", ln)) / len(nav) > 0.2:
        return False
    avg = sum(len(re.sub(r"^[|\-\s]+", "", ln)) for ln in nav) / len(nav)
    return avg < 22 and "。" not in p and p.count(".") <= 1


def strip_shared_paragraphs(pages: dict, ratio: float = 0.15, min_len: int = 40,
                            min_pages: int = 3, navlines: bool = True):
    """删跨页重复段与导航表。

    不猜模板名也无需知道模板长什么样：同一段长文本出现在 15% 以上页面（至少 3 页）
    就只能是导航/数据模板，正文不会这么整齐地重复。class 名单总会漏一两个变体，
    这一层是对它的兜底。
    """
    total = max(len(pages), 1)
    need = max(min_pages, int(total * ratio))
    counts: dict[str, int] = {}
    for body in pages.values():
        seen = set()
        for p in body.split("\n\n"):
            k = _para_key(p)
            if len(k) < min_len or k in seen:
                continue
            seen.add(k)
            counts[k] = counts.get(k, 0) + 1
    shared = {k for k, v in counts.items() if v >= need}

    out, dropped_shared, dropped_nav = {}, 0, 0
    for title, body in pages.items():
        keep = []
        for p in body.split("\n\n"):
            if _para_key(p) in shared:
                dropped_shared += 1
                continue
            if navlines and looks_like_nav_table(p):
                dropped_nav += 1
                continue
            if p.strip():
                keep.append(p)
        out[title] = "\n\n".join(keep).strip()
    stats = {"pages": total, "threshold": need, "shared_paras": len(shared),
             "dropped_shared": dropped_shared, "dropped_navlines": dropped_nav}
    return out, stats


def shared_para_rate(pages: dict, ratio: float = 0.15, min_pages: int = 3) -> float:
    """闸门用：跨页重复段在全部段落里的占比（清理后应接近 0）。"""
    total = max(len(pages), 1)
    need = max(min_pages, int(total * ratio))
    counts: dict[str, int] = {}
    for body in pages.values():
        seen = set()
        for p in body.split("\n\n"):
            k = _para_key(p)
            if len(k) < 40 or k in seen:
                continue
            seen.add(k)
            counts[k] = counts.get(k, 0) + 1
    if not counts:
        return 0.0
    dup = sum(1 for v in counts.values() if v >= need)
    return round(dup / len(counts), 3)


# ── API ──
class Wiki:
    def __init__(self, api: str, delay: float = 0.35):
        self.api = api
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})

    def reset_session(self):
        """丢弃当前 keep-alive 连接，换新 Session。

        Cloudflare 会对单条长连接做滴漏陷阱（约 400+ 请求后 ESTABLISHED 但
        不再回数据，read timeout 被逐字吊着永远不触发）。实测换连接即恢复。
        """
        try:
            self.s.close()
        except Exception:
            pass
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
def audit(pages: dict, zh: bool, nav_max: int = 6) -> dict:
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

    nav_poll = poll_med

    shared_rate = shared_para_rate(pages)

    # 标题 vs 正文对题：抽 20 页，标题里的实词得出现在正文开头
    hit = 0
    for t in sample:
        head = pages[t][:800].lower()
        words = [w for w in re.split(r"[\s\-_:,()\[\]/]+", t.lower()) if len(w) > 3]
        if not words or any(w in head for w in words):
            hit += 1
    match = round(hit / max(len(sample), 1), 3)

    cjk = 0.0
    if zh:
        joined = "".join(pages.values())
        cjk = len(re.findall(r"[\u4e00-\u9fff]", joined)) / max(len(joined), 1)

    checks = {
        "median_chars": (med, 500 <= med <= 8000),
        "share_over_20k": (round(over, 3), over <= 0.05),
        "share_under_100": (round(under, 3), under <= 0.12),
        "paragraph_rate": (round(para, 3), para >= 0.70),
        "nav_pollution_median": (nav_poll, nav_poll <= nav_max),
        "title_in_body_rate": (match, match >= 0.85),
        "shared_para_rate": (shared_rate, shared_rate <= 0.02),
    }
    if zh:
        # zh 站（如 terraria.wiki.gg/zh）标题是英文、正文是中文，
        # 标题词永远匹配不上正文——该检查结构性失效，降为仅供参考，
        # 对错题的防线由 cjk_ratio + 抽样人眼比对承担
        checks["title_in_body_rate"] = (match, True)
        checks["cjk_ratio"] = (round(cjk, 3), cjk >= 0.25)
    return {"pages": len(pages), "checks": checks,
            "ok": all(ok for _, ok in checks.values())}


class PageStallError(Exception):
    """单页抓取停滞（Cloudflare 滴漏陷阱）。

    必须继承 Exception 而非 OSError/TimeoutError：SIGALRM handler 在
    socket.read 里抛出时，urllib3 会把 OSError 子类包装成 ReadTimeout，
    随后被 api_get 的 except requests.RequestException 吞掉并重试——
    而 alarm 只装一次，重试再陷滴漏就永久卡死（实测复现）。
    自定义异常不被 urllib3 转换，直接穿透到主循环。
    """


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
    ap.add_argument("--no-strip", action="store_true",
                    help="关掉跨页模板/导航表清理（排查误删时对照用）")
    ap.add_argument("--strip-ratio", type=float, default=0.15,
                    help="共享段判定阈值：出现在多少比例的页面算模板（默认 0.15）")
    ap.add_argument("--replace", action="store_true", help="质检通过后替换正式文件（先备份）")
    ap.add_argument("--nav-threshold", type=int, default=6,
                    help="导航污染中位数上限（默认 6）。terraria zh 这类条目密集互链"
                         "的库（列表页枚举全部条目、正文自然提及关联物品）实测干净数据"
                         "也在 14 左右，需放宽到 20；判别真伪污染的依据是"
                         " shared_para_rate≈0 且抽样上下文为自然语句")
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
    # 看门狗线程：macOS 的 mDNS 解析卡死时进程阻塞在 C 库内部的 kevent 重试，
    # SIGALRM 到不了 Python 层（实测挂死 20+ 分钟无解）。守护线程检测到
    # 240s 无页面进度就 os._exit(42)，交给外层 shell 断点续传重启。
    import os
    import threading
    _last_progress = [time.monotonic()]
    def _watchdog():
        while True:
            time.sleep(30)
            if time.monotonic() - _last_progress[0] > 240:
                print("    🐕 看门狗：240s 无进展（疑似 DNS 挂死），"
                      "退出码 42 交给外层重启", flush=True)
                os._exit(42)
    threading.Thread(target=_watchdog, daemon=True).start()
    # 滴漏陷阱硬兜底：requests 的 read timeout 会被「逐字回包」吊死，
    # 用 SIGALRM 给单页设 90s 总上限。handler 必须抛 PageStallError
    # （非 OSError 子类），否则会被 urllib3 转成 ReadTimeout 后遭重试吞没
    import signal
    def _page_timeout(signum, frame):
        raise PageStallError("单页抓取超过 90s（疑似滴漏陷阱）")
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _page_timeout)
    for i, t in enumerate(sample, 1):
        if t in state["done"]:
            pages[t] = state["done"][t]
            skipped += 1
            continue
        # 每 200 页换新连接：Cloudflare 对单条 keep-alive 长连接会滴漏
        if i % 200 == 0:
            wiki.reset_session()
        if hasattr(signal, "SIGALRM"):
            signal.alarm(90)
        try:
            text = wiki.page_text(t)
        except PageStallError as e:
            # 连接已被滴漏污染，换新连接；该页记失败，下轮重跑会补
            print(f"    ⏱️ {t}: {e}")
            wiki.reset_session()
            text = None
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)
        # 阈值 30：剥离后只剩一行的真 stub 也收（如「X is a boss in Silksong.」，
        # 对问答有实体识别价值）；0 字符的空页仍然丢弃
        if text and len(text) >= 30:
            pages[t] = text
            ok += 1
        else:
            bad += 1
        if i % 20 == 0 or args.probe:
            print(f"    {i}/{len(sample)} ✓{ok} ✗{bad} ↺{skipped}")
        # 定期落盘：长任务（数千页、数小时）中途被杀/断网不至于全丢
        if not args.probe and i % 100 == 0:
            state["done"].update(pages)
            state_path.write_text(json.dumps(state, ensure_ascii=False),
                                  encoding="utf-8")
        _last_progress[0] = time.monotonic()
        time.sleep(args.delay)

    if args.no_strip:
        strip_stats = {}
    else:
        pages, strip_stats = strip_shared_paragraphs(
            pages, ratio=args.strip_ratio, navlines=True)
        print(f"\n🧹 跨页模板清理：共享段 {strip_stats['dropped_shared']} 处、"
              f"导航表 {strip_stats['dropped_navlines']} 处"
              f"（阈值：{strip_stats['threshold']} 页以上重复算模板）")

    report = audit(pages, args.lang == "zh", nav_max=args.nav_threshold)
    report["strip"] = strip_stats
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
              f"抓取方式: refetch_wiki.py（正文限定 mw-parser-output，导航/信息框整块剥离，"
              f"跨页共享模板按段落重复率清扫）\n\n")
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
