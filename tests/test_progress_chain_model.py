"""进度轴 S1：chain 模型加载与 BG3 行为等价契约。

S1 章程：config/progress/ 落地 + ChainModel 接线，替换 progress.py 的
硬编码锚点，**BG3 行为不变**。这里钉住两类事：

1. 加载层：BG3 有 chain 配置、没有配置的游戏返回 None、lru_cache 生效；
2. 推断等价：锚点 / 明确说法 / max 合并 / evidence / source 与硬编码时代一致，
   特别是「第一章锚点未纳入」——「幽暗地域」「地表」不应推出进度
   （草案加过第一章锚点含泛词，S1 按「宁少勿多」剔了，见配置 _notes）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rag_agent.progress import infer_progress            # noqa: E402
from rag_agent.progress_model import load_chain_model    # noqa: E402


def test_BG3有chain配置且字段齐全():
    model = load_chain_model("baldurs_gate3")
    assert model is not None
    assert model.game == "baldurs_gate3"
    assert model.max_rank == 3
    assert model.axis_label == "章"
    assert set(model.anchors) == {2, 3}          # 第一章锚点未纳入（行为等价）
    assert len(model.explicit_patterns) == 2
    assert model.reviewed is True


def test_无配置的游戏返回None():
    assert load_chain_model("不存在的游戏") is None
    # silksong（axis=act 但没有 chain 配置）不再误用 BG3 锚点
    progress = infer_progress("silksong", "最后之光旅店在哪")
    assert progress.level is None


def test_锚点推断与硬编码时代一致():
    assert infer_progress("baldurs_gate3", "月出之塔怎么打").level == 2
    assert infer_progress("baldurs_gate3", "下城区在哪").level == 3
    # 第一章锚点未纳入：这些词在硬编码时代也推不出进度
    assert infer_progress("baldurs_gate3", "幽暗地域怎么去").level is None
    assert infer_progress("baldurs_gate3", "地表最强武器是什么").level is None


def test_明确说法推断一致():
    assert infer_progress("baldurs_gate3", "我打到第二章了").level == 2
    assert infer_progress("baldurs_gate3", "act 3 内容").level == 3
    # 「Act二」不匹配：\b 在中文与字母之间不成立（二后面跟「了」也没有边界），
    # 与硬编码时代的三个正则行为完全一致——中文语境请用「第X章/幕」说法
    assert infer_progress("baldurs_gate3", "我打到Act二了").level is None
    assert infer_progress("baldurs_gate3", "我打到第2幕了").level == 2


def test_max合并与证据收集不变():
    history = [
        {"role": "user", "content": "我在最后之光旅店卡关了"},
        {"role": "assistant", "content": "可以试试绕后"},
        {"role": "user", "content": "下城区的任务怎么做"},  # 只扫 user 消息
    ]
    progress = infer_progress("baldurs_gate3", "这件装备在哪", history)
    assert progress.level == 3                    # 多来源取 max
    assert progress.source == "history"           # max 来自历史里的「下城区」
    assert "下城区" in progress.evidence
    # max 来自本轮问题时 source 才是 question
    progress_q = infer_progress("baldurs_gate3", "下城区的装备在哪", history)
    assert progress_q.level == 3
    assert progress_q.source == "question"


def test_只扫user消息_助理答案不反推进度():
    history = [
        {"role": "assistant", "content": "月出之塔在第二章"},
    ]
    progress = infer_progress("baldurs_gate3", "这件装备在哪", history)
    assert progress.level is None
