#!/usr/bin/env python3
"""
合并 HallownestAPI + Wiki 知识库为统一的 hallownest_knowledge.md。

输出格式与 embed_offline.py 的 parse_markdown_docs() 兼容：
  # 文档 [N] Title
  - 类别：category
  - 标识：slug
  - 路径：source

  [body content]
  ---
"""

import re
from pathlib import Path
from typing import List, Dict


DATA_DIR = Path("/data/learning/agent/data")
OUTPUT = DATA_DIR / "hallownest_knowledge.md"


def _parse_meta_value(line: str, prefix: str) -> str:
    """从 '- 类别：value' 中提取 value。"""
    if line.startswith(prefix):
        val = line[len(prefix):].strip()
        # 去除可能残留的冒号前缀（因旧版解析 bug）
        while val.startswith("："):
            val = val[1:]
        return val
    return ""


def parse_hallownest_docs() -> List[Dict]:
    """读取并解析现有的 hallownest_knowledge.md（如果存在）。"""
    path = DATA_DIR / "hallownest_knowledge.md"
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    # 按 # 文档 标题切割
    chunks = re.split(r"(?=^#\s*文档)", text, flags=re.MULTILINE)
    docs = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.split("\n")

        title_match = re.search(r"^#\s*文档\s*\[\d+\]\s*(.*)", lines[0])
        if not title_match:
            continue
        name = title_match.group(1).strip()

        metadata = {"name": name, "category": "", "slug": "", "source": ""}
        for line in lines[1:]:
            if line.startswith("- 类别："):
                metadata["category"] = _parse_meta_value(line, "- 类别：")
            elif line.startswith("- 标识："):
                metadata["slug"] = _parse_meta_value(line, "- 标识：")
            elif line.startswith("- 路径："):
                metadata["source"] = _parse_meta_value(line, "- 路径：")
            elif line.startswith("- 来源："):
                metadata["source"] = _parse_meta_value(line, "- 来源：")

        # 提取正文（跳过 metadata 行和标题行）
        body_lines = []
        for line in lines:
            if any(line.startswith(p) for p in ("- 类别：", "- 标识：", "- 路径：", "- 来源：", "# 文档")):
                continue
            if line.strip():
                body_lines.append(line)
        content = "\n".join(body_lines).strip()

        if content:
            docs.append({"content": content, "metadata": metadata})

    return docs


def parse_wiki_docs() -> List[Dict]:
    """解析 wiki_data.md，提取所有文档。

    注意：不能用 --- 做文档分隔符，因为 wiki 正文内也有 --- 用作内容分隔。
    改用 # 文档： 作为文档起始标记。
    """
    path = DATA_DIR / "wiki_data.md"
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")

    # 以 # 文档： 作为文档切割点
    chunks = re.split(r"(?=^# 文档[：:])", text, flags=re.MULTILINE)
    docs = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.split("\n")

        title_match = re.search(r"^#\s*文档[：:]\s*(.*)", lines[0])
        if not title_match:
            continue
        name = title_match.group(1).strip()

        metadata = {"name": name, "category": "", "slug": "", "source": ""}
        for line in lines[1:]:
            if line.startswith("- 类别："):
                metadata["category"] = _parse_meta_value(line, "- 类别：")
            elif line.startswith("- 标识："):
                metadata["slug"] = _parse_meta_value(line, "- 标识：")
            elif line.startswith("- 来源："):
                metadata["source"] = _parse_meta_value(line, "- 来源：")
            elif line.startswith("- 路径："):
                metadata["source"] = _parse_meta_value(line, "- 路径：")

        # 提取正文（跳过标题行、metadata 行、空行）
        body_lines = []
        for line in lines:
            if line.startswith(("# 文档", "- 类别：", "- 标识：", "- 来源：", "- 路径：")):
                continue
            if line.strip():
                body_lines.append(line)
        content = "\n".join(body_lines).strip()

        if content:
            docs.append({"content": content, "metadata": metadata})

    return docs


def format_doc(idx: int, doc: Dict) -> str:
    """将单个文档格式化为标准 Markdown 片段。"""
    name = doc["metadata"]["name"]
    category = doc["metadata"]["category"]
    slug = doc["metadata"]["slug"]
    source = doc["metadata"]["source"]
    content = doc["content"]

    lines = [
        f"# 文档 [{idx}] {name}",
        f"- 类别：{category}" if category else "- 类别：general",
        f"- 标识：{slug}" if slug else f"- 标识：{name.lower().replace(' ', '-')}",
        f"- 路径：{source}" if source else "- 路径：generated",
        "",
        content,
    ]
    return "\n".join(lines)


def main():
    print("📖 解析 HallownestAPI 数据...")
    hk_docs = parse_hallownest_docs()
    print(f"  → {len(hk_docs)} 篇")

    print("📖 解析 Wiki 数据...")
    wiki_docs = parse_wiki_docs()
    print(f"  → {len(wiki_docs)} 篇")

    # 去重：对于姓名相同的文档，保留 HallownestAPI 的，在 metadata 中标记 wiki 来源
    hk_names = {d["metadata"]["name"].lower().strip() for d in hk_docs}

    added = 0
    skipped = 0
    for wd in wiki_docs:
        name_lower = wd["metadata"]["name"].lower().strip()
        if name_lower in hk_names:
            # 重叠：追加到已有的 HallownestAPI 文档后面
            for hd in hk_docs:
                if hd["metadata"]["name"].lower().strip() == name_lower:
                    # 追加 wiki 内容（用分隔线标记来源）
                    hd["content"] += f"\n\n---\n*补充来源：Wiki*\n\n" + wd["content"]
                    break
            skipped += 1
        else:
            # Wiki 独有：新增文档
            hk_docs.append(wd)
            added += 1

    print(f"\n📊 合并统计：")
    print(f"  重叠条目：{skipped}（Wiki 内容追加到已有文档）")
    print(f"  Wiki 独有新增：{added}")
    print(f"  文档总数：{len(hk_docs)}")

    # 写出
    # 注意：不能用 --- 做文档分隔，因为正文中也可能包含 ---（如 wiki 策略指南）
    # 改用空行 + 文档标题标记来分割，避免 embed_offline 的 parse 出错
    DOC_SEP = "\n\n"
    all_lines = []
    for i, doc in enumerate(hk_docs, 1):
        all_lines.append(format_doc(i, doc))
    output_text = DOC_SEP.join(all_lines) + "\n"

    OUTPUT.write_text(output_text, encoding="utf-8")
    print(f"\n✅ 已写入：{OUTPUT}")
    print(f"  文件大小：{len(output_text.encode('utf-8')) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
