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


@pytest.fixture(autouse=True)
def _isolate_gameguide_data(tmp_path, monkeypatch):
    """把 api_server 的落盘目录全部改到 tmp，避免 pytest 写脏生产数据（I-22）。

    api_server 只在部分测试模块里 import，所以用 sys.modules 判断是否已加载：
    未加载时这个 fixture 什么也不做（纯算法测试不受影响）。
    """
    module = sys.modules.get("api_server")
    if module is None:
        return
    monkeypatch.setattr(module, "TURNS_DIR", tmp_path / "turns", raising=False)
    monkeypatch.setattr(module, "FEEDBACK_DIR", tmp_path / "feedback", raising=False)
    monkeypatch.setattr(module, "SESSION_DB", tmp_path / "sessions.db", raising=False)
    # 系统资源采样（看板「系统资源」块）：不隔离就会把测试的采样写进生产 data/metrics
    monkeypatch.setattr(module, "METRICS_DIR", tmp_path / "metrics", raising=False)
    # 博客上报（docs/BLOG_ANALYTICS_PLAN.md）：库路径走环境变量（blog_stats 读它），
    # 限速器换新实例，避免用例之间串「每分钟 60 次」的计数。
    monkeypatch.setenv("GAMEGUIDE_BLOG_DB", str(tmp_path / "blog.db"))
    if hasattr(module, "blog_hit_limiter"):
        monkeypatch.setattr(module, "blog_hit_limiter",
                            module.blog_stats.HitRateLimiter(60), raising=False)


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
