#!/usr/bin/env python3
"""检查 ragas / datasets 导入问题，定位具体是哪个包或版本不兼容"""

import sys

print(f"Python version: {sys.version}")
print(f"Executable: {sys.executable}")
print()

# ── datasets ──
try:
    import datasets
    print(f"✅ datasets {datasets.__version__}  import OK")
except ImportError as e:
    print(f"❌ datasets import failed: {e}")
except Exception as e:
    print(f"⚠️  datasets error: {type(e).__name__}: {e}")

try:
    from datasets import Dataset
    print("✅ datasets.Dataset import OK")
except ImportError as e:
    print(f"❌ datasets.Dataset ImportError: {e}")
except Exception as e:
    print(f"⚠️  datasets.Dataset error: {type(e).__name__}: {e}")

# ── ragas ──
try:
    import ragas
    print(f"✅ ragas {ragas.__version__} import OK")
except ImportError as e:
    print(f"❌ ragas import failed: {e}")
except Exception as e:
    print(f"⚠️  ragas error: {type(e).__name__}: {e}")

try:
    from ragas import evaluate
    print("✅ ragas.evaluate import OK")
except ImportError as e:
    print(f"❌ ragas.evaluate ImportError: {e}")
except Exception as e:
    print(f"⚠️  ragas.evaluate error: {type(e).__name__}: {e}")

try:
    from ragas.metrics import faithfulness
    from ragas.metrics import answer_relevancy
    from ragas.metrics import context_precision
    from ragas.metrics import context_recall
    print("✅ ragas.metrics.* import OK")
except ImportError as e:
    print(f"❌ ragas.metrics ImportError: {e}")
except Exception as e:
    print(f"⚠️  ragas.metrics error: {type(e).__name__}: {e}")

# ── pip list ──
print()
print("--- ragas dependencies ---")
import subprocess
result = subprocess.run(
    [sys.executable, "-m", "pip", "list", "--format=columns"],
    capture_output=True, text=True
)
for line in result.stdout.splitlines():
    if any(pkg in line.lower() for pkg in ["ragas", "datasets", "langchain", "fastembed"]):
        print(f"  {line}")
