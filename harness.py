"""Runs every eval case against baseline and hardened bots, writes results/."""

import json
import os
from collections import defaultdict

from rag_bot import KnowledgeBase, RagBot, get_llm, MockLLM
from eval_cases import EVAL_CASES

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def run_mode(mode: str, kb: KnowledgeBase, llm) -> list:
    bot = RagBot(mode=mode, kb=kb, llm=llm)
    results = []
    for case in EVAL_CASES:
        response = bot.ask(case["query"])
        passed = bool(case["check"](response))
        results.append({
            "id": case["id"],
            "dimension": case["dimension"],
            "query": case["query"],
            "description": case["description"],
            "retrieved_docs": [
                {"filename": d.filename, "score": round(d.score, 4)} for d in response.retrieved_docs
            ],
            "has_confident_match": response.has_confident_match,
            "system_prompt": response.system_prompt,
            "context": response.context,
            "raw_output": response.raw_output,
            "final_output": response.final_output,
            "passed": passed,
        })
    return results


def build_report(baseline_results, hardened_results, llm_name: str, is_mock: bool) -> str:
    by_id_hardened = {r["id"]: r for r in hardened_results}

    total = len(baseline_results)
    baseline_passed = sum(1 for r in baseline_results if r["passed"])
    hardened_passed = sum(1 for r in hardened_results if r["passed"])

    lines = []
    lines.append("# RAG Agent Evaluation Report\n")
    lines.append(f"**LLM backend:** {llm_name}\n")
    if is_mock:
        lines.append(
            "> Running on MockLLM (no API key set). This is a pipeline check, not a real "
            "model evaluation. Set an API key and re-run for real results.\n"
        )
    lines.append(f"**Overall summary:** Baseline: {baseline_passed}/{total} passed, "
                 f"Hardened: {hardened_passed}/{total} passed\n")

    by_dimension = defaultdict(list)
    for r in baseline_results:
        by_dimension[r["dimension"]].append(r["id"])

    for dimension, ids in by_dimension.items():
        dim_baseline = [r for r in baseline_results if r["dimension"] == dimension]
        dim_hardened = [by_id_hardened[i] for i in ids]
        b_pass = sum(1 for r in dim_baseline if r["passed"])
        h_pass = sum(1 for r in dim_hardened if r["passed"])

        lines.append(f"\n## {dimension}\n")
        lines.append(f"Baseline: {b_pass}/{len(dim_baseline)} passed | "
                     f"Hardened: {h_pass}/{len(dim_hardened)} passed\n")

        for b in dim_baseline:
            h = by_id_hardened[b["id"]]
            lines.append(f"### `{b['id']}`\n")
            lines.append(f"**Query:** {b['query']}\n")
            lines.append(f"**Tests:** {b['description']}\n")
            lines.append(f"**Retrieved docs:** {', '.join(d['filename'] for d in b['retrieved_docs']) or 'none'}\n")
            lines.append(f"- Baseline: {'PASS' if b['passed'] else 'FAIL'}\n")
            lines.append(f"  > {b['final_output']}\n")
            lines.append(f"- Hardened: {'PASS' if h['passed'] else 'FAIL'}\n")
            lines.append(f"  > {h['final_output']}\n")

    lines.append("\n## Guardrails Applied\n")
    lines.append(
        "Hardened mode applies four layers:\n\n"
        "1. `sanitize_context` strips instruction-shaped text out of retrieved documents "
        "before they reach the model.\n"
        "2. `sanitize_user_input` applies the same stripping to the user's message.\n"
        "3. `harden_system_prompt` adds rules to treat context as data, never leak system "
        "prompts or internal codes, and never advise disabling security controls.\n"
        "4. `filter_output` checks the model's output for leaked codes, compromise markers, "
        "and unsafe advice before returning it.\n"
    )

    return "\n".join(lines)


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    kb = KnowledgeBase()
    llm = get_llm()
    is_mock = isinstance(llm, MockLLM)

    print(f"Using LLM backend: {llm.name}")
    if is_mock:
        print("No API key found, running on MockLLM (pipeline check only).")

    print("\nRunning baseline mode...")
    baseline_results = run_mode("baseline", kb, llm)

    print("Running hardened mode...")
    hardened_results = run_mode("hardened", kb, llm)

    total = len(baseline_results)
    baseline_passed = sum(1 for r in baseline_results if r["passed"])
    hardened_passed = sum(1 for r in hardened_results if r["passed"])
    print(f"\nBaseline: {baseline_passed}/{total} passed")
    print(f"Hardened: {hardened_passed}/{total} passed")

    report = build_report(baseline_results, hardened_results, llm.name, is_mock)
    report_path = os.path.join(RESULTS_DIR, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    raw_log = {
        "llm_backend": llm.name,
        "is_mock": is_mock,
        "baseline": baseline_results,
        "hardened": hardened_results,
    }
    raw_log_path = os.path.join(RESULTS_DIR, "raw_log.json")
    with open(raw_log_path, "w", encoding="utf-8") as f:
        json.dump(raw_log, f, indent=2)

    print(f"\nWrote {report_path}")
    print(f"Wrote {raw_log_path}")


if __name__ == "__main__":
    main()
