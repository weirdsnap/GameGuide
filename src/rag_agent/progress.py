"""用户进度推断（防剧透 P1 的「上界从哪来」）。

纯函数：输入游戏、本轮问题、history，输出 `Progress`。只做保守推断：
  - 明确的章节/幕说法（第 2 章 / 第二章 / Act 2 / 第 3 幕）；
  - 高置信度的 BG3 地名（最后之光旅店=Act2、下城区=Act3）。
推断不出就返回 level=None，交给提示词层的「不问进度、按提问所指作答」策略。

设计口径见 docs/SPOILER_CONTROL_PLAN.md §3：
  - 进度单调取 max（多来源取最大）；
  - 提问即授权：本轮问题点到某一幕，level 至少抬到那一幕；
  - 只扫 user 消息，不拿助理答案反推进度（避免模型自己把进度越推越高）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from rag_agent.game_registry import AVAILABLE_GAMES


@dataclass(frozen=True)
class Progress:
    """某一轮的进度快照。axis=none 时 level 恒为 None。"""

    game: str
    axis: str
    level: Optional[int]
    source: str = "none"          # question | history | none
    evidence: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return self.level is not None


_CN_NUM = {"一": 1, "二": 2, "三": 3, "1": 1, "2": 2, "3": 3}

# 明确说法：第二章 / 第2幕 / Act 2 / act2
_EXPLICIT_PATTERNS = (
    re.compile(r"第\s*([一二三123])\s*[章幕]"),
    re.compile(r"\bact\s*([123])\b", re.IGNORECASE),
    re.compile(r"\bact\s*([一二三])\b", re.IGNORECASE),
)

# 高置信度地名 → 幕。宁少勿多：只收「提到基本就等于人在这一幕」的锚点。
# 与 api_server.SPOILER_TERMS 的 act 轴保持一致；那边是审计词表（含 L1-L3），
# 这里是进度推断，语义不同，暂不强行合并。
_ACT_ANCHORS: dict[int, tuple[str, ...]] = {
    2: (
        "最后之光旅店", "月出之塔", "大陵墓", "莎尔的试炼",
        "石匠工会", "mason's guild", "grand mausoleum", "gauntlet of shar",
        "moonrise towers", "nightsong", "夜之歌", "塞伦涅", "塞伦捏",
        "凯瑟里克",
    ),
    3: (
        "下城区", "利文顿", "钢铁卫士铸造厂", "主脑", "戈塔什", "奥林",
        "lower city", "rivington", "netherbrain", "steel watch foundry",
    ),
}


def _iter_user_texts(history: Optional[Iterable[dict]]) -> Iterable[str]:
    for message in history or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str) and content.strip():
            yield content


def _explicit_levels(text: str) -> Iterable[tuple[int, str]]:
    for pattern in _EXPLICIT_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1)
            level = _CN_NUM.get(raw) or _CN_NUM.get(raw.lower())
            if level:
                yield level, match.group(0)


def _anchor_levels(text: str) -> Iterable[tuple[int, str]]:
    low = text.lower()
    for level, anchors in _ACT_ANCHORS.items():
        for anchor in anchors:
            if anchor in low:
                yield level, anchor


def _matches(text: str) -> list[tuple[int, str]]:
    return list(_explicit_levels(text)) + list(_anchor_levels(text))


def infer_progress(game: str, question: str,
                   history: Optional[Iterable[dict]] = None) -> Progress:
    """推断本轮可用的进度上界；只对 spoiler_axis == "act" 的游戏生效。"""
    axis = AVAILABLE_GAMES.get(game, {}).get("spoiler_axis", "none")
    if axis != "act":
        return Progress(game=game, axis=axis, level=None)

    found: list[tuple[int, str, str]] = []      # (level, evidence, source)
    for text in _iter_user_texts(history):
        for level, term in _matches(text):
            found.append((level, term, "history"))
    for level, term in _matches(question or ""):
        found.append((level, term, "question"))

    if not found:
        return Progress(game=game, axis=axis, level=None)

    level = max(item[0] for item in found)
    evidence = []
    for lv, term, _source in found:
        if lv == level and term not in evidence:
            evidence.append(term)
    source = "question" if any(
        lv == level and src == "question" for lv, _t, src in found) else "history"
    return Progress(game=game, axis=axis, level=level, source=source,
                    evidence=tuple(evidence[:5]))


def progress_prompt_note(progress: Progress) -> str:
    """把进度写成提示词；未知进度注入「不得替用户假设章节」（I-14）。

    早期版本未知时返回空串（方案 C，不追问），但线上实测：零约束下模型
    会按世界知识往游戏中期（第二章）猜，新会话问 act3 物品也被安到第二章。
    所以对 act 轴游戏，未知也要给一句「不许猜」；非 act 轴游戏仍返回空串。
    """
    if not progress.known:
        if progress.axis == "act":
            return (
                "\n\n## 用户进度（系统注入）\n"
                "- 用户没有报过进度：不要替用户假设章节。\n"
                "- 条目属于哪一章就按哪一章说；不确定章节时不写章节，"
                "不要出现「第二章」「第三章」这类猜测性表述。"
            )
        return ""
    evidence = "、".join(progress.evidence) or "对话"
    return (
        "\n\n## 用户进度（系统注入，优先级高于历史）\n"
        f"- 当前进度 = 第 {progress.level} 章（Act {progress.level}，依据：{evidence}）。\n"
        "- 这是硬上界：不得正向描述第 "
        f"{progress.level} 章之后的地点、任务、敌人或结局；"
        "必须提及时只给占位提示。\n"
        "- 进度已知时，不要再追问用户玩到第几章。"
    )
