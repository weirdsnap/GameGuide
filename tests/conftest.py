# tests/conftest.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# test_light.py 是「脚本式」评估（有自己的 argparse main，但函数名以 test_ 开头），
# 被 pytest 收集会把 routing/retrieval/e2e 全部真跑一遗（含真实 LLM 调用与向量库加载，
# 曾导致 pytest tests/ 挂十分钟以上）。它只允许显式脚本方式运行：
#   .venv/bin/python tests/test_light.py
collect_ignore = ["test_light.py"]


def pytest_addoption(parser):
    parser.addoption(
        "--run-e2e", action="store_true", default=False,
        help="跑需要 LLM API key 的端到端用例（默认跳过）",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "e2e: 端到端用例，真实调用 LLM，需 --run-e2e 开启"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-e2e"):
        return
    skip_e2e = pytest.mark.skip(reason="端到端用例，需 --run-e2e 开启（真实调用 LLM）")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)
