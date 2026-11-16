#!/usr/bin/env python3
"""就地清洗 games/<game>/data/wiki_data*.md（不重抓也能救回一部分）。

规则（只动正文，标题行一律保留）：
  1. {{...}} 模板：Infobox 类压平成「- 字段：值」，其余整块删
  2. <ref>...</ref> 引用标记删除
  3. HTML 注释、<br>、其余标签清理
  4. 纯导航行（一行里塞满 ・ 或 | 链接）+ Contents 目录行删除
  5. 节内长重复行去重
  6. 连续空行压缩

用法：
  python3 scripts/build/clean_wiki.py --game cyberpunk2077 --game mhw            # 只看报告
  python3 scripts/build/clean_wiki.py --game cyberpunk2077 --write              # 落盘（先备份）
  python3 scripts/build/clean_wiki.py --game cyberpunk2077 --write --sample 3   # 每节抽 3 条前后对照

注意：silksong 那种「整站碎片」不属于本脚本能救的范围，需要重抓。
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from datetime import date
from pathlib import Path

# Infobox 里属于噪音的字段（标题已在节名里，图片/baseid/发行信息对检索无意义；
# source / caption 有实际信息（获取来源、描述），必须保留）
DROP_FIELDS = {
    "title", "name", "image", "imagecaption", "baseid", "id",
    "appears_games", "appears_in", "appears", "japanese", "chinese",
}

FILE_CANDIDATES = ["data/wiki_data.md", "data/wiki_data_zh.md"]

# 审计收集：(类别, 所在节, 被删内容前 300 字)；--write 时也会填，供 audit_clean.py 报告
AUDIT: list[tuple[str, str, str]] = []
_CUR: dict = {"title": ""}

# 需要压平（而不是删掉）的模板：里面的字段对检索有价值
FLATTEN_RE = re.compile(
    r"(?i)infobox|dbentry|dbchar|db ?entry|ttstats|block ?quote|quote2?|plainlist|related ?box|craftingtable|/row"
)

# 纯展示类模板：压平时只取内容不做字段包装
VAL_ONLY_RE = re.compile(r"(?i)plainlist")


def flatten_template(block: str) -> list[str] | None:
    """把模板压平成文本行：Infobox → `- 字段：值`；Quote → 引文；Plainlist → 纯内容。"""
    inner = block[2:-2]
    m = re.match(r"\s*([^\|\n]{1,40})", inner)
    tname = m.group(1).strip() if m else ""
    rest = inner[m.end():] if m else inner
    # 只在实际字段分隔处切（|key = ...），避开 [[a|b]] 这类 wikilink
    parts = re.split(r"(?m)\n\s*\|\s*|\|(?=\s*[a-zA-Z_][\w #]{0,20}\s*=)", rest)
    kv: list[tuple[str, str]] = []
    loose: list[str] = []
    for part in parts:
        if "=" not in part:
            txt = re.sub(r"\s*\n\s*", " ", part).strip().lstrip("|* ").strip()
            if txt:
                loose.append(txt)
            continue
        key, val = part.split("=", 1)
        key, val = key.strip().strip("|"), val.strip()
        if not val or key.lower() in DROP_FIELDS:
            continue
        val = re.sub(r"\s*\n\s*", " ", val).strip().strip("* ").strip()
        if "{{" in val:  # 嵌套模板（如 {{Plainlist}}）再清一层
            val, _ = strip_templates(val)
            val = re.sub(r"\s{2,}", " ", val).strip()
        if val:
            kv.append((key, val))
    if VAL_ONLY_RE.search(tname):
        txt = " ".join(loose + [v for _, v in kv]).strip()
        return [txt] if txt else None
    if re.search(r"(?i)quote", tname):
        text = next((v for k, v in kv if k.lower() in ("text", "quote", "1")), "")
        who = next((v for k, v in kv if k.lower() in ("speaker", "by", "author", "source", "2")), "")
        if text:
            return [f"引文：{text}" + (f"（{who}）" if who else "")] + loose
        return loose or None
    # loose 是无 “key =” 的散段（如 {{/row| Plenty Satisfied ...}} 的行名），不能丢
    return loose + [f"- {k}：{v}" for k, v in kv]


def strip_templates(text: str) -> tuple[str, dict]:
    """平衡括号扫描，删除/压平 {{...}}。"""
    out: list[str] = []
    i = 0
    removed = flattened = 0
    while True:
        j = text.find("{{", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        depth = 0
        k = j
        while k < len(text):
            if text.startswith("{{", k):
                depth += 1
                k += 2
                continue
            if text.startswith("}}", k):
                depth -= 1
                k += 2
                if depth == 0:
                    break
                continue
            k += 1
        block = text[j:k]
        if FLATTEN_RE.search(block[:80]):
            lines = flatten_template(block)
            if lines:
                out.append("\n" + "\n".join(lines) + "\n")
                flattened += 1
                AUDIT.append(("flatten", _CUR["title"], block[:300]))
            else:
                removed += 1
                AUDIT.append(("tpl_empty", _CUR["title"], block[:300]))
        else:
            removed += 1
            tname = re.match(r"\{\{\s*([^\|\}\n]{1,40})", block)
            AUDIT.append(("tpl_" + (tname.group(1).strip() if tname else "?"), _CUR["title"], block[:300]))
        i = k
    return "".join(out), {"tpl_removed": removed, "tpl_flattened": flattened}


def strip_refs_html(text: str) -> tuple[str, dict]:
    n_ref = len(re.findall(r"<ref", text, flags=re.I))
    text = re.sub(r"<ref[^>]*/\s*>", "", text, flags=re.I)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.I | re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    n_br = len(re.findall(r"<br\s*/?>", text, flags=re.I))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    n_tag = len(re.findall(r"<[a-zA-Z/][^>]*>", text))
    text = re.sub(r"<[a-zA-Z/][^>]*>", "", text)
    # 兜底：跨节/未闭合的 ref 只剩残标签
    text = re.sub(r"</?ref[^>]*>", "", text, flags=re.I)
    # 多重实体（&amp;lt;b&amp;gt;）反转义后可能再露出标签，循环到不再变化为止
    for _ in range(3):
        nxt = re.sub(r"<[a-zA-Z/][^>]*>", "", html.unescape(text))
        if nxt == text:
            break
        text = nxt
    return text, {"ref": n_ref, "br": n_br, "html_tag": n_tag}


def drop_nav_lines(text: str) -> tuple[str, int, int]:
    """只删「短且纯导航」的行。

    超长行（>600B）通常是「页内目录 / 导航 + 正文」挤在一行（BG3、MHW 就是这样），
    整行删会丢正文，断句又会让第二次运行把断后的短行当导航删（不幂等）。
    所以超长行一律原样保留。
    """
    keep, n = [], 0
    for line in text.split("\n"):
        s = line.strip()
        if len(s) <= 600 and (
            s.count("・") >= 4 or s.count(" | ") >= 5 or re.match(r"Contents\s+\d", s)
        ):
            n += 1
            AUDIT.append(("nav_line", _CUR["title"], s[:300]))
            continue
        keep.append(line)
    return "\n".join(keep), n, 0


def dedup_lines(text: str) -> tuple[str, int]:
    """只对「长段落」去重（>=100 字）。

    短行重复往往不是冗余：表格单元格（`data-sort-value="O" |||||2`）、
    列表条目（`* Mulan Tea or Sunshine Cloud`）、技能表行（`1 Activates skill effect.`）
    每行都对应不同实体，删了就是丢数据。
    """
    seen, keep, n = set(), [], 0
    for line in text.split("\n"):
        s = line.strip()
        # 表格结构/单元格行一律保留
        if s.startswith(("{|", "|}", "|-", "!", "|")) or "||" in s or "!!" in s \
                or "data-sort-" in s or 'class="' in s:
            keep.append(line)
            continue
        if len(s) >= 100:
            if s in seen:
                n += 1
                AUDIT.append(("dup_line", _CUR["title"], s[:200]))
                continue
            seen.add(s)
        keep.append(line)
    return "\n".join(keep), n


def tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def clean_section(body: str, stats: dict, samples: list, title: str, sample_n: int) -> str:
    before = body
    body, s = strip_templates(body)
    for k, v in s.items():
        stats[k] = stats.get(k, 0) + v
    body, s = strip_refs_html(body)
    for k, v in s.items():
        stats[k] = stats.get(k, 0) + v
    body, n, split = drop_nav_lines(body)
    stats["nav_line"] = stats.get("nav_line", 0) + n
    stats["nav_split"] = stats.get("nav_split", 0) + split
    body, n = dedup_lines(body)
    stats["dup_line"] = stats.get("dup_line", 0) + n
    # 行首的 wiki 有序列表（#xxx）不能留成 markdown 标题，转成普通列表项
    body = re.sub(r"(?m)^#{1,3}(?=\S)", "- ", body)
    body = "\n" + tidy(body) if body.strip() else "\n"
    if len(samples) < sample_n and before != body:
        samples.append((title, before, body))
    return body


def clean_file(path: Path, write: bool, sample_n: int, stats: dict, samples: list) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    # 只有「# 后跟空白」才是 markdown 标题；`#Wesley Hunt` 这种是 wiki 有序列表，
    # 必须留在正文里参与清洗（否则 ref 会漏删、列表被当标题）。
    parts = re.split(r"(?m)^(#{1,3}\s[^\n]*)$", text)
    out: list[str] = []
    heads = 0
    for idx, part in enumerate(parts):
        if idx % 2 == 1:  # 标题行
            heads += 1
            out.append(part)
            continue
        if not part.strip():
            out.append(part)
            continue
        title = ""
        prev = parts[idx - 1].strip() if idx else ""
        title = prev.lstrip("#").strip()
        _CUR["title"] = title
        out.append(clean_section(part, stats, samples, title, sample_n))
    new = "".join(out)
    new = tidy(new)
    stats["heads"] = heads
    stats["old_bytes"] = len(text.encode())
    stats["new_bytes"] = len(new.encode())
    if write:
        bak = path.with_suffix(path.suffix + f".bak-{date.today():%Y%m%d}")
        if not bak.exists():
            shutil.copy2(path, bak)
            print(f"  备份 → {bak.name}")
        path.write_text(new, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", action="append", required=True)
    ap.add_argument("--root", default="games")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()

    root = Path(args.root)
    for game in args.game:
        for rel in FILE_CANDIDATES:
            path = root / game / rel
            if not path.exists():
                continue
            stats: dict = {}
            samples: list = []
            clean_file(path, args.write, args.sample, stats, samples)
            old, new = stats["old_bytes"], stats["new_bytes"]
            pct = (1 - new / old) * 100 if old else 0
            print(f"\n=== {path}  {old/1024:.0f}KB → {new/1024:.0f}KB (-{pct:.1f}%)  节={stats['heads']}")
            print("    " + "  ".join(f"{k}={v}" for k, v in stats.items()
                                     if k not in ("old_bytes", "new_bytes", "heads")))
            for title, before, after in samples[: args.sample]:
                print(f"\n  --- 抽样 [{title[:40]}]")
                print("  前:", " ⏎ ".join(before.split("\n")[:4])[:300])
                print("  后:", " ⏎ ".join(after.split("\n")[:6])[:300])
            if not args.write:
                print("    （dry-run，未落盘）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
