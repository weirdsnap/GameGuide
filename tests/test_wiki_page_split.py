"""refetch_wiki.py 的分页格式：页面之间用 `---` 分隔，页内小节也是 `##`。

背景（2026-11-25 实查）：terraria 中文 wiki 全量替换（0395909）后重建库，
31248 块里 **48.1%** 的 title 是「制作 / 历史 / 花絮 / 备注 / 小贴士 / 另见」
这类页内小节名（制作 4322、历史 4255、花絮 1979…），7347 个不同 title 对应
只有 3031 个页面。检索「克苏鲁之眼怎么召唤」命中的是「部位」「另见」
「Demon's Eye」这些碎片，页面本身（title 确实是「克苏鲁之眼」）没进 top-k。

根因：建库分页器只按 `## ` 切（mac_build 的格式 B、ingest_game 的格式 2），
没有利用 refetch 输出的页面分隔。refetch 的格式其实分得出来：

    ## 页面名      ← 页面标题：`---` 之后第一个 ##
    ## 小节名      ← 页内小节
    ---            ← 页面分隔，行数恰好等于「总页数」（terraria 3031 / silksong 79）

修法（Mac 侧，scripts/tool/mac_build.py + ingest_game.py）：分页前先按
`(?m)^---\\s*$` 切页，页内再按 `##` 切小节；小节块的 title 必须继承页面名，
小节名另存 section 字段。修完删掉本文件的 xfail 标记。

本文件是**契约测试**：钉住「分页以页面为单位」这条契约。当前实现不满足，
所以标 xfail；实现修好后会 XPASS（strict=False 不报错），届时清理标记。
"""

import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "tool"))
sys.path.insert(0, str(ROOT / "src"))

import mac_build  # noqa: E402

# refetch_wiki.py 的真实产出形状：两页，每页各带一个页内小节
SAMPLE = """# terraria Wiki Data (中文)

来源: https://terraria.wiki.gg/zh/api.php
总页数: 2

## 克苏鲁之眼

克苏鲁之眼是一个困难模式之前的 Boss。

## 召唤

在夜晚使用可疑眼球可以召唤克苏鲁之眼。

---

## 泰拉刃

泰拉刃是一把困难模式的可制作剑。

## 制作

泰拉刃由真断钢剑与断钢剑在秘银砧合成。

---

"""


@pytest.mark.xfail(
    strict=False,
    reason="分页器还没按 `---` 切页：页内小节被当成独立页面（Mac 侧待修，"
           "见 docs/MAC_SIDE_TASKS.md 与 docs/CHAT_HISTORY_RENDER_PLAN.md 同批记录）",
)
def test_refetch_格式按页面分页而不是按小节分页():
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as f:
        f.write(SAMPLE)
        path = f.name
    try:
        docs = mac_build.load_wiki_documents(path, language="zh")
    finally:
        Path(path).unlink(missing_ok=True)

    titles = [d["metadata"]["title"] for d in docs]
    assert len(docs) == 2, f"应按页分页得到 2 条，实际 {len(docs)} 条: {titles}"
    assert set(titles) == {"克苏鲁之眼", "泰拉刃"}, titles

    by_title = {d["metadata"]["title"]: d["content"] for d in docs}
    # 小节正文必须并进所属页面，而不是自成一块
    assert "可疑眼球" in by_title["克苏鲁之眼"]
    assert "秘银砧" in by_title["泰拉刃"]


def test_旧格式_文档标记分页仍然正常():
    """回归保护：格式 A（`# 文档：标题`）不能被新分页逻辑带坏。"""
    sample = """# 文档：螳螂爪
- 类别: 护符

螳螂爪可以攀墙。

# 文档：深巢
- 类别: 区域

深巢在地图下方。
"""
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as f:
        f.write(sample)
        path = f.name
    try:
        docs = mac_build.load_wiki_documents(path, language="zh")
    finally:
        Path(path).unlink(missing_ok=True)

    assert [d["metadata"]["title"] for d in docs] == ["螳螂爪", "深巢"]
