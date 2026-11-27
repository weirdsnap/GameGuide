"""证据抽取（进度轴 S2，设计文档 §5）：从用户话里榨进度，不调 LLM。

输入：本轮 question + history（只扫 role == "user" 的消息，§5.2-4）。
输出：Evidence——抬 lo 的正向证据 + reverse 反向证据。

词表完全由模型传入（evidence_targets），本模块不含任何游戏名（§6 硬约束）。

§5.1 证据类型与本文实现的对照（conf 只是文档口径，合并规则见 §5.2）：
  explicit_progress  「第 2 幕 / Act 2」——模型的 explicit_patterns
  arrival            「我在 X / 我到了 X / 刚进 X / 现在在 X」
  howto_at           「X 怎么打 / X 在哪拿 / 怎么过 X」（问怎么打说明已碰上）
  reverse            「怎么去 X / X 在哪 / X 怎么进」（= 还没到过 X）
  mention_only       「X 是什么 / 有什么用」——不抬 lo，忽略
  ability_gain / boss_kill：锚点版没建模 abilities/bosses，S4 补。

一个词的多个说法优先级：arrival > howto_at > reverse > mention_only，
同一文本里一个词只归一类（「圣堡在哪拿」按 howto_at，不会误记 reverse）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple


@dataclass(frozen=True)
class Evidence:
    """一轮的进度证据汇总。

    lo_facts: (act, 命中词, 类型)——正向证据，lo = max(act)。
    not_reached: 区域名元组——reverse 证据（「怎么去 X」），留档供渲染/剪枝。
    source: 最「新鲜」的证据来源（question 优先于 history）。
    """
    lo_facts: Tuple[Tuple[int, str, str], ...] = ()
    not_reached: Tuple[str, ...] = ()
    source: str = "none"               # question | history | none


# ── 句式模板（纯中文问法；英文区域名照常嵌入）──

_ARRIVAL_BEFORE = ("我在", "我到了", "刚到", "刚进", "现在在", "已经到", "打到了", "走到")
_ARRIVAL_AFTER = ("到了", "打完了")

_HOWTO_BEFORE = ("怎么打", "怎么过", "如何打", "怎么打")
_HOWTO_AFTER = ("怎么打", "在哪拿", "怎么过", "怎么拿", "打法", "攻略")

_REVERSE_BEFORE = ("怎么去", "怎么进", "如何去", "怎么进入")
_REVERSE_AFTER = ("在哪", "在哪里", "怎么去", "怎么走", "怎么进")

# 「在哪拿/在哪买/在哪刷」是 howto_at（已碰上在找具体位置），不是 reverse
_REVERSE_AFTER_EXCLUDE = ("拿", "买", "刷", "掉落", "换")

_MENTION_AFTER = ("是什么", "有什么用", "强不强", "厉害吗", "好用吗", "是什么地方")


def _contains(text: str, term: str) -> bool:
    return term.lower() in text.lower()


def _classify_term(text: str, term: str) -> Optional[str]:
    """一个词在一段文本里的证据归类；不匹配返回 None。

    顺序即优先级：arrival > howto_at > reverse > mention_only（见 docstring）。
    关键词与词表词之间允许 0-4 个任意字符的间隙（「刚进 Deep Docks」的
    空格、「圣堡的怪怎么打」的「的怪」都是自然说法）。
    """
    low = text.lower()
    t = term.lower()
    if t not in low:
        return None
    esc = re.escape(t)
    gap = r".{0,4}?"

    def _before(kw: str) -> bool:   # 关键词在词表词之前（「怎么去 X」）
        return re.search(re.escape(kw) + gap + esc, low) is not None

    def _after(kw: str) -> bool:    # 关键词在词表词之后（「X 在哪」）
        return re.search(esc + gap + re.escape(kw), low) is not None

    if any(_before(kw) for kw in _ARRIVAL_BEFORE) or \
       any(_after(kw) for kw in _ARRIVAL_AFTER):
        return "arrival"
    if any(_before(kw) for kw in _HOWTO_BEFORE) or \
       any(_after(kw) for kw in _HOWTO_AFTER):
        return "howto_at"
    if any(_before(kw) for kw in _REVERSE_BEFORE):
        return "reverse"
    for kw in _REVERSE_AFTER:
        if _after(kw) and not any(
                re.search(esc + gap + re.escape(kw) + re.escape(ex), low)
                for ex in _REVERSE_AFTER_EXCLUDE):
            return "reverse"
    if any(_after(kw) for kw in _MENTION_AFTER):
        return "mention_only"
    return None


_CN_NUM = {"一": 1, "二": 2, "三": 3, "1": 1, "2": 2, "3": 3}


def _explicit_acts(text: str, model) -> Iterable[Tuple[int, str]]:
    """明确说法「第 2 幕 / Act 2」→ act（与 chain 同口径，正则来自配置）。"""
    for pattern in model.explicit_patterns:
        for match in pattern.finditer(text):
            raw = match.group(1)
            act = _CN_NUM.get(raw) or _CN_NUM.get(raw.lower())
            if act:
                yield act, match.group(0)


def _iter_user_texts(history: Optional[Iterable[dict]]) -> Iterable[str]:
    for message in history or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str) and content.strip():
            yield content


def extract_evidence(question: str, history: Optional[Iterable[dict]],
                     model) -> Evidence:
    """从 question + user history 抽取进度证据（§5）。

    model 只需提供 evidence_targets()（dag 词表）与 explicit_patterns；
    不含任何游戏特定分支（§6 硬约束：游戏差异全在数据里）。
    """
    targets = model.evidence_targets()
    lo_facts: list = []
    not_reached: list = []
    latest_source = "none"

    # question 在后处理，优先级高于 history（§5.2-5「以更新的那条为准」）
    for source, texts in (("history", _iter_user_texts(history)),
                          ("question", [question or ""])):
        for text in texts:
            if not text:
                continue
            hit = False
            for act, term in _explicit_acts(text, model):
                lo_facts.append((act, term, "explicit_progress"))
                hit = True
            for aid, act, terms in targets:
                if act is None:
                    continue  # 未校对的区域不参与推断（§4.2 act 可为 null）
                for term in terms:
                    kind = _classify_term(text, term)
                    if kind in ("arrival", "howto_at"):
                        lo_facts.append((act, term, kind))
                        hit = True
                        break  # 同一区域一个文本只记一条
                    elif kind == "reverse":
                        name = model.areas[aid]["name"]
                        if name not in not_reached:
                            not_reached.append(name)
                        hit = True
                        break
            if hit:
                latest_source = source

    return Evidence(lo_facts=tuple(lo_facts),
                    not_reached=tuple(not_reached),
                    source=latest_source)
