#!/usr/bin/env python3
"""Wiki 数据扩充器。

扫描 Wiki 分类，找出尚未采集的页面，批量拉取并追加到 wiki_data.md。

用法：
  python scripts/expand_wiki_data.py                  # 全量扩充
  python scripts/expand_wiki_data.py --dry-run         # 试跑预览
  python scripts/expand_wiki_data.py --category bosses # 只补指定分类
"""

import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests

API_BASE = "https://hollowknight.fandom.com/api.php"
USER_AGENT = "GameGuideBot/1.0 (knowledge collector for RAG agent)"
DATA_DIR = Path(__file__).parent.parent / "data"

# 要扫描的分类
CATEGORIES = {
    "Enemies_(Hollow_Knight)": "enemies",
    "Bosses_(Hollow_Knight)": "bosses",
    "Charms": "charms",
    "Spells_and_Abilities_(Hollow_Knight)": "abilities",
    "NPCs_(Hollow_Knight)": "characters",
    "Areas_(Hollow_Knight)": "areas",
    "Items_(Hollow_Knight)": "items",
    "Quests_(Hollow_Knight)": "quests",
    "Lore": "lore",
}

EXCLUDED_PREFIXES = ("User:", "Template:", "File:", "MediaWiki:", "Category:", "Talk:", "Module:")
EXCLUDED_SUFFIXES = ("(Silksong)",)


def api_call(params: dict) -> Optional[dict]:
    params["format"] = "json"
    params["action"] = params.get("action", "query")
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(API_BASE, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  ⚠️ API 请求失败: {e}")
        return None


def get_all_category_members(category: str) -> List[str]:
    """获取分类下的所有页面标题。（处理分页）"""
    titles = []
    cmcontinue = None
    while True:
        params = {
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmlimit": "max",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        data = api_call(params)
        if not data:
            break

        for member in data.get("query", {}).get("categorymembers", []):
            if member.get("ns") == 0:  # 主命名空间
                title = member["title"]
                # 过滤
                skip = False
                for prefix in EXCLUDED_PREFIXES:
                    if title.startswith(prefix):
                        skip = True
                        break
                for suffix in EXCLUDED_SUFFIXES:
                    if title.endswith(suffix):
                        skip = True
                        break
                if not skip:
                    titles.append(title)

        # 检查是否有后续页
        cont = data.get("continue", {})
        cmcontinue = cont.get("cmcontinue")

        if not cmcontinue:
            break
        time.sleep(0.3)

    return titles


def get_existing_titles() -> Set[str]:
    """从现有 wiki_data.md 读取已有页面标题。"""
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


def get_pages_wikitext(titles: List[str]) -> Dict[str, str]:
    """批量获取页面 wikitext（50 条一批）。"""
    result = {}
    for batch_start in range(0, len(titles), 50):
        batch = list(set(titles[batch_start:batch_start + 50]))
        params = {
            "titles": "|".join(batch),
            "redirects": "1",
            "prop": "revisions",
            "rvprop": "content",
            "format": "json",
        }
        data = api_call(params)
        if not data or "query" not in data:
            continue

        query = data["query"]

        # 标题映射：from → to
        title_map = {}
        if "redirects" in query:
            for rd in query["redirects"]:
                title_map[rd["from"]] = rd["to"]
        if "normalized" in query:
            for n in query["normalized"]:
                if n["from"] not in title_map:
                    title_map[n["from"]] = n["to"]

        # 获取内容
        pages = query.get("pages", {})
        content_by_title = {}
        for page_id, page_data in pages.items():
            if page_id == "-1":
                continue
            title = page_data.get("title", "")
            revisions = page_data.get("revisions", [])
            if revisions:
                content_by_title[title] = revisions[0].get("*", "")

        # 还原到原始标题
        for orig in batch:
            target = orig
            seen = set()
            while target in title_map and target not in seen:
                seen.add(target)
                target = title_map[target]
            if target in content_by_title:
                result[orig] = content_by_title[target]

        print(f"    批次 {batch_start//50+1}: 获取 {len(result)}/{len(batch)} 篇", end="", flush=True)

        # 统计获取情况
        n_found = sum(1 for t in batch if t in result)
        n_missing = len(batch) - n_found
        print(f" (找到 {n_found}, 缺失 {n_missing})")

        time.sleep(1.0)

    return result


# ── 文档处理 ──

def clean_wikitext(wt: str) -> str:
    text = wt
    # HTML 注释
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # <ref>
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*/>", "", text)
    # 块级标签
    for tag in ["gallery", "nowiki", "pre", "code", "poem", "includeonly", "noinclude"]:
        text = re.sub(f"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL)
    # 文件/图片
    text = re.sub(r"\[\[File:[^\]]*\]\]", "", text)
    text = re.sub(r"\[\[Image:[^\]]*\]\]", "", text)
    # 分类
    text = re.sub(r"\[\[Category:[^\]]*\]\]", "", text)
    # 跨语言链接
    text = re.sub(r"\[\[[a-z]{2,3}:[^\]]*\]\]", "", text)
    # 多媒体
    text = re.sub(r"\[\[Media:[^\]]*\]\]", "", text)

    # 表格
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
                content = stripped.lstrip("|!-").strip()
                if content:
                    cleaned_lines.append(content)
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # 模板 {{...}}
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

    # 内部链接
    text = re.sub(r"\[\[([^\]|]*?)\|([^\]]*?)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*?)\]\]", r"\1", text)

    # 外部链接
    text = re.sub(r"\[https?://[^\s\[\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\[\]]+\]", "", text)

    # 内联标签
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<hr\s*/?>", "\n---\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(div|span|center|small|big|sup|sub|u|s|strike|abbr|tt|code|blockquote|cite|table|tr|td|th|tbody|thead|caption|colgroup|col)[^>]*>", "", text, flags=re.IGNORECASE)

    # 粗斜体
    text = re.sub(r"'''(.*?)'''", r"\1", text)
    text = re.sub(r"''(.*?)''", r"\1", text)

    # 多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^[ \t]+|[ \t]+$", "", text, flags=re.MULTILINE)

    # 残余标签
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


def render_document(title: str, category: str, wikitext: str) -> Optional[str]:
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
        f"- 类别：{category}\n"
        f"- 标识：{slug}\n"
        f"- 来源：wiki/{title.replace(' ', '_')}\n\n"
        f"{text}"
    )


# ── 主流程 ──

def match_existing_by_names(existing: Set[str]) -> Set[str]:
    """构建规范化名称集合用于模糊匹配。"""
    normalized = set()
    for name in existing:
        normalized.add(name.lower().strip())
        # 去掉括号后缀
        clean = re.sub(r"\s*\(.*?\)\s*", "", name).strip().lower()
        normalized.add(clean)
    return normalized


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Wiki 数据扩充器")
    parser.add_argument("--dry-run", action="store_true", help="试跑预览不写文件")
    parser.add_argument("--category", "-c", help="只补指定分类")
    args = parser.parse_args()

    # 读取已有页面
    existing = get_existing_titles()
    existing_norm = match_existing_by_names(existing)
    print(f"📖 现有 Wiki 页面: {len(existing)} 篇")
    print()

    # 逐分类扫描
    all_new_docs: List[str] = []
    total_new = 0

    for cat_name, doc_cat in CATEGORIES.items():
        if args.category and doc_cat != args.category:
            continue

        print(f"📂 扫描分类: {cat_name} → {doc_cat}")

        # 获取所有页面
        all_titles = get_all_category_members(cat_name)
        print(f"  分类下共 {len(all_titles)} 个页面")

        # 过滤已有
        new_titles = []
        for t in all_titles:
            name = page_title_to_name(t)
            name_lower = name.lower().strip()
            slug = t.lower().replace("_", "-").replace("'", "").replace("(", "").replace(")", "").replace(",", "")
            # 检查是否已有（名称匹配或 slug 匹配）
            if name in existing or name_lower in existing_norm or name_lower.replace(" ", "") in existing_norm:
                continue
            # 也检查 wiki_data.md 中已存在的标识
            new_titles.append(t)

        if not new_titles:
            print(f"  没有新页面需要采集")
            print()
            continue

        print(f"  新页面: {len(new_titles)} 个")
        if args.dry_run:
            for t in sorted(new_titles):
                print(f"    · {t}")
            print()
            continue

        # 批量获取内容
        print(f"  正在获取内容...")
        pages = get_pages_wikitext(new_titles)
        print(f"  成功获取 {len(pages)}/{len(new_titles)} 篇")

        # 渲染文档
        docs = []
        for title, wt in sorted(pages.items()):
            doc = render_document(title, doc_cat, wt)
            if doc:
                docs.append(doc)
                name = page_title_to_name(title)
                print(f"    ✅ {name} ({len(doc)} chars)")
            else:
                print(f"    ⏭️ {title} (跳过)")

        if docs:
            all_new_docs.extend(docs)
            total_new += len(docs)
            print(f"  新增 {len(docs)} 篇文档")

        print()

    # 统计
    print("=" * 50)
    print(f"📊 共新增 {total_new} 篇文档")

    if not all_new_docs:
        print("没有新增内容。")
        return

    if args.dry_run:
        print("(dry-run 模式，未写入)")
        return

    # 追加到 wiki_data.md
    output_path = DATA_DIR / "wiki_data.md"

    # 读取现有内容
    existing_content = output_path.read_text(encoding="utf-8") if output_path.exists() else ""

    # 去掉末尾的 --- 分隔符和空行
    existing_content = existing_content.rstrip() + "\n\n"

    # 生成新内容
    new_content = ""
    for doc in all_new_docs:
        new_content += doc + "\n\n---\n\n"

    # 追加
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(existing_content)
        f.write(new_content)

    # 更新文档总数注释
    total_docs = len(re.split(r"(?=^#\s*文档)", existing_content + new_content, flags=re.MULTILINE)) - 1
    # 更新开头统计行
    lines = output_path.read_text(encoding="utf-8").split("\n")
    for i, line in enumerate(lines):
        if line.startswith("文档总数："):
            lines[i] = f"文档总数：{total_docs}"
            break
    output_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"✅ 已追加到 {output_path}")
    print(f"📊 总计: {total_docs} 篇文档（新增 {total_new} 篇）")


if __name__ == "__main__":
    main()
