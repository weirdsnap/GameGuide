"""进度模型数据 schema 契约测试。

设计文档：docs/PROGRESS_AXIS_DESIGN.md
数据草案：docs/progress_drafts/*.json
正式目录：config/progress/*.json（S1 落地后）

这里只校验**数据结构**，不涉及推断逻辑。目的是让「加游戏 = 加一个 JSON」
这个约定有机器可查的边界：
  - 字段齐全、kind 合法、文件名与 game 一致；
  - 节点 / 能力 id 唯一，边引用的目标必须存在；
  - 区域前置图必须无环（有环时可达集 BFS 会死循环）；
  - act 只能是空或正整数（空 = 未校对）；
  - 锚点非空、不跨 rank 重复（同一个词不能既是第一章又是第三章）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DRAFT_DIR = REPO_ROOT / "docs" / "progress_drafts"
CONFIG_DIR = REPO_ROOT / "config" / "progress"

VALID_KINDS = {"chain", "dag", "null"}


def _model_files() -> list[Path]:
    files: list[Path] = []
    for directory in (DRAFT_DIR, CONFIG_DIR):
        if directory.is_dir():
            files.extend(sorted(directory.glob("*.json")))
    return files


MODEL_FILES = _model_files()


def _test_ids(path: Path) -> str:
    return path.name


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_have_at_least_one_model_file():
    assert MODEL_FILES, (
        "没找到任何进度模型文件：应放在 docs/progress_drafts/ 或 config/progress/"
    )


@pytest.mark.parametrize("path", MODEL_FILES, ids=_test_ids)
def test_top_level_fields(path: Path):
    model = _load(path)
    for key in ("game", "kind", "axis_label", "reviewed"):
        assert key in model, f"{path.name} 缺少字段 {key}"
    assert model["kind"] in VALID_KINDS, f"{path.name} kind 非法：{model['kind']}"
    assert isinstance(model["reviewed"], bool), f"{path.name} reviewed 必须是布尔"
    assert isinstance(model["axis_label"], str) and model["axis_label"].strip()

    # 文件名约定：<game>.<kind>.json —— 让「加游戏加文件」可被自动发现
    expected = f"{model['game']}.{model['kind']}.json"
    assert path.name == expected, f"{path.name} 文件名应为 {expected}"


@pytest.mark.parametrize("path", MODEL_FILES, ids=_test_ids)
def test_chain_model(path: Path):
    model = _load(path)
    if model["kind"] != "chain":
        pytest.skip("非 chain 模型")

    max_rank = model.get("max_rank")
    assert isinstance(max_rank, int) and max_rank >= 1, f"{path.name} max_rank 非法"

    anchors = model.get("anchors")
    assert isinstance(anchors, dict) and anchors, f"{path.name} anchors 不能为空"

    seen: dict[str, str] = {}
    for raw_rank, terms in anchors.items():
        assert str(raw_rank).isdigit(), f"{path.name} 锚点 rank 必须是整数串：{raw_rank}"
        rank = int(raw_rank)
        assert 1 <= rank <= max_rank, f"{path.name} rank {rank} 超出 max_rank"
        assert isinstance(terms, list) and terms, f"{path.name} rank {rank} 锚点列表为空"
        for term in terms:
            assert isinstance(term, str) and term.strip(), f"{path.name} 空锚点"
            key = term.strip().lower()
            assert key not in seen or seen[key] == raw_rank, (
                f"{path.name} 锚点 {term!r} 同时出现在 rank {seen.get(key)} 与 {raw_rank}"
            )
            seen[key] = raw_rank

    for pattern in model.get("explicit_patterns", []):
        re.compile(pattern)  # 正则必须能编译


@pytest.mark.parametrize("path", MODEL_FILES, ids=_test_ids)
def test_dag_model(path: Path):
    model = _load(path)
    if model["kind"] != "dag":
        pytest.skip("非 dag 模型")

    areas = model.get("areas")
    abilities = model.get("abilities", {})
    assert isinstance(areas, dict) and areas, f"{path.name} areas 不能为空"
    assert isinstance(abilities, dict), f"{path.name} abilities 必须是对象"

    # 能力定义
    for aid, ability in abilities.items():
        assert aid and aid == aid.strip().lower(), f"{path.name} 能力 id 需为小写：{aid}"
        assert isinstance(ability.get("name"), str) and ability["name"].strip()
        _assert_act_ok(path, ability.get("act"), f"能力 {aid}")
        for src in ability.get("from", []):
            assert src in areas, f"{path.name} 能力 {aid} 的 from 引用了未定义区域 {src}"

    # 区域节点
    for aid, area in areas.items():
        assert aid and aid == aid.strip().lower(), f"{path.name} 区域 id 需为小写：{aid}"
        assert isinstance(area.get("name"), str) and area["name"].strip()
        _assert_act_ok(path, area.get("act"), f"区域 {aid}")
        for dep in area.get("requires_areas", []):
            assert dep in areas, f"{path.name} 区域 {aid} 引用了未定义区域 {dep}"
        for dep in area.get("requires_abilities", []):
            assert dep in abilities, (
                f"{path.name} 区域 {aid} 引用了未定义能力 {dep}"
            )
        gates = area.get("requires_gates", [])
        assert isinstance(gates, list) and all(
            isinstance(g, str) and g.strip() for g in gates
        ), f"{path.name} 区域 {aid} 的 requires_gates 必须是非空字符串列表"

    if model.get("act_of_node") is not None:
        assert isinstance(model["act_of_node"], bool)

    # 前置图必须无环
    cycle = _find_cycle({aid: list(area.get("requires_areas", [])) for aid, area in areas.items()})
    assert cycle is None, f"{path.name} 区域前置图有环：{' -> '.join(cycle or [])}"


def _assert_act_ok(path: Path, act, label: str) -> None:
    if act is None:
        return  # null = 未校对，允许
    assert isinstance(act, int) and act >= 1, f"{path.name} {label} 的 act 非法：{act!r}"


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """返回一个环（节点序列），无环返回 None。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for dep in graph.get(node, []):
            if color.get(dep, BLACK) == GRAY:
                return stack[stack.index(dep):] + [dep]
            if color.get(dep, BLACK) == WHITE:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in graph:
        if color[node] == WHITE:
            found = visit(node)
            if found:
                return found
    return None


def test_cycle_detector_self_check():
    assert _find_cycle({"a": ["b"], "b": []}) is None
    assert _find_cycle({"a": ["b"], "b": ["a"]}) is not None
