"""脚本入口冒烟测试：从任意 cwd 跑 `--help` 不应该 ImportError。

背景：game_registry 重构后，`ingest_game.py` / `wiki.py` 在 `sys.path.insert`
之前就 `from rag_agent...`，按文档命令运行会直接 ModuleNotFoundError。
（mac_build.py 此前单独修过，这里一并锁住。）
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SCRIPTS = [
    "scripts/tool/ingest_game.py",
    "scripts/tool/mac_build.py",
    "scripts/fetch/wiki.py",
    "scripts/eval/glossary_probe.py",
]


@pytest.mark.parametrize("rel", SCRIPTS)
def test_script_help_from_foreign_cwd(rel, tmp_path):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)  # 不允许用外部 PYTHONPATH 掩盖 sys.path 顺序问题

    proc = subprocess.run(
        [sys.executable, str(ROOT / rel), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert proc.returncode == 0, f"{rel} 入口失败：\n{proc.stderr}"
