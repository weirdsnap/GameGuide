import os

from typing import Optional, List, Dict
from langchain_openai import ChatOpenAI
from typing_extensions import TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import create_react_agent as create_agent
from langgraph.config import get_stream_writer
from langgraph.utils.runnable import RunnableCallable

from rag_agent.config import LLM_CONFIG
from rag_agent.game_router import (
    DETECT_TH, SWITCH_TH, build_common_rules, build_game_prompt,
    detect_game, detect_game_detail, is_switch_query,
)
from rag_agent.game_router import AVAILABLE_GAMES
from rag_agent.game_registry import GAME_MENU, KNOWN_KEYWORDS
from rag_agent.tools import ACT_LIMIT, GAME_TOOLS
from rag_agent.progress import infer_progress, progress_prompt_note

# 图状态类
class AgentState(TypedDict, total=False):
    question: str
    history: Optional[List[dict]]        # 裁剪后的历史（进模型上下文）
    history_full: Optional[List[dict]]   # 完整历史（只给进度推断用，防剧透锚点可能在被裁掉的旧轮里）
    model_name: Optional[str]
    verbose: bool
    # ── 当前请求内的路由状态 ──
    last_game: Optional[str]
    last_game_confirmed: bool
    detected_game: Optional[str]
    game_switched: bool
    # ── detect 节点产物 ──
    prompt: str
    menu_text: Optional[str]
    vs_ok: Optional[bool]
    db_ok: Optional[bool]
    progress_level: Optional[int]     # 防剧透 P1：本轮用户进度上界（act 轴）
    # ── 最终回答 ──
    answer: str
    # ── 观测（第 0 步：只统计不参与决策）──
    usage: Optional[Dict[str, int]]


def _build_menu(header: str, tail: str) -> str:
    """菜单从注册表派生（GAME_MENU），新增游戏不用改这里。"""
    lines = [f"{i}. {g['emoji']} **{g['zh']}** ({g['en']})"
             for i, g in enumerate(GAME_MENU, 1)]
    return f"{header}\n\n" + "\n".join(lines) + f"\n\n{tail}"


_MENU_SWITCH = _build_menu("你想切换到哪个游戏？请选择：", "直接告诉我游戏名称就可以啦！")
_MENU_NEW = _build_menu("请问你想问哪款游戏的攻略？请选择：", "直接告诉我游戏名称就可以开始啦！")
_SUPPORTED_ZH = "、".join(g["zh"] for g in GAME_MENU)

_FALLBACKPT = (
                f"用户问了一个游戏问题，但这个游戏不在你的知识库中（支持的游戏："
                f"{_SUPPORTED_ZH}）。\n\n"
                "请按以下原则回答：\n"
                "1. 先告知用户「这个游戏不在我的专业知识库中，以下信息基于我的训练数据，"
                "可能不完全准确」。\n"
                "2. 然后尽力回答用户的问题。\n"
                "3. 如果确实不知道答案，诚实承认不知道。\n"
                "4. 使用中文回答。\n"
                "5. 如果用户想要知识库级别的准确信息，提示他直接说出游戏名称"
                "（支持列表见上），这样就能切换到对应知识库。\n"
                "6. 不主动展开用户当前进度之后的内容；必须提及时只给占位提示"
                "（不给名字、不给结果）。\n"
                "7. 不编造来源：只有工具返回文本里真实出现的条目才能写成"
                "「来源：知识库「X」」；没有工具返回时不要写来源。"
            )

# 切换意图的正则表已收敛到 game_router.SWITCH_PATTERNS（is_switch_query 使用），
# 这里不要再维护第二份（历史上有过一份完全相同的死代码，已删）。

# ── 外部游戏列表（不在我们的知识库中，由 LLM 自身知识回答）──
# 注意：别再列「博德之门 / 怪物猎人」这类已支持游戏的词——KNOWN_KEYWORDS
# 排除先跑，那些词在这里是永远不会触发的死条目（「monster hunter」保留：
# 用户问的是怪物猎人世界等未收录作品时要走兜底）。
_KNOWN_EXTERNAL_GAMES = [
    "原神", "genshin", "星穹铁道", "崩坏", "honkai",
    "星露谷", "stardew valley", "我的世界", "minecraft",
    "艾尔登法环", "elden ring", "黑魂", "dark souls", "只狼", "sekiro",
    "巫师", "witcher", "gta", "荒野大镖客", "red dead",
    "最终幻想", "final fantasy",
    "塞尔达", "zelda", "宝可梦", "pokemon",
    "怪物猎人", "monster hunter", "文明", "civilization",
    "英雄联盟", "league of legends", "lol", "dota",
    "战神", "god of war", "神秘海域", "uncharted",
    "古墓丽影", "tomb raider", "生化危机", "resident evil",
    "死亡搁浅", "death stranding",
    "上古卷轴", "skyrim", "辐射", "fallout",
    "双人成行", "it takes two", "胡闹厨房", "overcooked",
]



# ══════════════════════════════════════════
#  用量统计（第 0 步观测层）
# ══════════════════════════════════════════

ZERO_USAGE: Dict[str, int] = {"tokens_in": 0, "tokens_out": 0, "tool_calls": 0}


def usage_from_messages(messages) -> Dict[str, int]:
    """从 Agent 的消息轨迹里汇总 token 与工具调用次数。

    - 只统计 AIMessage：ToolMessage/HumanMessage 不产生用量
    - 优先 usage_metadata（langchain 标准化字段）；缺失时回退到
      response_metadata["token_usage"]（OpenAI 兼容接口的原始字段）
    - 流式路径要拿到 usage，ChatOpenAI 必须开 stream_usage（见 _make_llm），
      否则这里永远统计到 0
    """
    tokens_in = tokens_out = tool_calls = 0
    for msg in messages or []:
        if not isinstance(msg, AIMessage):
            continue
        tool_calls += len(getattr(msg, "tool_calls", None) or [])
        usage = getattr(msg, "usage_metadata", None)
        if usage:
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
            continue
        legacy = (getattr(msg, "response_metadata", None) or {}).get("token_usage") or {}
        tokens_in += int(legacy.get("prompt_tokens") or 0)
        tokens_out += int(legacy.get("completion_tokens") or 0)
    return {"tokens_in": tokens_in, "tokens_out": tokens_out, "tool_calls": tool_calls}


def _names_external_game(q: str) -> bool:
    """检查是否明确点名了一个不在我们知识库里的游戏。

    只有「点名外部游戏名」才算未知游戏证据。曾经还匹配
    「在哪里/职业/装备」等泛游戏用语，导致会话中段的正常追问
    被误判为未知游戏、清空游戏上下文（见 docs/ROUTING_FINDINGS.md）。
    """
    ql = q.lower().strip()

    # 排除已知游戏（避免误判）；词表从注册表派生（名称 + aliases）
    for kw in KNOWN_KEYWORDS:
        if kw in ql:
            return False

    # 检查外部游戏名
    for game in _KNOWN_EXTERNAL_GAMES:
        if game in ql:
            return True

    return False


# 兼容旧名（外部/测试可能还在 import）；判定逻辑用 DETECT_TH / SWITCH_TH。
GAME_CONFIDENCE_TH = DETECT_TH

def detect_game_node(state) -> dict:
    
    q = state["question"].strip()
    if not q:
        return  {"detected_game": None, "menu_text": "请问你想了解哪款游戏的攻略？"}

    game_key, confidence, exclusive = detect_game_detail(q)
    last_game = state.get("last_game")
    last_game_confirmed = state.get("last_game_confirmed", False)
    switched = False

    # 决策顺序（切换权交给用户，见 docs/ROUTING_FINDINGS.md 建议 A-D）：
    #   1. 明确点名已知游戏：专有命中且过 SWITCH_TH 才允许跨游戏切换
    #   2. 明确切换意图 → 切换菜单
    #   3. 明确点名外部游戏 → LLM 兜底并清空上下文
    #   4. 有历史且上轮游戏已确认 → 无条件沿用，不再被泛游戏用语打断
    #   5. 有历史但推不出游戏 → LLM 带历史兜底（不打断会话）
    #   6. 无历史也推不出 → 新菜单（菜单只留给空历史）
    if game_key and last_game and last_game_confirmed and game_key != last_game:
        # 通用词（护符/灵魂/法术）不能单独推翻已确认的会话：
        # 只有专有命中（游戏名/角色/地名）且过门槛才切；否则静默沿用。
        if exclusive and confidence >= SWITCH_TH:
            switched = True
            last_game, last_game_confirmed = game_key, True
        else:
            game_key = last_game
    elif game_key:
        last_game, last_game_confirmed = game_key, True
    elif is_switch_query(q):
        return {
            "detected_game": None,
            "menu_text": _MENU_SWITCH,
            "last_game": None,
            "last_game_confirmed": False
        }
    elif _names_external_game(q):
        return {
            "detected_game": "__llm_fallback__",
            "prompt": _FALLBACKPT,
            "last_game": None,
            "last_game_confirmed": False
        }
    elif state.get("history") and last_game and last_game_confirmed:
        game_key = last_game
    elif state.get("history"):
        # 会话中段但推不出游戏：LLM 带历史作答，不清空也不确认游戏
        return {"detected_game": "__llm_fallback__", "prompt": _FALLBACKPT}
    else:
        return {
            "detected_game": None,
            "menu_text": _MENU_NEW,
            "last_game": None,
            "last_game_confirmed": False
        }

    cfg = AVAILABLE_GAMES[game_key]
    vs_ok = os.path.isdir(cfg["vectorstore_dir"])
    db_ok = os.path.isfile(cfg["db_path"])

    if not vs_ok and not db_ok:
        return {"detected_game": None,
                "menu_text": f"抱歉，{cfg['name']} 的知识库尚未准备好。请联系管理员初始化数据。",
                "last_game": None, "last_game_confirmed": False}

    # 防剧透 P1：把用户进度解析成结构化上界，既写进 prompt（L2），
    # 也通过 state.progress_level 传给 agent 节点注入检索层（L1）。
    # 进度锚点可能在被裁剪掉的旧轮里，所以用完整历史推断。
    progress = infer_progress(game_key, q, state.get("history_full") or state.get("history"))
    prompt = build_game_prompt(game_key) + "\n\n" + build_common_rules(game_key)
    prompt += progress_prompt_note(progress)
    if switched:
        prompt += f"\n\n**系统提示：** 用户切换了话题开始聊{cfg['name']}，除非用户主动提及和之前游戏的对比，否则上述的历史请忽略。"

    return {
        "detected_game": game_key, 
        "prompt": prompt,
        "vs_ok": vs_ok, 
        "db_ok": db_ok, 
        "game_switched": switched,
        "last_game": last_game,
        "last_game_confirmed": last_game_confirmed,
        "progress_level": progress.level
    }


# ══════════════════════════════════════════
#  条件路由 + 菜单节点
# ══════════════════════════════════════════

def route_after_detect(state: AgentState) -> str:
    """条件边函数：把 detect 节点的路由决策翻译为下一个节点名。

    契约（继承自旧 ask() 开头的两个 if 判断）：
      detected_game is None               → "menu"      弹菜单/报错（文案在 menu_text）
      detected_game == "__llm_fallback__" → "fallback"  未知游戏，LLM 自身知识兜底
      其余（正常游戏 key）                 → "agent"     进 ReAct agent

    注意：
      - 本函数不做任何判断逻辑，只读 detect_game_node 已写好的 detected_game
      - 返回值必须和 build_graph 里 add_conditional_edges 的字典键一一对应
      - "__llm_fallback__" 是沿用旧契约的字符串魔法值（见 docs/REFACTOR_LANGGRAPH.md 妥协清单）
    """

    detected_game = state.get("detected_game")
    if detected_game is None:
        return "menu"
    if detected_game == "__llm_fallback__":
        return "fallback"
    return "agent"


def menu_node(state: AgentState) -> dict:
    """菜单节点：menu_text 直通为 answer，不做任何判断。

    覆盖的场景（都是 detect 节点 detected_game=None 的分支）：
      - 空问题 / 无关键词无历史 → _MENU_NEW
      - 切换意图               → _MENU_SWITCH
      - 知识库未就绪            → 报错文案

    返回：{"answer": state["menu_text"], "usage": 全零}
    """
    return {
        "answer": state["menu_text"],
        "usage": dict(ZERO_USAGE),      # 菜单路径不过 LLM，用量恒为 0
    }


# ══════════════════════════════════════════
#  LLM 兜底节点（未知游戏，用 LLM 自身知识回答）
# ══════════════════════════════════════════

def _make_llm(state: AgentState, streaming: bool = False):
    """按 state 里的 model_name 覆盖构建 ChatOpenAI。

    - model_name 为 None 时用 LLM_CONFIG 里的默认模型
    - streaming=True 让 astream_events 能拿到逐 token 的 chunk（流式入口需要）
    """
    config = dict(LLM_CONFIG)              # ① 默认：config.py 里的模型/key/温度
    if state.get("model_name"):            # ② 调用方覆盖：这次请求想换个模型
        config["model"] = state["model_name"]
    if streaming:                          # ③ 用途标记：要不要逐 token 输出
        config["streaming"] = True
    # ④ 流式必须显式要求用量：不带 stream_options.include_usage 时，
    #    OpenAI 兼容接口不会返回 usage，流式请求的 token 统计会恒为 0。
    #    非流式路径下这个开关无副作用。
    config["stream_usage"] = True
    return ChatOpenAI(**config)

def _fallback_setup(state: AgentState):
    """fallback 节点的共用准备：建 LLM + 拼消息。"""
    llm = _make_llm(state, streaming=True)
    messages = build_messages(
        state["question"].strip(),
        state.get("history"),
        state["prompt"],          # detect 写好的 _FALLBACKPT，不是游戏 prompt
    )
    return llm, messages


def fallback_node_sync(state: AgentState) -> dict:
    """LLM 兜底节点（同步版，graph.invoke / ask() 路径用）。"""
    llm, messages = _fallback_setup(state)
    response = llm.invoke(messages)
    return {
        "answer": response.content or "（无回复）",
        "usage": usage_from_messages([response]),
    }


async def fallback_node(state: AgentState) -> dict:
    """LLM 兜底节点（异步版，astream / ask_stream 路径用）。

    对应旧 ask() 的 __llm_fallback__ 分支（multi_agent.py:655-669）。
    与 fallback_node_sync 成对注册（RunnableCallable），勿只改一个。

    注意：流式路径必须是 async 节点 + ainvoke。同步节点会被框架丢到线程池
    执行，LLM 流式 token 的回调传不回外层 astream 事件流（曾导致流式零 token）。
    """
    llm, messages = _fallback_setup(state)
    response = await llm.ainvoke(messages)
    return {
        "answer": response.content or "（无回复）",
        "usage": usage_from_messages([response]),
    }


# ══════════════════════════════════════════
#  Agent 节点（正常游戏的主路径）
# ══════════════════════════════════════════

def _agent_setup(state: AgentState):
    """agent 节点的共用准备：建 LLM + ReAct agent + 发 meta + 拼消息。"""
    # ① 建 LLM —— streaming=True 保证流式路径能拿到逐 token chunk
    llm = _make_llm(state, streaming=True)

    # ② 建 ReAct agent：llm / 工具列表 / system prompt（detect 拼好的游戏 prompt）
    agent = create_agent(llm, GAME_TOOLS,
                         prompt=SystemMessage(content=state["prompt"]))

    # ③ 发 meta 自定义事件（stream_mode 不含 "custom" 时为 no-op）
    get_stream_writer()({
        "type": "meta",
        "game": state["detected_game"],
        "game_name": AVAILABLE_GAMES[state["detected_game"]]["name"],
        "model": llm.model_name,
        "sources": {
            "vectorstore": state.get("vs_ok"),
            "database": state.get("db_ok")
        },
    })

    # ④ 拼消息。系统提示词只由 ② 的 create_agent(prompt=...) 注入一次，
    #    这里不再传 game_prompt——历史上两处都注入，每轮请求白烧一倍
    #    system prompt 的 token（2026-11 修）。
    messages = build_messages(
        state["question"].strip(),
        state.get("history"),
    )
    return agent, messages


def _agent_answer(result: dict) -> dict:
    """⑥ 提取最终回答：result["messages"] 是整个循环轨迹，最后一条才是答案。

    顺带汇总用量（第 0 步观测）：API 层只拿到 answer 字符串，
    工具调用轨迹只有节点里看得到，所以统计必须在这里做。
    """
    answer = result["messages"][-1].content if result.get("messages") else ""
    return {
        "answer": answer or "（Agent 没有返回有效回答）",
        "usage": usage_from_messages(result.get("messages")),
    }


def agent_node_sync(state: AgentState) -> dict:
    """Agent 节点（同步版，graph.invoke / ask() 路径用）。"""
    agent, messages = _agent_setup(state)
    token = ACT_LIMIT.set(state.get("progress_level"))
    try:
        result = agent.invoke({"messages": messages}, {"recursion_limit": 50})
    finally:
        ACT_LIMIT.reset(token)
    return _agent_answer(result)


async def agent_node(state: AgentState) -> dict:
    """Agent 节点（异步版，astream / ask_stream 路径用）。

    对应旧 ask() 的主体（multi_agent.py:671-695）。
    与 agent_node_sync 成对注册（RunnableCallable），勿只改一个。

    两个流式陷阱（接线期踩过的坑，勿回退）：
      - 流式路径必须是 async 节点 + ainvoke：同步节点被框架丢线程池执行，
        LLM token 回调传不回外层 astream（曾导致流式零 token）；
      - create_react_agent 是子图：ask_stream 必须开 subgraphs=True 才能
        收到内部 LLM 的 token（见其注释）。
    """
    agent, messages = _agent_setup(state)
    # ⑤ 驱动 ReAct 循环 —— recursion_limit 是循环保险丝
    token = ACT_LIMIT.set(state.get("progress_level"))
    try:
        result = await agent.ainvoke({"messages": messages}, {"recursion_limit": 50})
    finally:
        ACT_LIMIT.reset(token)
    return _agent_answer(result)


#  构建消息
# ══════════════════════════════════════════

def build_messages(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    game_prompt: str = "",
) -> List[BaseMessage]:
    """构建消息列表。"""
    messages: List[BaseMessage] = []

    if history:
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

    if game_prompt and (not history or not any(isinstance(m, SystemMessage) for m in messages)):
        messages.insert(0, SystemMessage(content=game_prompt))

    messages.append(HumanMessage(content=question))
    return messages
    

# ══════════════════════════════════════════
#  图的组装（主入口）
# ══════════════════════════════════════════

def build_graph():
    """组装并编译图。

    拓扑：
        detect_game ──┬─ "menu"     → menu     → END
                      ├─ "fallback" → fallback → END
                      └─ "agent"    → agent    → END

    route_after_detect 在这里是"注册"而不是"调用"：
    add_conditional_edges 收到的是函数对象本身，框架在 detect 节点每次跑完后
    自动调用它，用返回值查第三个参数（字典），决定走向哪个节点。
    """
    workflow = StateGraph(AgentState)

    # 1. 注册节点：名字 ↔ 函数
    #    fallback/agent 用 RunnableCallable 注册同步+异步双版本：
    #    graph.invoke（ask 路径）走同步版，astream（流式路径）走异步版。
    #    只注册异步版会导致同步 invoke 报 "No synchronous function provided"。
    workflow.add_node("detect_game", detect_game_node)
    workflow.add_node("menu", menu_node)
    workflow.add_node("fallback", RunnableCallable(fallback_node_sync, fallback_node))
    workflow.add_node("agent", RunnableCallable(agent_node_sync, agent_node))

    # 2. 入口：一切从 detect 开始
    workflow.set_entry_point("detect_game")

    # 3. 条件边：detect 之后走哪条，由 route_after_detect 的返回值查字典决定
    workflow.add_conditional_edges(
        "detect_game",
        route_after_detect,
        {"menu": "menu", "fallback": "fallback", "agent": "agent"},
    )

    # 4. 三个终端节点跑完都直接结束
    workflow.add_edge("menu", END)
    workflow.add_edge("fallback", END)
    workflow.add_edge("agent", END)

    # 5. 编译成可执行的 CompiledGraph（拥有 .invoke / .astream_events 等方法）
    return workflow.compile()


_graph = None

def get_graph():
    """模块级懒加载单例：编译一次，所有请求复用。"""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
