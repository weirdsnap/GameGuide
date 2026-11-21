#!/usr/bin/env python3
"""上下文裁剪（trim_history）的契约测试。

设计见 docs/CONTEXT_BUDGET_PLAN.md §3.2：

    trim_history(history, max_turns=6, max_chars=6000, keep_head=1)
    1. 保留最前面 keep_head 轮（一问一答算一轮）
    2. 从最新往回取，最多 max_turns 轮，累计字符超过 max_chars 停止
    3. 永远保留最后一条 user 消息（本轮问题之前的那条），即使已超预算

函数尚未实现（线上 11-22 单轮 29 万 tokens 就是它没落地的代价）。
所有用例挂条件式 xfail：找不到函数时只标记不报错，实现后 condition 自动失效，
这六条直接变成真守卫，不需要摘标记。

模块位置未定，所以按候选列表探测，避免把实现绑死在某个包名上。
"""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


_CANDIDATES = (
    ("rag_agent.history_trim", "trim_history"),
    ("rag_agent.context_budget", "trim_history"),
    ("rag_agent.multi_agent", "trim_history"),
    ("api_server", "trim_history"),
)


def _load_trim():
    for mod_name, attr in _CANDIDATES:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        func = getattr(mod, attr, None)
        if callable(func):
            return func
    return None


trim_history = _load_trim()

pytestmark = pytest.mark.xfail(
    condition=trim_history is None,
    reason="trim_history 未实现（docs/CONTEXT_BUDGET_PLAN.md §3.2）",
    strict=False,
)


def _mk(turns, q_len=None, a_len=120):
    """把 [(问题, 回答), ...] 展开成 LangChain 风格的 history。"""
    history = []
    for q, a in turns:
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": a})
    return history


def _contents(messages):
    return [m["content"] for m in messages]


# ── 1. 预算内不动 ─────────────────────────────────────────────

def test_short_history_untouched():
    """总字符在预算内时，顺序与内容都不变。"""
    history = _mk([(f"短问{i}", f"短答{i}") for i in range(3)])
    got = trim_history(history)
    assert _contents(got) == _contents(history)
    assert [m["role"] for m in got] == [m["role"] for m in history]


# ── 2. 首轮保留 ───────────────────────────────────────────────

def test_first_turn_kept():
    """超预算时首轮（user + assistant）必须保留，它承担游戏锚点。"""
    history = _mk([("bg3 卡拉克怎么加点", "先看职业分支")] +
                  [(f"问{i}", "答" * 300) for i in range(20)])
    got = trim_history(history, max_turns=3, max_chars=500)
    assert len(got) < len(history), "历史明显超预算，应该被裁掉一部分"
    assert got[0]["role"] == "user"
    assert got[0]["content"] == "bg3 卡拉克怎么加点"
    assert len(got) >= 2 and got[1]["role"] == "assistant"


# ── 3. 末条 user 必留 ─────────────────────────────────────────

def test_last_user_message_kept():
    """即使单条就超预算，最后一条 user 也不能丢（追问全靠它）。"""
    history = _mk([(f"问{i}", "长答" * 500) for i in range(30)])
    got = trim_history(history, max_turns=2, max_chars=100)
    users = [m for m in got if m["role"] == "user"]
    assert users, "裁剪后一条 user 都不剩"
    assert users[-1]["content"] == "问29"


# ── 4. 字符预算 ───────────────────────────────────────────────

def test_char_budget_respected():
    """裁剪后总字符要落在预算附近，不能只裁条数不裁字符。"""
    history = _mk([(f"问{i}", "答" * 400) for i in range(50)])
    got = trim_history(history, max_turns=20, max_chars=2000)
    total = sum(len(m["content"]) for m in got)
    original = sum(len(m["content"]) for m in history)
    assert total < original / 2, f"明显超预算却几乎没裁：{total} vs {original}"
    # 首轮与强制保留的末条 user 不受预算约束，留出余量
    assert total <= 2000 + 2000, f"裁剪后仍超预算过多：{total}"


# ── 5. 轮数上限 ───────────────────────────────────────────────

def test_turn_limit():
    """max_turns 限定「最近几轮」，加上首轮后条数有限。"""
    history = _mk([(f"问{i}", "答" * 50) for i in range(40)])
    got = trim_history(history, max_turns=2, max_chars=100000)
    assert len(got) <= 2 * (2 + 1), f"max_turns=2 却留下 {len(got)} 条"
    assert got[0]["content"] == "问0", "首轮被轮数上限挤掉了"
    assert got[-1]["content"].startswith("答")


# ── 6. 顺序与配对 ─────────────────────────────────────────────

def test_original_order_preserved():
    """裁剪只能删，不能重排；结果内容序列必须是原序列的子序列。"""
    history = _mk([(f"问{i}", f"答{i}") for i in range(30)])
    got = trim_history(history, max_turns=3, max_chars=200)
    idx = 0
    for msg in got:
        while idx < len(history) and history[idx]["content"] != msg["content"]:
            idx += 1
        assert idx < len(history), f"结果里出现了原历史没有的内容：{msg['content'][:20]}"
        idx += 1
