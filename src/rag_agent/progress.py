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

from dataclasses import dataclass
from typing import Iterable, Optional

from rag_agent.game_registry import AVAILABLE_GAMES
from rag_agent.progress_model import load_chain_model, load_dag_model


@dataclass(frozen=True)
class Progress:
    """某一轮的进度快照。axis=none 时 level 恒为 None。"""

    game: str
    axis: str
    level: Optional[int]
    source: str = "none"          # question | history | none
    evidence: tuple[str, ...] = ()
    axis_label: str = "章"        # S2：dag 模型用「幕」，chain 仍是「章」
    not_reached: tuple[str, ...] = ()  # reverse 证据（§5.1「怎么去 X」）

    @property
    def known(self) -> bool:
        return self.level is not None


_CN_NUM = {"一": 1, "二": 2, "三": 3, "1": 1, "2": 2, "3": 3}


def _iter_user_texts(history: Optional[Iterable[dict]]) -> Iterable[str]:
    for message in history or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str) and content.strip():
            yield content


def _explicit_levels(text: str, model) -> Iterable[tuple[int, str]]:
    """明确说法：第二章 / 第2幕 / Act 2 / act2。正则来自 chain 配置。"""
    for pattern in model.explicit_patterns:
        for match in pattern.finditer(text):
            raw = match.group(1)
            level = _CN_NUM.get(raw) or _CN_NUM.get(raw.lower())
            if level:
                yield level, match.group(0)


def _anchor_levels(text: str, model) -> Iterable[tuple[int, str]]:
    """高置信度地名 → 幕。宁少勿多：只收「提到基本就等于人在这一幕」的锚点。
    词表来自 config/progress/<game>.chain.json；与 api_server.SPOILER_TERMS 的
    act 轴保持一致；那边是审计词表（含 L1-L3），这里是进度推断，语义不同。
    """
    low = text.lower()
    for level, anchors in model.anchors.items():
        for anchor in anchors:
            if anchor in low:
                yield level, anchor


def _matches(text: str, model) -> list[tuple[int, str]]:
    return list(_explicit_levels(text, model)) + list(_anchor_levels(text, model))


def infer_progress(game: str, question: str,
                   history: Optional[Iterable[dict]] = None) -> Progress:
    """推断本轮可用的进度上界；只对 spoiler_axis == "act" 的游戏生效。

    chain 模型（BG3）走锚点/明确说法匹配（S1 起从 config/progress/*.chain.json 读）；
    没有 chain 配置时尝试 dag 模型（S2，丝之歌锚点版）：evidence.py 从用户话里
    抽 arrival/howto/reverse 证据，reviewed:false 下只抬 lo 不剪枝。
    都没有的游戏返回 level=None。
    """
    axis = AVAILABLE_GAMES.get(game, {}).get("spoiler_axis", "none")
    if axis != "act":
        return Progress(game=game, axis=axis, level=None)
    model = load_chain_model(game)
    if model is not None:
        return _infer_chain(game, axis, question, history, model)
    dag = load_dag_model(game)
    if dag is not None:
        return _infer_dag(game, axis, question, history, dag)
    return Progress(game=game, axis=axis, level=None)


def _infer_chain(game: str, axis: str, question: str,
                 history: Optional[Iterable[dict]], model) -> Progress:
    """chain 推断：锚点 + 明确说法，max 合并（原 infer_progress 行为，不改）。"""
    found: list[tuple[int, str, str]] = []      # (level, evidence, source)
    for text in _iter_user_texts(history):
        for level, term in _matches(text, model):
            found.append((level, term, "history"))
    for level, term in _matches(question or "", model):
        found.append((level, term, "question"))

    if not found:
        return Progress(game=game, axis=axis, level=None,
                        axis_label=model.axis_label)

    level = max(item[0] for item in found)
    evidence = []
    for lv, term, _source in found:
        if lv == level and term not in evidence:
            evidence.append(term)
    source = "question" if any(
        lv == level and src == "question" for lv, _t, src in found) else "history"
    return Progress(game=game, axis=axis, level=level, source=source,
                    evidence=tuple(evidence[:5]), axis_label=model.axis_label)


def _infer_dag(game: str, axis: str, question: str,
               history: Optional[Iterable[dict]], model) -> Progress:
    """dag 推断（S2 锚点版）：evidence 抽取 → model.bound。

    reviewed:false 数据只抬 lo（§4.2/§5.3），not_reached 留档渲染不剪枝。
    """
    from rag_agent.evidence import extract_evidence
    ev = extract_evidence(question, history, model)
    bound = model.bound(
        lo_facts=[(act, term) for act, term, _kind in ev.lo_facts],
        not_reached=list(ev.not_reached),
        source=ev.source,
    )
    if not bound.known:
        return Progress(game=game, axis=axis, level=None,
                        axis_label=model.axis_label,
                        not_reached=bound.not_reached)
    return Progress(game=game, axis=axis, level=bound.lo, source=bound.source,
                    evidence=bound.evidence[:5], axis_label=model.axis_label,
                    not_reached=bound.not_reached)


def progress_prompt_note(progress: Progress) -> str:
    """把进度写成提示词；未知进度注入「不得替用户假设章节」（I-14）。

    早期版本未知时返回空串（方案 C，不追问），但线上实测：零约束下模型
    会按世界知识往游戏中期（第二章）猜，新会话问 act3 物品也被安到第二章。
    所以对 act 轴游戏，未知也要给一句「不许猜」；非 act 轴游戏仍返回空串。
    S2：文案用 axis_label（chain=章、dag=幕）；reverse 证据（「怎么去 X」）
    以「未到 X」注入，帮模型区分「已确认到达」与「明确没到」。
    """
    label = progress.axis_label
    if not progress.known:
        if progress.axis == "act":
            return (
                "\n\n## 用户进度（系统注入）\n"
                f"- 用户没有报过进度：不要替用户假设其打到第几{label}。\n"
                f"- 条目属于哪一{label}就按哪一{label}说；不确定时不写，"
                "不要出现猜测性的章节/幕次表述。"
            )
        return ""
    evidence = "、".join(progress.evidence) or "对话"
    not_reached = (
        f"\n- 用户明确还没到过：{'、'.join(progress.not_reached)}"
        "——这些地点的具体内容按占位提示处理，不主动展开。"
        if progress.not_reached else ""
    )
    return (
        "\n\n## 用户进度（系统注入，优先级高于历史）\n"
        f"- 当前进度 = 第 {progress.level} {label}"
        f"（Act {progress.level}，依据：{evidence}）。\n"
        "- 这是硬上界：不得正向描述第 "
        f"{progress.level} {label}之后的地点、任务、敌人或结局；"
        "必须提及时只给占位提示。" + not_reached + "\n"
        "- 进度已知时，不要再追问用户玩到第几章/幕。"
    )
