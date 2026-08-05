"""detect_game_node 契约测试（离线，不依赖 LLM）。"""

from rag_agent.refactor.graph import detect_game_node


def _detect(question, history=None, last_game=None, confirmed=False):
    """构造初始 state 并调用 detect 节点，返回其部分更新 dict。"""
    state = {
        "question": question,
        "history": history or [],
        "last_game": last_game,
        "last_game_confirmed": confirmed,
    }
    return detect_game_node(state)


# ── 正常路由 ──────────────────────────────────────────────

def test_detect_命中关键词():
    result = _detect("空洞骑士辐光怎么打")

    assert result["detected_game"] == "hollow_knight"
    assert result["last_game"] == "hollow_knight"
    assert result["last_game_confirmed"] is True
    assert "prompt" in result


def test_detect_无关键词延续上轮():
    history = [{"role": "user", "content": "空洞骑士辐光怎么打"}]
    result = _detect("它多少血", history=history,
                     last_game="hollow_knight", confirmed=True)

    assert result["detected_game"] == "hollow_knight"


# ── 菜单分支（状态必须清空）────────────────────────────────

def test_detect_空问题弹菜单():
    result = _detect("")

    assert result["detected_game"] is None
    assert "请问你想了解哪款游戏" in result["menu_text"]


def test_detect_切换意图弹切换菜单():
    result = _detect("换个游戏", last_game="hollow_knight", confirmed=True)

    assert result["detected_game"] is None
    assert result["last_game"] is None
    assert result["last_game_confirmed"] is False
    assert "切换" in result["menu_text"]


def test_detect_无关键词无历史弹新菜单():
    result = _detect("今天天气不错")

    assert result["detected_game"] is None
    assert result["last_game"] is None
    assert result["last_game_confirmed"] is False


# ── 兜底分支 ──────────────────────────────────────────────

def test_detect_未知游戏走LLM兜底():
    result = _detect("原神怎么玩")

    assert result["detected_game"] == "__llm_fallback__"
    assert "prompt" in result
    assert result["last_game"] is None
    assert result["last_game_confirmed"] is False


def test_menu_直通():
    from rag_agent.refactor.graph import menu_node
    assert menu_node({"menu_text": "任意文案"}) == {"answer": "任意文案"}


def test_graph_菜单路径端到端():
    """不需要 LLM 的完整图调用：无关键词 → 弹菜单。"""
    from rag_agent.refactor.graph import get_graph
    result = get_graph().invoke({
        "question": "今天天气不错", "history": [],
        "last_game": None, "last_game_confirmed": False,
    })
    assert "请问你想问哪款游戏" in result["answer"]
    assert result["last_game"] is None


# ── detect：切换与边界 ─────────────────────────────────────

def test_detect_游戏切换带提示语():
    result = _detect("空洞骑士辐光怎么打", last_game="oni", confirmed=True)

    assert result["detected_game"] == "hollow_knight"
    assert result["game_switched"] is True
    assert "切换了话题" in result["prompt"]


def test_detect_知识库未就绪弹报错(monkeypatch):
    """注册一个路径不存在的假游戏，命中后应走"知识库未就绪"菜单。"""
    import rag_agent.refactor.graph as graph_mod
    monkeypatch.setitem(graph_mod.AVAILABLE_GAMES, "fake_game", {
        "name": "Fake Game (假游戏)",
        "db_path": "/nonexistent/fake.db",
        "vectorstore_dir": "/nonexistent/vectorstore",
    })
    from rag_agent import game_router
    monkeypatch.setitem(game_router.GAME_SIGNALS, "fake_game", ["不存在的特征词xyz"])

    result = _detect("不存在的特征词xyz 怎么玩")

    assert result["detected_game"] is None
    assert "尚未准备好" in result["menu_text"]
    assert result["last_game"] is None


def test_detect_有last_game但无历史不延续():
    """history 为空是"延续上轮"的否决条件：应弹新菜单而不是沿用。"""
    result = _detect("它多少血", last_game="hollow_knight", confirmed=True)

    assert result["detected_game"] is None


# ── route_after_detect ─────────────────────────────────────

import pytest

from rag_agent.refactor.graph import route_after_detect


@pytest.mark.parametrize("game_key", ["hollow_knight", "oni", "terraria", "mhw"])
def test_route_正常游戏进agent(game_key):
    assert route_after_detect({"detected_game": game_key}) == "agent"


def test_route_None进menu():
    assert route_after_detect({"detected_game": None}) == "menu"


def test_route_魔法值进fallback():
    assert route_after_detect({"detected_game": "__llm_fallback__"}) == "fallback"


# ── build_messages ─────────────────────────────────────────

from rag_agent.refactor.graph import build_messages


def test_build_messages_角色转换与顺序():
    history = [{"role": "user", "content": "问1"},
               {"role": "assistant", "content": "答1"}]
    msgs = build_messages("问2", history, "系统提示")

    assert [type(m).__name__ for m in msgs] == [
        "SystemMessage", "HumanMessage", "AIMessage", "HumanMessage"]
    assert msgs[0].content == "系统提示"
    assert msgs[-1].content == "问2"


def test_build_messages_无历史只有系统和问题():
    msgs = build_messages("问题", [], "系统提示")

    assert [type(m).__name__ for m in msgs] == ["SystemMessage", "HumanMessage"]


def test_build_messages_无prompt不插系统消息():
    msgs = build_messages("问题", [], "")

    assert [type(m).__name__ for m in msgs] == ["HumanMessage"]


# ── _make_llm ──────────────────────────────────────────────

def test_make_llm_默认与覆盖(monkeypatch):
    """构建对象不发网络请求；假 api_key 仅为绕开本地校验。"""
    import rag_agent.refactor.graph as graph_mod
    monkeypatch.setitem(graph_mod.LLM_CONFIG, "api_key", "sk-test")

    llm_default = graph_mod._make_llm({})
    assert llm_default.model_name == graph_mod.LLM_CONFIG["model"]

    llm_custom = graph_mod._make_llm({"model_name": "some-other-model"})
    assert llm_custom.model_name == "some-other-model"