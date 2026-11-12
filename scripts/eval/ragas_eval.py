#!/usr/bin/env python3
"""
Tier 2 RAGAS Evaluation — runs on Mac M3 for comprehensive assessment.

Usage:
    # Install RAGAS first
    pip install ragas

    # Run evaluation
    python scripts/eval/ragas_eval.py --game all              # All games
    python scripts/eval/ragas_eval.py --game hollow_knight     # Single game
    python scripts/eval/ragas_eval.py --game all --limit 5     # Quick test (5 per game)

    # Reports saved to: evaluation/reports/ragas_{game}_{timestamp}.json
"""

import sys
import os
import re
import json
import time
import asyncio
import inspect
import argparse
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("evaluate_ragas")

# ── Configuration ───────────────────────────────────────────────────

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = "deepseek-chat"

REPORT_DIR = Path(__file__).resolve().parent.parent.parent / "evaluation" / "reports"
GAMES_DIR = Path(__file__).resolve().parent.parent.parent / "games"

# ── Test Questions per Game ─────────────────────────────────────────

@dataclass
class TestQuestion:
    question: str
    ground_truth: str
    category: str  # "description" | "numeric" | "location" | "edge_case"
    expected_game: str = ""

QUESTIONS = {
    "hollow_knight": [
        TestQuestion("What is the Mantis Claw and where is it found?",
                     "The Mantis Claw is an ability that allows wall-jumping, found in the Mantis Village in Fungal Wastes after defeating the Mantis Lords.",
                     "location", "hollow_knight"),
        TestQuestion("How do I obtain the Dream Nail?",
                     "The Dream Nail is obtained from the Dreamer in the Resting Grounds after visiting the Seer.",
                     "location", "hollow_knight"),
        TestQuestion("What is the health of the Radiance boss?",
                     "The Radiance has 1000 HP (or 1200 in the base game counting the first phase).",
                     "numeric", "hollow_knight"),
        TestQuestion("How many charm notches are in the game?",
                     "There are 11 charm notches total (3 base + 8 from charm notch upgrades).",
                     "numeric", "hollow_knight"),
        TestQuestion("What does Fury of the Fallen do?",
                     "Fury of the Fallen increases damage by 75% when the player is at 1 mask of health.",
                     "description", "hollow_knight"),
        TestQuestion("Where is the Nailsmith located?",
                     "The Nailsmith is located in his hut in the west side of the City of Tears.",
                     "location", "hollow_knight"),
        TestQuestion("What is the Grimmchild charm?",
                     "The Grimmchild is a companion charm obtained during the Grimm Troupe questline that attacks enemies.",
                     "description", "hollow_knight"),
        TestQuestion("How much does the Pure Nail upgrade cost?",
                     "The Pure Nail upgrade costs 4000 Geo and requires 3 Pale Ore.",
                     "numeric", "hollow_knight"),
        TestQuestion("What happens when you give the Delicate Flower to the Godseeker?",
                     "Giving the Delicate Flower to the Godseeker unlocks the Pantheon of Hallownest.",
                     "description", "hollow_knight"),
        TestQuestion("How many Dream Warriors are there?",
                     "There are 16 Dream Warriors in the base game.",
                     "numeric", "hollow_knight"),
        TestQuestion("Does Hollow Knight have guns?",
                     "No, Hollow Knight does not have guns. The game uses nail-based melee combat and magic spells.",
                     "edge_case", "hollow_knight"),
        TestQuestion("Can you jump on spikes?",
                     "No, spikes instantly damage the player. The player cannot safely stand on spikes without invincibility frames.",
                     "description", "hollow_knight"),
        TestQuestion("What is the lore of the Pale King?",
                     "The Pale King was the Wyrm ruler of Hallownest who created the Vessels and the Hollow Knight to contain the Radiance.",
                     "description", "hollow_knight"),
        TestQuestion("How many health masks does the player start with?",
                     "The player starts with 5 health masks.",
                     "numeric", "hollow_knight"),
        TestQuestion("Where is the Crystal Heart ability?",
                     "The Crystal Heart is found in the Crystal Peak, accessed after defeating the Crystal Guardian mini-boss.",
                     "location", "hollow_knight"),
    ],
    "oni": [
        TestQuestion("How do I generate oxygen in Oxygen Not Included?",
                     "Oxygen is primarily generated using Algae Terrariums, Oxygen Diffusers (early game), or Electrolyzers (mid-game) which split water into oxygen and hydrogen.",
                     "description", "oni"),
        TestQuestion("What is the best way to cool a base?",
                     "Cooling is done with Thermo Aquatuners cooling liquid in radiant pipes, with heat deleted by Steam Turbines.",
                     "description", "oni"),
        TestQuestion("How do I get rid of polluted water?",
                     "Polluted water can be filtered through a Water Sieve to produce clean water, or used to grow Reed Fiber in Hydroponic Farms.",
                     "description", "oni"),
        TestQuestion("What does the Atmo Suit do?",
                     "The Atmo Suit provides oxygen and protection from hazardous environments when worn by dupes.",
                     "description", "oni"),
        TestQuestion("How much power does a Coal Generator produce?",
                     "A Coal Generator produces 600 W of power.",
                     "numeric", "oni"),
        TestQuestion("What is needed for plastic production?",
                     "Plastic is produced from Petroleum in a Polymer Press, or by shearing Glossy Dreckos.",
                     "description", "oni"),
        TestQuestion("How do I deal with heat death?",
                     "Heat death is prevented by cooling systems using Thermo Aquatuners, Steam Turbines, and carefully managed heat deletion.",
                     "description", "oni"),
    ],
    "terraria": [
        TestQuestion("How do I summon the Eye of Cthulhu?",
                     "The Eye of Cthulhu is summoned by using a Suspicious Looking Eye at night, or it may spawn naturally if certain conditions are met.",
                     "location", "terraria"),
        TestQuestion("What items do I need to summon the Moon Lord?",
                     "The Moon Lord is summoned by using a Celestial Sigil after defeating the Lunar Events.",
                     "description", "terraria"),
        TestQuestion("How do I craft the Terraspark Boots?",
                     "The Terraspark Boots are crafted from Frostspark Boots and Lava Waders at a Tinkerer's Workshop.",
                     "description", "terraria"),
        TestQuestion("What is the health of the Moon Lord?",
                     "The Moon Lord has a total of 145,000 HP across its three parts.",
                     "numeric", "terraria"),
        TestQuestion("How do I get the Rod of Discord?",
                     "The Rod of Discord is a rare drop from Chaos Elementals found in the Underground Hallow.",
                     "description", "terraria"),
        TestQuestion("Can you fish in lava?",
                     "Yes, using a Hotline Fishing Hook or a Lavaproof Fishing Hook.",
                     "description", "terraria"),
        TestQuestion("How many boss summon items are in Terraria?",
                     "There are over 20 boss summon items in Terraria across all stages of the game.",
                     "numeric", "terraria"),
    ],
    "silksong": [
        TestQuestion("Who is the main character of Silksong?",
                     "The main character of Silksong is Hornet, the princess of Hallownest.",
                     "description", "silksong"),
        TestQuestion("What is Silk used for in Silksong?",
                     "Silk is a resource in Silksong used for crafting items and tools.",
                     "description", "silksong"),
        TestQuestion("What is the setting of Silksong?",
                     "Silksong takes place in a new kingdom called Pharloom, which is different from Hallownest.",
                     "description", "silksong"),
    ],
}

# ── RAGAS Evaluation ────────────────────────────────────────────────

def run_ragas_evaluation(game: str, questions: list, dry_run: bool = False) -> dict:
    """
    Run RAGAS evaluation for a specific game.
    
    For each question:
    1. Ask the multi-agent (uses both vectorstore + SQL)
    2. Collect the answer and retrieved contexts
    3. Compute RAGAS metrics
    
    Returns dict with metrics.
    """
    try:
        from ragas.metrics.collections import (
            Faithfulness,
            AnswerAccuracy,
            ContextPrecision,
            ContextRecall,
        )
    except ImportError:
        logger.error("ragas is required. Install with: pip install ragas")
        sys.exit(1)

    # ── 1. Gather answers and contexts ──
    logger.info(f"\n  Running {len(questions)} questions for {game}...")
    
    # Import agent
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
    from rag_agent.multi_agent import ask
    from rag_agent.vectorstore import get_retriever
    from rag_agent.game_registry import AVAILABLE_GAMES

    # 当前游戏的中文显示名，用于占位 history（见循环内注释）
    _name = AVAILABLE_GAMES[game].get("name", game)
    _m = re.search(r"\((.+?)\)", _name)
    game_zh = _m.group(1) if _m else _name

    results = []
    errors = []

    vs_path = str(GAMES_DIR / game / "vectorstore")
    has_vs = (GAMES_DIR / game / "vectorstore" / "index.faiss").exists()

    for i, q in enumerate(questions):
        logger.info(f"    [{i+1}/{len(questions)}] {q.question[:60]}...")

        # 预置会话游戏上下文：题库多为纯英文问题，不带游戏信号词，
        # 直接问会路由不到游戏而弹菜单。评估语义是"已确认游戏的会话中
        # 追问"，靠占位 history 让 _game_from_history 推导出当前游戏
        # （重构后已无 _LAST_GAME 全局，状态完全从 history 推导）。
        # 注意占位文本必须含中文显示名——内部键名（如 hollow_knight）
        # 不是信号词会路由失败；英文全名会让 silksong 误中 hollow_knight。

        # ── Run agent ──
        t0 = time.time()
        try:
            answer = ask(q.question, verbose=False,
                         history=[{"role": "user", "content": f"（评估会话：{game_zh}）"}])
        except Exception as e:
            logger.info(f"      ❌ Agent error: {e}")
            errors.append({"question": q.question, "error": str(e)})
            continue
        elapsed = time.time() - t0
        
        # ── Get retrieved contexts ──
        contexts = []
        if has_vs:
            try:
                from rag_agent.vectorstore import load_vectorstore
                retriever = get_retriever(vs_path, k=3)
                docs = retriever.invoke(q.question)
                contexts = [d.page_content[:500] for d in docs]
            except Exception:
                contexts = ["(retrieval unavailable)"]
        else:
            contexts = ["(no vectorstore)"]
        
        # ── For numeric questions, simulate what the SQL tool might retrieve ──
        # (In production the agent uses a SQL tool; here we include the DB context
        #  to let RAGAS evaluate properly)
        if q.category == "numeric":
            try:
                db_path = str(GAMES_DIR / game / f"{game}_data.db")
                if os.path.isfile(db_path):
                    import sqlite3
                    conn = sqlite3.connect(db_path)
                    cur = conn.cursor()
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [r[0] for r in cur.fetchall()]
                    db_summary = f"Database tables: {', '.join(tables)}"
                    # Try to get sample data
                    sample_rows = ""
                    for t in tables[:2]:
                        try:
                            cur.execute(f'SELECT * FROM "{t}" LIMIT 3')
                            cols = [d[0] for d in cur.description]
                            sample_rows += f"\n  {t} ({', '.join(cols)}): {cur.fetchall()}"
                        except:
                            pass
                    contexts.append(f"Structured DB available: {tables}. Sample: {sample_rows[:300]}")
                    conn.close()
            except:
                pass
        
        results.append({
            "question": q.question,
            "ground_truth": q.ground_truth,
            "answer": answer,
            "contexts": contexts,
            "category": q.category,
            "elapsed_ms": round(elapsed * 1000),
        })
    
    if not results:
        return {"game": game, "error": "All questions failed", "errors": errors}
    
    # ── 2~3. 逐题计算 RAGAS 指标 ──
    # ragas 0.4：collections 指标不走 evaluate()（它不是 ragas.metrics.base.Metric
    # 的子类，会被 evaluate 的类型校验拒绝），官方用法是逐条 ascore 自行聚合。
    # collections 指标只接受现代 InstructorLLM，用 llm_factory + OpenAI 兼容
    # 客户端（DeepSeek 端点）。
    # answer_relevancy 需要 embeddings，但 DeepSeek 无 embedding API、本地
    # sentence-transformers 未安装，故改用纯 LLM 的 answer_accuracy
    # （双裁判对比 ground_truth，对本评测集更贴切）。
    logger.info(f"\n  Computing RAGAS metrics for {game}...")

    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    # 指标走 agenerate()（异步），client 必须是 AsyncOpenAI，同步 client 会被拒
    ragas_llm = llm_factory(
        DEEPSEEK_MODEL,
        client=AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL),
    )

    metrics_config = {
        "faithfulness": Faithfulness(llm=ragas_llm),
        "answer_accuracy": AnswerAccuracy(llm=ragas_llm),
        "context_precision": ContextPrecision(llm=ragas_llm),
        "context_recall": ContextRecall(llm=ragas_llm),
    }

    async def _score_questions() -> list:
        """逐题计算各指标，返回与 results 对齐的 [{指标名: 分数}, ...]。"""
        rows = []
        for r in results:
            kwargs = {
                "user_input": r["question"],
                "response": r["answer"],
                "retrieved_contexts": r["contexts"],
                "reference": r["ground_truth"],
            }
            row = {}
            for name, metric in metrics_config.items():
                # 各指标 ascore 签名不同，按签名挑参数
                params = inspect.signature(metric.ascore).parameters
                try:
                    res = await metric.ascore(
                        **{k: v for k, v in kwargs.items() if k in params})
                    row[name] = float(res.value)
                except Exception as e:
                    logger.info(f"      ⚠️ {name} 打分失败（{r['question'][:30]}…）: {e}")
                    row[name] = None
            rows.append(row)
        return rows

    def _mean(values):
        vals = [v for v in values if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    t0 = time.time()
    score_rows = asyncio.run(_score_questions())
    eval_time = time.time() - t0

    # ── 4. Format results ──
    report = {
        "game": game,
        "num_questions": len(results),
        "eval_time_seconds": round(eval_time, 1),
        "metrics": {},
        "per_category": {},
        "questions_detail": [],
        "errors": errors,
    }

    # Overall metrics（忽略单题失败的 None）
    for name in metrics_config:
        report["metrics"][name] = _mean(row[name] for row in score_rows)

    # Per-category breakdown
    categories = set(r["category"] for r in results)
    for cat in categories:
        idx = [i for i, r in enumerate(results) if r["category"] == cat]
        report["per_category"][cat] = {
            "count": len(idx),
            "metrics": {name: _mean(score_rows[i][name] for i in idx)
                        for name in metrics_config},
        }

    # Per-question detail（附各指标单题分数）
    for r, row in zip(results, score_rows):
        report["questions_detail"].append({
            "question": r["question"],
            "category": r["category"],
            "answer_length": len(r["answer"]),
            "num_contexts": len(r["contexts"]),
            "elapsed_ms": r["elapsed_ms"],
            "scores": row,
        })

    return report


# ── Report Output ───────────────────────────────────────────────────

def print_report(report: dict):
    """Pretty-print the evaluation report."""
    print(f"\n{'='*60}")
    print(f"  RAGAS Evaluation: {report.get('game', '?')}")
    print(f"{'='*60}")
    
    if "error" in report:
        print(f"  ❌ Error: {report['error']}")
        return
    
    metrics = report["metrics"]
    print(f"\n  Questions: {report['num_questions']}")
    print(f"  Eval time: {report['eval_time_seconds']}s")
    
    print(f"\n  {'Overall Metrics':-^40}")
    metric_labels = {
        "faithfulness": "Faithfulness (factual accuracy)",
        "answer_accuracy": "Answer Accuracy (vs ground truth)",
        "context_precision": "Context Precision (retrieval relevance)",
        "context_recall": "Context Recall (retrieval completeness)",
    }
    for name, label in metric_labels.items():
        v = metrics.get(name)
        if v is not None:
            bar = "█" * int(v * 20) + "░" * (20 - int(v * 20))
            print(f"  {label:30s}: {bar} {v:.3f}")
        else:
            print(f"  {label:30s}: N/A")
    
    if report.get("per_category"):
        print(f"\n  {'Per-Category Breakdown':-^40}")
        for cat, data in report["per_category"].items():
            print(f"  [{cat}] ({data['count']} questions)")
            for m, v in data["metrics"].items():
                if v is not None:
                    print(f"    {m:20s}: {v:.3f}")
    
    if report.get("questions_detail"):
        print(f"\n  {'Per-Question Detail (latency)':-^40}")
        for q in report["questions_detail"]:
            print(f"  [{q['category']:10s}] {q['elapsed_ms']:5d}ms  {q['question'][:50]}...")
    
    if report.get("errors"):
        print(f"\n  {'Errors':-^40}")
        for e in report["errors"]:
            print(f"  ❌ {e['question'][:40]}: {e['error']}")


def save_report(report: dict):
    """Save report to JSON."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    game = report.get("game", "unknown")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"ragas_{game}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"\n  Report saved: {path}")
    return path


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RAGAS Tier 2 Evaluation")
    parser.add_argument("--game", default="all", 
                        help="Game to evaluate (all, hollow_knight, oni, terraria, silksong)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit questions per game (0 = all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print questions and exit without running")
    args = parser.parse_args()
    
    games = list(QUESTIONS.keys()) if args.game == "all" else [args.game]
    
    for game in games:
        if game not in QUESTIONS:
            logger.info(f"Unknown game: {game}. Available: {list(QUESTIONS.keys())}")
            continue
        
        questions = QUESTIONS[game]
        if args.limit:
            questions = questions[:args.limit]
        
        if args.dry_run:
            print(f"\n{'='*40}")
            print(f"  {game} — {len(questions)} questions")
            for q in questions:
                print(f"  [{q.category:10s}] {q.question[:60]}")
            continue
        
        report = run_ragas_evaluation(game, questions, dry_run=args.dry_run)
        print_report(report)
        save_report(report)
        
        # Empty line between games
        print()


if __name__ == "__main__":
    main()
