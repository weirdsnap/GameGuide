#!/usr/bin/env python3
"""进度模型草案的校对辅助工具（非运行时依赖）。

读 `docs/progress_drafts/<game>.<kind>.json` 与 `games/<game>/data/wiki_data.md`，
输出一份「待填清单」：每个区域 / 能力的 act 状态、wiki 里能当依据的原句、
以及草案与 wiki 打架的地方。

用途：S4 数据校对（设计见 docs/PROGRESS_AXIS_DESIGN.md §5、数据规则见
docs/progress_drafts/README.md）。**它只提线索，不下判断**，最终填什么由人定。

用法：
    python3 scripts/analysis/progress_draft_probe.py --game silksong
    python3 scripts/analysis/progress_draft_probe.py --game silksong --out docs/progress_drafts/silksong.todo.md
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAFT_DIR = REPO_ROOT / "docs" / "progress_drafts"

# 能当 act / 进入条件依据的关键词
CLUE_PATTERNS = (
    r"\bact\s*[123]\b",
    r"\bearly-game\b",
    r"\bmid-game\b",
    r"\blate-game\b",
    r"accessible",
    r"\brequires?\b",
    r"\bunlock",
    r"\bbefore\b",
    r"\bafter\b",
    r"\bonly\b",
)


def load_draft(game: str) -> tuple[Path, dict]:
    matches = sorted(DRAFT_DIR.glob(f"{game}.*.json"))
    if not matches:
        raise SystemExit(f"没找到 {game} 的草案：{DRAFT_DIR}/{game}.*.json")
    path = matches[0]
    return path, json.loads(path.read_text(encoding="utf-8"))


# wiki_data.md 里页面标题和页内小节都是 `## `，这些标题不算页面
SUBSECTION_NAMES = {
    x.lower() for x in (
        "Dialogue", "Location", "Trivia", "Behavior", "Behaviour",
        "Behavior and Tactics", "Behaviour and Tactics", "Behaviour and tactics",
        "Enemies", "NPCs", "Points of Interest", "Points of interest",
        "Overview", "Connections", "Key Items and Abilities", "Abilities",
        "Inventory", "Features", "Main content", "Development", "Lore",
        "In-game events", "Quests", "Use", "How to Acquire", "Variations",
        "Achievements", "Music theme", "Rewards", "Health and Silk", "Silk",
        "Needolin dialogue", "Needolin Dialogue", "Silk Soar", "Sub-area",
        "Sub-Area", "Description", "How to Access", "Background", "Gather",
        "Wayfarer", "Hunt", "Grand Hunt",
    )
}


def split_pages(text: str) -> dict[str, str]:
    """按 markdown 二级标题切页：{页名: 正文}。

    wiki_data.md 的页内小节也用 `## `，所以要把已知小节名排除，否则
    “Dialogue”“Location” 会被当成页面。
    """
    pages: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            if title.lower() in SUBSECTION_NAMES:
                continue  # 页内小节：保持归属当前页
            current = title
            pages[current] = []
        elif current:
            pages[current].append(line)
    return {name: "\n".join(body).strip() for name, body in pages.items()}


def clue_lines(body: str) -> list[str]:
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
        low = sentence.lower()
        if any(re.search(p, low) for p in CLUE_PATTERNS):
            s = sentence.strip()
            if 10 <= len(s) <= 300:
                out.append(s)
    return out


def find_page(key: str, name: str, pages: dict[str, str]) -> tuple[str, str] | None:
    """按区域 id 或名称找页：先精确，再忽略大小写/标点包含。"""
    def norm(x: str) -> str:
        return re.sub(r"[^a-z0-9]", "", x.lower())

    for title, body in pages.items():
        if norm(title) == norm(name) or norm(title) == norm(key):
            return title, body
    for title, body in pages.items():
        nt, nn = norm(title), norm(name)
        if nt and nn and (nn in nt or nt in nn) and min(len(nt), len(nn)) >= 4:
            return title, body
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--out", help="写到文件（markdown）；省略则打印到 stdout")
    args = ap.parse_args()

    draft_path, draft = load_draft(args.game)
    wiki_path = REPO_ROOT / "games" / args.game / "data" / "wiki_data.md"
    pages = split_pages(wiki_path.read_text(encoding="utf-8")) if wiki_path.is_file() else {}

    lines: list[str] = []
    add = lines.append
    add(f"# {args.game} 进度草案待填清单")
    add("")
    add(f"> 自动生成，**只提线索不下判断**。草案：`{draft_path.relative_to(REPO_ROOT)}`，"
        f"wiki：`{wiki_path.relative_to(REPO_ROOT)}`（{len(pages)} 页）")
    add("")

    if draft.get("kind") != "dag":
        add("（当前只支持 dag 模型，chain 模型的锚点表人工维护即可。）")
        _emit(lines, args)
        return

    areas = draft.get("areas", {})
    abilities = draft.get("abilities", {})

    # 1) 区域 act 待填
    missing_act = [k for k, v in areas.items() if v.get("act") is None]
    add(f"## 1. act 待定（{len(missing_act)}/{len(areas)}）")
    add("")
    for aid in missing_act:
        area = areas[aid]
        add(f"### {area['name']} (`{aid}`)")
        found = find_page(aid, area["name"], pages)
        if not found:
            add("- wiki 里没有对应页面，需从地图/攻略补。")
        else:
            title, body = found
            clues = clue_lines(body)
            add(f"- wiki 页：`{title}`")
            if clues:
                for c in clues[:4]:
                    add(f"  - {c}")
            else:
                add("  - 该页没有 act / 进入条件类原句。")
            if re.search(r"\bact\s*[123]\b", body, re.I) is None:
                add("  - 全文未出现 `Act N` 字样。")
        add("")

    # 2) 前置边待填
    no_edge = [
        k for k, v in areas.items()
        if not v.get("requires_areas") and not v.get("requires_gates")
    ]
    add(f"## 2. 前置信息为空（{len(no_edge)}/{len(areas)}）")
    add("")
    add("这些区域的 `requires_areas` / `requires_gates` 都是空：")
    add("")
    for aid in no_edge:
        add(f"- `{aid}`（{areas[aid]['name']}）")
    add("")
    add("> 注意：wiki 的 `Connections` 段是**无向邻接**，不能直接当 `requires_areas` 写。")
    add("")

    # 3) 能力获取地缺失
    add("## 3. 能力获取地待补")
    add("")
    for aid, ab in abilities.items():
        if not ab.get("from"):
            add(f"- `{aid}`（{ab['name']}）：`from` 为空")
    add("")

    # 4) 一致性：已填 act 与 wiki 线索是否打架
    add("## 4. 已填 act 与 wiki 线索对照")
    add("")
    for aid, area in areas.items():
        if area.get("act") is None:
            continue
        found = find_page(aid, area["name"], pages)
        act_hits = re.findall(r"act\s*([123])", found[1], re.I) if found else []
        flag = ""
        if act_hits and str(area["act"]) not in act_hits:
            flag = f"  ⚠ wiki 出现 Act {'/'.join(sorted(set(act_hits)))}"
        add(f"- `{aid}` = Act {area['act']}{flag}")
    add("")

    _emit(lines, args)


def _emit(lines: list[str], args) -> None:
    text = "\n".join(lines) + "\n"
    if args.out:
        out = REPO_ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"写入 {out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
