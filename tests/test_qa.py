#!/usr/bin/env python3
"""《空洞骑士》RAG Agent — 自动化回归测试。

两种跑法：
  # pytest（默认跳过 e2e；--run-e2e 才真实调用 LLM）
  .venv/bin/python -m pytest tests/test_qa.py --run-e2e

  # 脚本模式（直接跑全部 e2e，支持过滤）
  python tests/test_qa.py
  python tests/test_qa.py --filter "mantis"
  python tests/test_qa.py --list
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rag_agent.multi_agent import ask

# ── 测试用例 ──
# (名称, 问题, 应包含关键词列表, 不应包含关键词列表)
TEST_CASES = [
    # ── 能力获取 ──
    (
        "mantis_claw",
        "How do I get the Mantis Claw?",
        ["fungal", "mantis village", "mantis lords"],
        [],
    ),
    (
        "mantis_claw_cn",
        "螳螂爪怎么拿？",
        ["真菌", "螳螂村", "Mantis"],
        [],
    ),
    (
        "dream_nail",
        "How to get the Dream Nail?",
        ["resting grounds", "dream nail"],
        [],
    ),
    (
        "dream_nail_cn",
        "梦之钉怎么获得？",
        ["Seer", "resting grounds"],
        [],
    ),
    (
        "crystal_heart",
        "What does Crystal Heart do?",
        ["crystal", "crystal peak"],
        [],
    ),
    (
        "isma_tear",
        "How to get Isma's Tear?",
        ["isma", "royal waterways"],
        [],
    ),
    (
        "monarch_wings",
        "Where to get Monarch Wings?",
        ["ancient basin", "broken vessel"],
        [],
    ),

    # ── 关键地点 ──
    (
        "city_of_tears",
        "How do I get to the City of Tears?",
        ["city of tears"],
        [],
    ),
    (
        "deepnest_entrance",
        "How to enter Deepnest from the Fungal Wastes?",
        ["mantis village", "deepnest"],
        [],
    ),
    (
        "colosseum",
        "Where is the Colosseum of Fools?",
        ["colosseum"],
        [],
    ),
    (
        "white_palace",
        "How to enter the White Palace?",
        ["dream nail", "white palace"],
        [],
    ),

    # ── Boss 相关 ──
    (
        "hollow_knight_boss",
        "Where is the Hollow Knight boss?",
        ["black egg", "temple"],
        [],
    ),
    (
        "radiance_location",
        "Where is the Radiance?",
        ["radiance", "temple of the black egg"],
        [],
    ),

    # ── NPC / 故事 ──
    (
        "pale_king",
        "Who is the Pale King?",
        ["king", "pale"],
        [],
    ),
    (
        "hornet",
        "Who is Hornet?",
        ["hornet", "protector"],
        [],
    ),

    # ── 机制 ──
    (
        "mask_shards",
        "How many mask shards make one mask?",
        ["4"],
        [],
    ),
    (
        "nail_upgrade",
        "How many times can you upgrade the Nail?",
        ["nailsmith", "city of tears"],
        [],
    ),

    # ── 边界：不在知识库内的问题 ──
    (
        "silksong_info",
        "How to get to the Moss Grotto in Silksong?",
        ["尚未"],
        ["step 1", "step 2", "defeat"],
    ),
    (
        "unrelated_game",
        "How to beat Ganon in Zelda?",
        ["无法", "空洞"],
        ["triforce", "master sword"],
    ),
    (
        "unrelated_math",
        "What is 2+2?",
        [],
        ["攻略指南", "2+2="],
    ),
]

PASS = "✅"
FAIL = "❌"


def run_case(name: str, question: str, must_have: list, must_not_have: list) -> tuple[list[str], str]:
    """跑单条用例，返回 (问题列表, 完整回答)。问题列表为空 = 通过。"""
    answer = ask(question)
    answer_lower = answer.lower()

    problems = []
    for kw in must_have:
        if kw.lower() not in answer_lower:
            problems.append(f"缺少关键词「{kw}」")
    for kw in must_not_have:
        if kw.lower() in answer_lower:
            problems.append(f"不应出现「{kw}」")
    return problems, answer


# ── pytest 入口（e2e：真实调用 LLM，默认跳过，--run-e2e 开启）──


@pytest.mark.e2e
@pytest.mark.parametrize(
    "name,question,must_have,must_not_have",
    TEST_CASES,
    ids=[c[0] for c in TEST_CASES],
)
def test_qa_case(name: str, question: str, must_have: list, must_not_have: list):
    problems, answer = run_case(name, question, must_have, must_not_have)
    assert not problems, f"{'; '.join(problems)}\n回答预览: {answer[:200]}"


def list_tests():
    print(f"共 {len(TEST_CASES)} 个测试用例：\n")
    for i, (name, question, must_have, must_not_have) in enumerate(TEST_CASES, 1):
        req = f"要求包含: {must_have}" if must_have else ""
        ban = f"禁止出现: {must_not_have}" if must_not_have else ""
        sep = " | " if req and ban else ""
        print(f"  {i:2d}. [{name}] {question}")
        if req or ban:
            print(f"      {req}{sep}{ban}")
    print()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--filter", "-f", help="filter by test name substring")
    parser.add_argument("--list", "-l", action="store_true", help="list tests")
    args = parser.parse_args()

    if args.list:
        list_tests()
        return 0

    cases = TEST_CASES
    if args.filter:
        cases = [c for c in cases if args.filter.lower() in c[0].lower()]
        if not cases:
            print(f"⚠️  no matches for '{args.filter}'")
            sys.exit(1)

    total = len(cases)
    passed = 0
    failed = 0

    print(f"🧪  Hollow Knight RAG Agent tests ({total} cases)\n")
    print("━" * 50)

    for i, (name, question, must_have, must_not_have) in enumerate(cases, 1):
        print(f"\n[{i}/{total}] [{name}]")
        print(f"  ❓ {question}")
        problems, answer = run_case(name, question, must_have, must_not_have)
        if problems:
            failed += 1
            print(f"    {FAIL} {'; '.join(problems)}")
            print(f"    回答预览：{answer[:200]}")
        else:
            passed += 1
            prefix = answer[:min(len(answer), 100)]
            print(f"    {PASS} {prefix}...")

    print("\n" + "━" * 50)
    print(f"\n{PASS} {passed}/{total} passed", end="")
    if failed:
        print(f", {FAIL} {failed}/{total} failed", end="")
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
