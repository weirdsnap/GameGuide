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

# Infobox 里属于噪音的字段（标题已在节名里，图片/baseid 对检索无意义）
DROP_FIELDS = {
    "title", "name", "image", "imagecaption", "caption", "baseid", "id",
    "appears_games", "appears_in", "appears", "source", "japanese", "chinese",
}

FILE_CANDIDATES = ["data/wiki_data.md", "data/wiki_data_zh.md"]

# 需要压平（而不是删掉）的模板：里面的字段对检索有价值
FLATTEN_RE = re.compile(r"(?i)infobox|dbentry|dbchar|db ?entry|ttstats|block ?quote|quote|quote2")


def flatten_template(block: str) -> list[str] | None:
    """把 {{Infobox ... |k = v}} / {{Block Quote|text=|speaker=}} 压平成文本行。"""
    inner = block[2:-2]
    parts = re.split(r"(?m)\n\s*\|\s*|\A\|", inner)
    if len(parts) <= 1:  # 单行模板，字段用 | 分隔
        parts = re.split(r"\|", inner)
    head = re.match(r"\s*([^\n\|]*)", inner)
    tname = head.group(1).strip() if head else ""
    kv: list[tuple[str, str]] = []
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key, val = key.strip(), val.strip()
        if not val or key.lower() in DROP_FIELDS:
            continue
        val = re.sub(r"\s*\n\s*", " ", val).strip().strip("* ").strip()
        if "{{" in val:  # 嵌套模板（如 {{Plainlist}}）再清一层
            val, _ = strip_templates(val)
            val = re.sub(r"\s{2,}", " ", val).strip()
        if val:
            kv.append((key, val))
    if not kv:
        return None
    if re.search(r"(?i)quote", tname):
        text = next((v for k, v in kv if k.lower() == "text"), "")
        who = next((v for k, v in kv if k.lower() in ("speaker", "by", "author")), "")
        if text:
            return [f"引文：{text}" + (f"（{who}）" if who else "")]
        return None
    return [f"- {k}：{v}" for k, v in kv]


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
            else:
                removed += 1
        else:
            removed += 1
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
    text = html.unescape(text)
    return text, {"ref": n_ref, "br": n_br, "html_tag": n_tag}


def drop_nav_lines(text: str) -> tuple[str, int, int]:
    """删纯导航行；超长行（通常导航+正文挤在一行）只断句保留，绝不整行删。"""
    keep, n, split = [], 0, 0
    for line in text.split("\n"):
        s = line.strip()
        if s.count("・") >= 4 or s.count(" | ") >= 5 or re.match(r"Contents\s+\d", s):
            if len(s) <= 600:
                n += 1
                continue
            line = re.sub(r"(?<=[.!?。！？])\s+", "\n", line)
            split += 1
        keep.append(line)
    return "\n".join(keep), n, split


def dedup_lines(text: str) -> tuple[str, int]:
    seen, keep, n = set(), [], 0
    for line in text.split("\n"):
        s = line.strip()
        if len(s) > 20:
            if s in seen:
                n += 1
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
    body = "\n" + tidy(body) if body.strip() else "\n"
    if len(samples) < sample_n and before != body:
        samples.append((title, before, body))
    return body


def clean_file(path: Path, write: bool, sample_n: int, stats: dict, samples: list) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    # 按标题行切分，标题行本身原样保留
    parts = re.split(r"(?m)^(#{1,3}[^\n]*)$", text)
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
