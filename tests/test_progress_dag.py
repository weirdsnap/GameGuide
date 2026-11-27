"""进度轴 S2 测试：DagModel + evidence 抽取 + 丝之歌锚点版集成。

设计文档：docs/PROGRESS_AXIS_DESIGN.md（迁移路径 S2）。
数据：config/progress/silksong.dag.json（reviewed:false，只抬 lo 不剪枝）。

覆盖：
  - DagModel 加载与 evidence_targets 词表（英文名 + 中文 alias）
  - evidence 抽取的各证据类型（§5.1）：arrival / howto_at / reverse /
    reverse-exclude（在哪拿≠没到）/ mention_only / explicit_progress
  - bound：reviewed:false 下 hi == lo，not_reached 留档不剪枝
  - infer_progress 集成（silksong，axis=act）
  - progress_prompt_note：axis_label（幕）+ 未到区域注入
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rag_agent.evidence import extract_evidence  # noqa: E402
from rag_agent.progress import infer_progress, progress_prompt_note  # noqa: E402
from rag_agent.progress_model import load_dag_model  # noqa: E402


@pytest.fixture(scope="module")
def dag():
    model = load_dag_model("silksong")
    assert model is not None, "config/progress/silksong.dag.json 应能加载"
    return model


# ══════════════════════════════════════════════
#  DagModel 数据层
# ══════════════════════════════════════════════

def test_dag_加载字段(dag):
    assert dag.kind == "dag"
    assert dag.axis_label == "幕"
    assert dag.reviewed is False
    assert dag.max_rank >= 3


def test_dag_词表含中英文(dag):
    targets = {aid: (act, terms) for aid, act, terms in dag.evidence_targets()}
    act, terms = targets["bone-bottom"]
    assert act == 1
    assert "Bone Bottom" in terms
    assert "髓骨之地" in terms


# ══════════════════════════════════════════════
#  evidence 抽取（§5.1 各类型）
# ══════════════════════════════════════════════

def test_evidence_arrival_中文区域名(dag):
    ev = extract_evidence("我在髓骨之地卡住了", None, dag)
    assert (1, "髓骨之地", "arrival") in ev.lo_facts


def test_evidence_arrival_英文区域名(dag):
    ev = extract_evidence("刚进 Deep Docks", None, dag)
    assert (1, "Deep Docks", "arrival") in ev.lo_facts


def test_evidence_howto_at_抬lo(dag):
    """「圣堡怎么打」= 已碰上圣堡内容 → lo 抬到 act 2。"""
    ev = extract_evidence("圣堡的怪怎么打", None, dag)
    assert (2, "圣堡", "howto_at") in ev.lo_facts


def test_evidence_reverse_记未到不抬lo(dag):
    """「怎么去圣堡」= 还没到过圣堡——只记 not_reached，不抬 lo。"""
    ev = extract_evidence("怎么去圣堡", None, dag)
    assert "Citadel" in ev.not_reached
    assert not any(act == 2 for act, _t, _k in ev.lo_facts)


def test_evidence_reverse排除_在哪拿是howto(dag):
    """「X 在哪拿」= 已碰上在找具体位置（howto_at），不是 reverse。"""
    ev = extract_evidence("圣堡钥匙在哪拿", None, dag)
    assert "Citadel" not in ev.not_reached
    assert (2, "圣堡", "howto_at") in ev.lo_facts


def test_evidence_mention_only_不抬lo(dag):
    """「圣堡是什么」= mention_only（可能只是听说），不产生任何 lo 证据。"""
    ev = extract_evidence("圣堡是什么地方", None, dag)
    assert not ev.lo_facts
    assert not ev.not_reached


def test_evidence_explicit_明确幕次说法(dag):
    ev = extract_evidence("第二幕的boss", None, dag)
    assert (2, "第二幕", "explicit_progress") in ev.lo_facts


def test_evidence_只扫user消息(dag):
    """assistant 消息不得用于推断（§5.2-4）。"""
    history = [
        {"role": "assistant", "content": "你现在在髓骨之地"},
        {"role": "user", "content": "这件装备怎么样"},
    ]
    ev = extract_evidence("这件装备怎么样", history, dag)
    assert not ev.lo_facts


def test_evidence_question优先于history(dag):
    history = [{"role": "user", "content": "我在髓骨之地"}]
    ev = extract_evidence("刚进灰沼", history, dag)
    assert ev.source == "question"


# ══════════════════════════════════════════════
#  bound（reviewed:false 口径）
# ══════════════════════════════════════════════

def test_bound_抬lo到最大act(dag):
    ev = extract_evidence("我在髓骨之地", None, dag)
    bound = dag.bound(lo_facts=[(a, t) for a, t, _k in ev.lo_facts],
                      not_reached=list(ev.not_reached), source=ev.source)
    assert bound.lo == 1
    assert bound.hi == 1           # reviewed:false：不剪 hi（§5.3 简化口径）
    assert bound.axis_label == "幕"


def test_bound_无证据返回unknown(dag):
    bound = dag.bound(lo_facts=[], not_reached=["Citadel"], source="none")
    assert not bound.known
    assert bound.not_reached == ("Citadel",)   # reverse 仍留档


# ══════════════════════════════════════════════
#  infer_progress 集成（silksong，axis=act）
# ══════════════════════════════════════════════

def test_infer_silksong_arrival_抬到act1():
    p = infer_progress("silksong", "我在髓骨之地卡住了")
    assert p.level == 1
    assert p.axis_label == "幕"
    assert "髓骨之地" in p.evidence


def test_infer_silksong_howto_圣堡抬到act2():
    p = infer_progress("silksong", "圣堡的怪怎么打")
    assert p.level == 2


def test_infer_silksong_reverse_未知进度但记未到():
    p = infer_progress("silksong", "怎么去圣堡")
    assert p.level is None            # 只有 reverse 证据时不抬 lo
    assert "Citadel" in p.not_reached


def test_infer_silksong_无证据不推断():
    p = infer_progress("silksong", "这件装备怎么样")
    assert p.level is None


def test_infer_silksong_历史单调取max():
    history = [
        {"role": "user", "content": "我在髓骨之地"},
        {"role": "user", "content": "圣堡的怪怎么打"},
    ]
    p = infer_progress("silksong", "这个护符好用吗", history)
    assert p.level == 2                # max(act1, act2)（§5.2-2 多来源取 max）


# ══════════════════════════════════════════════
#  progress_prompt_note 渲染
# ══════════════════════════════════════════════

def test_note_用幕不是章():
    p = infer_progress("silksong", "我在髓骨之地")
    note = progress_prompt_note(p)
    assert "第 1 幕" in note
    assert "第 1 章" not in note


def test_note_未到区域注入():
    history = [{"role": "user", "content": "怎么去圣堡"}]
    p = infer_progress("silksong", "我在髓骨之地", history)
    note = progress_prompt_note(p)
    assert "Citadel" in note
    assert "没到过" in note


def test_note_BG3的chain行为不变():
    """chain（BG3）的 prompt 文案仍是「章」，S2 不得改变既有行为。"""
    p = infer_progress("baldurs_gate3", "我在下城区")
    assert p.axis_label == "章"
    note = progress_prompt_note(p)
    assert "第 3 章" in note
