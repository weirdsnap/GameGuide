#!/usr/bin/env python3
"""采集 Fandom Wiki 剩余的零散页面。

包括主分类页面、Combat/Exploration/Quests 子分类
以及 Additional Content 中未采集的页面。
"""

import re
import time
from pathlib import Path

import requests

API_BASE = "https://hollowknight.fandom.com/api.php"
USER_AGENT = "GameGuideBot/1.0"
DATA_DIR = Path(__file__).parent.parent / "data"

# 所有要采集的页面（去重后）
PAGES_TO_FETCH = [
    # ===== 主分类页面 =====
    "Achievements (Hollow Knight)",
    "Completion (Hollow Knight)",
    "Controls (Hollow Knight)",
    "Cut Content (Hollow Knight)",
    "Endings (Hollow Knight)",
    "Godseeker Mode",
    "Steel Soul Mode",
    "Trivia (Hollow Knight)",
    "Updates (Hollow Knight)",
    # ===== Combat 子分类新增 =====
    "Damage Values and Enemy Health (Hollow Knight)",
    "Eternal Ordeal",
    "Hall of Gods",
    "Soul",
    # ===== Exploration 子分类新增 =====
    "Bench (Hollow Knight)",
    "Fast Travel (Hollow Knight)",
    # ===== Quests 子分类新增 =====
    "Delicate Flower (Quest)",
    "Grimm Troupe (Quest)",
    # ===== Additional Content 额外新增 =====
    "Grimm Troupe (Lore)",
    "Hollow Knight Beta",
    "Lifeblood Cocoon",
    "Soundtrack (Hollow Knight)",
    "Voidheart Edition",
]


def api_call(params: dict) -> dict:
    params.setdefault("format", "json")
    params.setdefault("action", "query")
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(3):
        try:
            resp = requests.get(API_BASE, params=params, headers=headers, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt < 2:
                time.sleep(2)
                continue
            print(f"    ⚠️ 请求失败: {e}")
            return {}
    return {}


def fetch_pages(titles: list) -> dict:
    """批量获取页面 wikitext。"""
    result = {}
    for batch_start in range(0, len(titles), 50):
        batch = titles[batch_start:batch_start + 50]
        params = {
            "titles": "|".join(batch),
            "redirects": "1",
            "prop": "revisions",
            "rvprop": "content",
        }
        data = api_call(params)
        if not data or "query" not in data:
            continue

        query = data["query"]
        title_map = {}
        if "redirects" in query:
            for rd in query["redirects"]:
                title_map[rd["from"]] = rd["to"]
        if "normalized" in query:
            for n in query["normalized"]:
                if n["from"] not in title_map:
                    title_map[n["from"]] = n["to"]

        pages = query.get("pages", {})
        content_by_title = {}
        for page_id, page_data in pages.items():
            if page_id == "-1":
                continue
            title = page_data.get("title", "")
            revisions = page_data.get("revisions", [])
            if revisions:
                content_by_title[title] = revisions[0].get("*", "")

        for orig in batch:
            target = orig
            seen = set()
            while target in title_map and target not in seen:
                seen.add(target)
                target = title_map[target]
            if target in content_by_title:
                result[orig] = content_by_title[target]

        time.sleep(1.0)

    return result


# ── 文本清洗（复用之前的逻辑）──

def clean_wikitext(wt: str) -> str:
    text = wt
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*/>", "", text)
    for tag in ["gallery", "nowiki", "pre", "code", "poem", "includeonly", "noinclude"]:
        text = re.sub(f"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL)
    text = re.sub(r"\[\[File:[^\]]*\]\]", "", text)
    text = re.sub(r"\[\[Image:[^\]]*\]\]", "", text)
    text = re.sub(r"\[\[Category:[^\]]*\]\]", "", text)
    text = re.sub(r"\[\[[a-z]{2,3}:[^\]]*\]\]", "", text)
    text = re.sub(r"\[\[Media:[^\]]*\]\]", "", text)

    # 表格（保留内容）
    lines = text.split("\n")
    cleaned_lines = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("{|"):
            in_table = True
            continue
        if stripped == "|}":
            in_table = False
            continue
        if in_table:
            if stripped.startswith("|") or stripped.startswith("!"):
                content = stripped.lstrip("|!-+").strip()
                if content:
                    cleaned_lines.append(content)
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    loop = 0
    while "{{" in text:
        loop += 1
        if loop > 100:
            text = re.sub(r"{{|}}", "", text)
            break
        new_text, n = re.subn(r"\{\{[^{}]*?\}\}", "", text)
        if n == 0:
            text = re.sub(r"{{|}}", "", text)
            break
        text = new_text

    text = re.sub(r"\[\[([^\]|]*?)\|([^\]]*?)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*?)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\[\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\[\]]+\]", "", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(div|span|center|small|big|sup|sub|u|s|strike|abbr|tt|code|blockquote|cite|table|tr|td|th|tbody|thead|caption|colgroup|col)[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"'''(.*?)'''", r"\1", text)
    text = re.sub(r"''(.*?)''", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^[ \t]+|[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"</?h[23456][^>]*>", "", text, flags=re.IGNORECASE)
    return text.strip()


def wikitext_to_heading(text: str) -> str:
    return re.sub(r"^={2,6}\s*(.*?)\s*={2,6}\s*$",
                  lambda m: "## " + m.group(1).strip(),
                  text, flags=re.MULTILINE)


def strip_list_markers(text: str) -> str:
    return re.sub(r"^[*#;:]+\s*", "", text, flags=re.MULTILINE)


def page_title_to_name(title: str) -> str:
    name = title.replace("_", " ")
    name = re.sub(r"\s*\(.*?\)\s*", "", name).strip()
    if not name:
        name = title.replace("_", " ")
    return name


def render_document(title: str, wikitext: str) -> str:
    """将 wikitext 渲染为知识文档。"""
    text = clean_wikitext(wikitext)
    if len(text) < 50:
        return None
    if text.startswith("#REDIRECT") or text.startswith("#redirect"):
        return None

    text = wikitext_to_heading(text)
    text = strip_list_markers(text)
    text = re.sub(r"__\w+__", "", text)
    text = text.strip()

    if len(text) < 50:
        return None

    name = page_title_to_name(title)
    slug = title.lower().replace(" ", "-").replace("'", "").replace("(", "").replace(")", "").replace(",", "")

    return (
        f"# 文档：{name}\n\n"
        f"- 类别：guide\n"
        f"- 标识：{slug}\n"
        f"- 来源：wiki/{title.replace(' ', '_')}\n\n"
        f"{text}"
    )


def get_existing_titles() -> set:
    """读取现有 wiki_data.md 已有页面标题。"""
    path = DATA_DIR / "wiki_data.md"
    if not path.exists():
        return set()
    content = path.read_text(encoding="utf-8")
    docs = re.split(r"(?=^#\s*文档)", content, flags=re.MULTILINE)
    titles = set()
    for d in docs:
        m = re.search(r"# 文档：(.+)", d)
        if m:
            titles.add(m.group(1).strip())
    return titles


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    existing = get_existing_titles()
    print(f"📖 已有页面: {len(existing)} 篇")

    # 过滤已存在的
    to_fetch = []
    for title in PAGES_TO_FETCH:
        name = page_title_to_name(title)
        if name in existing:
            print(f"   ✅ {name} (已有)")
        else:
            to_fetch.append(title)
            print(f"   📄 {title} ← 新")

    print(f"\n待采集: {len(to_fetch)} 页")

    if not to_fetch:
        print("没有新内容。")
        return

    if args.dry_run:
        return

    # 获取内容
    print(f"\n🌐 获取内容中...")
    pages = fetch_pages(to_fetch)
    print(f"成功获取 {len(pages)}/{len(to_fetch)} 页")

    # 渲染文档
    docs = []
    for title in to_fetch:
        wt = pages.get(title)
        if not wt:
            print(f"   ⚠️ {title} (无内容)")
            continue
        doc = render_document(title, wt)
        if doc:
            docs.append(doc)
            print(f"   ✅ {page_title_to_name(title)}")
        else:
            print(f"   ⏭️ {title} (跳过)")

    print(f"\n新文档: {len(docs)} 篇")

    if not docs:
        return

    # 追加到 wiki_data.md
    output_path = DATA_DIR / "wiki_data.md"
    existing_content = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    existing_content = existing_content.rstrip() + "\n\n"

    new_content = ""
    for doc in docs:
        new_content += doc + "\n\n---\n\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(existing_content)
        f.write(new_content)

    # 更新统计行
    total_docs = len(re.split(r"(?=^#\s*文档)", existing_content + new_content, flags=re.MULTILINE)) - 1
    lines = output_path.read_text(encoding="utf-8").split("\n")
    for i, line in enumerate(lines):
        if line.startswith("文档总数："):
            lines[i] = f"文档总数：{total_docs}"
            break
    output_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n✅ 已追加到 {output_path}")
    print(f"📊 总计: {total_docs} 篇文档（新增 {len(docs)} 篇）")


if __name__ == "__main__":
    main()
