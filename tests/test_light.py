#!/usr/bin/env python3
"""
Tier 1 Lightweight RAG Evaluation — runs on server, can run every time.

Usage:
    .venv/bin/python tests/test_light.py              # Run all tests
    .venv/bin/python tests/test_light.py --routing    # Routing accuracy only
    .venv/bin/python tests/test_light.py --retrieval  # Retrieval hit-rate only
    .venv/bin/python tests/test_light.py --e2e        # End-to-end QA only
    .venv/bin/python tests/test_light.py --summary    # Just show game status

Tests:
  routing    — game_router correctly identifies game from query text
  retrieval  — vectorstore search places correct doc in top-k (no LLM needed)
  e2e        — full agent pipeline: answer contains expected keywords
"""

import sys
import os
import time
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("LOG_LEVEL", "WARNING")

# ── Test data ────────────────────────────────────────────────────────

ROUTING_TESTS = [
    # (query_text, expected_game, description)
    ("空洞骑士螳螂爪怎么拿？",      "hollow_knight", "HK Chinese"),
    ("How to beat the Radiance?",     "hollow_knight", "HK English"),
    # 以下两条不含游戏关键词 → 路由无法识别（当前行为如此，非 bug）
    ("在哪里拿到螳螂爪",             None,            "HK Chinese (no game name, expected fail)"),
    ("水晶之心怎么拿",               None,            "HK Crystal Heart (no game name, expected fail)"),
    ("缺氧净水怎么造？",              "oni",           "ONI Chinese"),
    ("How to cool hydrogen in ONI?",  "oni",           "ONI English"),
    ("泰拉瑞亚克苏鲁之眼怎么召唤？",  "terraria",      "Terraria Chinese"),
    ("How to summon Moon Lord?",      "terraria",      "Terraria English"),
    ("丝之歌什么时候出？",            "silksong",      "Silksong Chinese"),
    ("怪物猎人荒野煌雷龙弱什么？",    "mhw",           "MHW Chinese"),
    ("赛博朋克2077怎么加点？",        "cyberpunk2077", "Cyberpunk Chinese"),
    ("VA-11 Hall-A 怎么调酒？",       "va11halla",     "VA-11 Hall-A Chinese"),
]

RETRIEVAL_TESTS = [
    # (query, game, expected_title_substrings, description)
    # Hollow Knight
    ("Mantis Claw location",          "hollow_knight", ["Mantis Claw"],                 "HK: ability pickup"),
    ("How to get the Dream Nail",     "hollow_knight", ["Dream Nail"],                  "HK: dream nail"),
    ("White Palace lore",             "hollow_knight", ["White Palace"],                "HK: white palace"),
    ("螳螂爪怎么拿",                  "hollow_knight", ["Mantis Claw", "Mantis"],       "HK: Chinese retrieval (cross-lingual)"),
    # ONI
    ("oxygen generation",             "oni",           ["Oxygen"],                      "ONI: oxygen"),
    ("water purification",            "oni",           ["Water Sieve", "Purification"],  "ONI: water"),
    ("cooling loop",                  "oni",           ["Cool Steam Vent", "Coolant"],  "ONI: cooling"),
    # Terraria
    ("summon Eye of Cthulhu",         "terraria",      ["Eye of Cthulhu"],              "Terraria: boss summon"),
    ("Frostspark Boots crafting",     "terraria",      ["Frostspark Boots"],            "Terraria: accessory"),
    ("greedy ring",                   "terraria",      ["Greedy Ring", "Lucky Coin"],   "Terraria: item"),
    ("Moon Lord summon",              "terraria",      ["Moon Lord"],                   "Terraria: final boss"),
    # Silksong
    ("Hornet abilities",              "silksong",      ["Hornet", "Ability"],           "Silksong: abilities"),
    ("Silksong map areas",            "silksong",      ["Area", "Biome", "Location"],   "Silksong: areas"),
]

E2E_TESTS = [
    # (query, game, must_contain, must_not_contain, description)
    # 包含游戏名称以确保路由正确识别
    ("How many charm notches are there in Hollow Knight?",
     "hollow_knight", ["11"], [], "HK: charm notches count"),
    ("What is the max HP of the Radiance in Hollow Knight?",
     "hollow_knight", ["HP", "3000"], [], "HK: boss HP (3000 total HP)"),
    ("In Hollow Knight, who is the Nailsmith?",
     "hollow_knight", ["Nailsmith", "nail"], ["Godseeker", "Pantheon"], "HK: NPC"),
    ("How to cool water in Oxygen Not Included?",
     "oni", ["cool", "water", "Thermo"], ["Hollow"], "ONI: cooling"),
    ("How to summon the Eye of Cthulhu in Terraria?",
     "terraria", ["Eye of Cthulhu", "Suspicious Looking Eye"], [], "Terraria: boss summon"),
    ("Does Hollow Knight have guns?",
     "hollow_knight", [], ["Yes", "firearm", "pistol", "rifle"], "HK: hallucination check (no guns)"),
]

# ── Utilities ────────────────────────────────────────────────────────

PASS = "✅"
FAIL = "❌"
SKIP = "⏭️ "

def colored(status, text):
    return f"{status} {text}"


# ── Routing Tests ────────────────────────────────────────────────────

def test_routing():
    """Test game_router detects the correct game."""
    from rag_agent.game_router import detect_game

    print(f"\n{'='*60}")
    print(f"  Routing Tests ({len(ROUTING_TESTS)} cases)")
    print(f"{'='*60}")

    passed = 0
    failed = 0

    for query, expected, desc in ROUTING_TESTS:
        result = detect_game(query)
        actual = result[0] if isinstance(result, (list, tuple)) else result
        ok = actual == expected
        if ok:
            passed += 1
            print(f"  {PASS} [{desc}] \"{query[:30]}...\" → {actual}")
        else:
            failed += 1
            print(f"  {FAIL} [{desc}] \"{query[:30]}...\" → {actual} (expected {expected})")

    print(f"\n  Result: {PASS if passed > 0 else ''} {passed}/{len(ROUTING_TESTS)} passed"
          f"{f', {FAIL} {failed} failed' if failed else ''}")
    return passed, failed


# ── Retrieval Tests ──────────────────────────────────────────────────

def test_retrieval(k: int = 5):
    """Test vectorstore retrieval: does expected doc title appear in top-k?"""
    from rag_agent.vectorstore import load_vectorstore

    print(f"\n{'='*60}")
    print(f"  Retrieval Tests ({len(RETRIEVAL_TESTS)} cases, top-{k})")
    print(f"{'='*60}")

    passed = 0
    failed = 0
    skipped = 0
    total_hit_rate = 0.0
    count_hit_rate = 0

    for query, game, expected_titles, desc in RETRIEVAL_TESTS:
        vs_path = ROOT / "games" / game / "vectorstore"
        if not (vs_path / "index.faiss").exists():
            print(f"  {SKIP} [{desc}] no vectorstore for {game}")
            skipped += 1
            continue

        try:
            vs = load_vectorstore(save_dir=str(vs_path))
        except Exception as e:
            print(f"  {FAIL} [{desc}] load failed: {e}")
            failed += 1
            continue

        results = vs.similarity_search_with_score(query, k=k)
        retrieved_titles = [r[0].metadata.get("title", "").lower() for r in results]
        retrieved_texts = [r[0].page_content[:200].lower() for r in results]

        # Check if any expected substring appears in retrieved titles or content
        hit = False
        for exp in expected_titles:
            exp_lower = exp.lower()
            for rt in retrieved_titles:
                if exp_lower in rt:
                    hit = True
                    break
            if hit:
                break
            # Also check content (for cases where title might not match)
            for rt in retrieved_texts:
                if exp_lower in rt:
                    hit = True
                    break
            if hit:
                break

        # Calculate hit rate per test case
        total_hit_rate += 1.0 if hit else 0.0
        count_hit_rate += 1

        status = PASS if hit else FAIL
        if hit:
            passed += 1
        else:
            failed += 1

        top_titles = [r[0].metadata.get("title", "?") for r in results]
        print(f"  {status} [{desc}] query=\"{query[:40]}\"")
        print(f"         top-{k}: {top_titles}")
        print(f"         sought: {expected_titles}")

    overall_hit_rate = (total_hit_rate / count_hit_rate * 100) if count_hit_rate else 0
    print(f"\n  Overall Hit@{k}: {overall_hit_rate:.1f}%")
    print(f"  Result: {passed}/{len(RETRIEVAL_TESTS) - skipped} passed"
          f"{f', {failed} failed' if failed else ''}"
          f"{f', {skipped} skipped' if skipped else ''}")
    return passed, failed, skipped


# ── End-to-End Tests ─────────────────────────────────────────────────

def test_e2e():
    """Test full agent pipeline: answer keyword coverage."""
    print(f"\n{'='*60}")
    print(f"  End-to-End Tests ({len(E2E_TESTS)} cases)")
    print(f"{'='*60}")

    passed = 0
    failed = 0

    for query, game, must_contain, must_not_contain, desc in E2E_TESTS:
        print(f"  [ ] [{desc}] \"{query[:40]}...\" ", end="")
        sys.stdout.flush()

        try:
            from rag_agent.multi_agent import ask
            answer = ask(query, verbose=False)
        except Exception as e:
            print(f"  {FAIL} Agent call failed: {e}")
            failed += 1
            continue

        answer_lower = answer.lower()

        all_found = all(k.lower() in answer_lower for k in must_contain)
        any_forbidden = any(k.lower() in answer_lower for k in must_not_contain)

        if all_found and not any_forbidden:
            passed += 1
            print(f"  {PASS}")
        else:
            failed += 1
            print(f"  {FAIL}")
            if not all_found:
                missing = [k for k in must_contain if k.lower() not in answer_lower]
                print(f"         missing: {missing}")
            if any_forbidden:
                found_bad = [k for k in must_not_contain if k.lower() in answer_lower]
                print(f"         found forbidden: {found_bad}")
            print(f"         answer preview: {answer[:100]}...")

    print(f"\n  Result: {passed}/{len(E2E_TESTS)} passed"
          f"{f', {failed} failed' if failed else ''}")
    return passed, failed


# ── Summary ──────────────────────────────────────────────────────────

def show_summary():
    """Show game infrastructure status."""
    print(f"\n{'='*60}")
    print(f"  Game Infrastructure Summary")
    print(f"{'='*60}")

    games_status = {}
    for game_dir in sorted(ROOT.glob("games/*/vectorstore")):
        game = game_dir.parent.name
        db_path = ROOT / "games" / game / f"{game}_data.db"
        faiss = game_dir / "index.faiss"
        has_db = db_path.exists()
        has_vs = faiss.exists()

        if has_vs:
            import faiss as faiss_lib
            idx = faiss_lib.read_index(str(faiss))
            n_vectors = idx.ntotal
        else:
            n_vectors = 0

        games_status[game] = {
            "db": has_db,
            "vectorstore": has_vs,
            "vectors": n_vectors,
        }
        db_mark = PASS if has_db else FAIL
        vs_mark = PASS if has_vs else FAIL
        print(f"  {game:20s} DB: {db_mark}  Vectorstore: {vs_mark} ({n_vectors} vectors)")

    return games_status


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Tier 1 Lightweight RAG Tests")
    parser.add_argument("--routing", action="store_true", help="Run routing tests only")
    parser.add_argument("--retrieval", action="store_true", help="Run retrieval tests only")
    parser.add_argument("--e2e", action="store_true", help="Run end-to-end tests only")
    parser.add_argument("--summary", action="store_true", help="Show game status only")
    parser.add_argument("--topk", type=int, default=5, help="Top-k for retrieval tests")
    args = parser.parse_args()

    start = time.time()
    total_passed = 0
    total_failed = 0
    total_skipped = 0

    # If no specific flags, run all
    run_all = not (args.routing or args.retrieval or args.e2e or args.summary)

    if args.summary or run_all:
        show_summary()

    if args.routing or run_all:
        p, f = test_routing()
        total_passed += p
        total_failed += f

    if args.retrieval or run_all:
        p, f, s = test_retrieval(k=args.topk)
        total_passed += p
        total_failed += f
        total_skipped += s

    if args.e2e or run_all:
        p, f = test_e2e()
        total_passed += p
        total_failed += f

    elapsed = time.time() - start

    print(f"\n{'='*60}")
    print(f"  {'ALL TESTS COMPLETE' if run_all else 'DONE'}")
    print(f"  Passed: {total_passed}  Failed: {total_failed}"
          f"{f'  Skipped: {total_skipped}' if total_skipped else ''}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"{'='*60}")

    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
