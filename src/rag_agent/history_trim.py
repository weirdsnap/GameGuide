"""上下文预算：history 裁剪（docs/CONTEXT_BUDGET_PLAN.md §3.2）。

背景：前端每轮重发完整会话历史，服务端不裁剪时单轮 tokens_in 最高到
29 万（11-22 线上实测），耗时随输入线性上涨。本模块只做一件事：
把 history 裁到预算内，且保证三件东西不丢——

1. 首轮（keep_head 轮）：通常是定游戏、定流派的锚点句；
2. 最后一轮：本轮问题的直接上下文，永远保留，即使已超预算；
3. 顺序：只删不重排，结果是原序列的子序列。

契约测试：tests/test_history_trim.py。
"""

from __future__ import annotations

from typing import Dict, List

# 单条消息字符上限：长表格式回答最容易爆预算，超过就截断
MAX_MSG_CHARS = 2000


def _truncate(content: str) -> str:
    if len(content) <= MAX_MSG_CHARS:
        return content
    return content[:MAX_MSG_CHARS] + "（后略）"


def trim_history(history, max_turns: int = 6, max_chars: int = 6000,
                 keep_head: int = 1) -> List[Dict]:
    """把 history 裁到预算内。纯函数，不修改入参。

    规则（与 tests/test_history_trim.py 一一对应）：
      1. 只保留 user/assistant 且 content 为字符串的消息；
      2. 丢掉开头的孤立 assistant（没有上下文的开场）；
      3. 单条超过 MAX_MSG_CHARS 截断，尾部加「（后略）」；
      4. 保留最前面 keep_head 轮（一问一答算一轮）；
      5. 最后一轮永远保留；从它往前最多再取 max_turns-1 轮，
         累计字符超过 max_chars 停止。
    """
    if not history:
        return []

    msgs = [m for m in history
            if isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs:
        return []

    msgs = [m if len(m["content"]) <= MAX_MSG_CHARS
            else {**m, "content": _truncate(m["content"])}
            for m in msgs]

    # 预算内不动（顺序与内容都不变）
    if len(msgs) <= 2 * (keep_head + max_turns) and \
            sum(len(m["content"]) for m in msgs) <= max_chars:
        return msgs

    head = msgs[:2 * keep_head]
    rest = msgs[2 * keep_head:]

    # 从末尾往前按轮取：一轮 = 一条 user + 它后面直到下一条 user 之前的 assistant
    tail: List[Dict] = []
    turns = 0
    chars = 0
    i = len(rest)
    while i > 0:
        u = i - 1
        while u >= 0 and rest[u]["role"] != "user":
            u -= 1
        if u < 0:
            break                      # 剩下的全是孤立 assistant，不带
        turn = rest[u:i]
        turn_chars = sum(len(m["content"]) for m in turn)
        # 第一轮（最新一轮）无条件保留；之后的轮受轮数与字符预算约束
        if tail and (turns + 1 > max_turns or chars + turn_chars > max_chars):
            break
        tail = turn + tail
        chars += turn_chars
        turns += 1
        i = u

    return head + tail
