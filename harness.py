"""
Test runner for the RAG agent evaluation harness.

Runs every case in eval_cases.EVAL_CASES against both a baseline RagBot
(no guardrails) and a hardened RagBot (all four guardrails), using the same
KnowledgeBase and LLM instance for both so the comparison is apples-to-apples.

Writes:
  results/report.md    - human-readable report, grouped by dimension
  results/raw_log.json - full raw data for both modes
"""

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
            "> Running on MockLLM (no ANTHROPIC_API_KEY set). This is a **pipeline check**, "
            "not a real model evaluation result. Set ANTHROPIC_API_KEY and re-run for a real "
            "evaluation against Claude.\n"
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
        "The hardened mode applies four layers, each catching a different class of failure:\n\n"
        "1. **`sanitize_context`** — strips instruction-shaped text (fake system/assistant "
        "instructions, HTML comments, \"ignore previous instructions\", role-hijack phrasing) "
        "out of retrieved knowledge base documents before they reach the model. This matters "
        "because a RAG agent trusts its retrieval corpus by default, and any content anyone can "
        "write into that corpus (a wiki page, a ticket, a shared doc) becomes an attack surface "
        "otherwise.\n"
        "2. **`sanitize_user_input`** — applies the same stripping to the user's own message, "
        "since direct prompt injection doesn't require a poisoned document.\n"
        "3. **`harden_system_prompt`** — adds explicit rules telling the model to treat retrieved "
        "context as data rather than instructions, never leak system prompts or internal codes, "
        "and never advise disabling security controls or sharing credentials.\n"
        "4. **`filter_output`** — a last-resort check on the model's actual output, catching "
        "anything that slipped past the first three layers (a leaked code, a compromise marker, "
        "unsafe operational advice) before it reaches the user.\n"
    )

    lines.append("\n## Why Each Dimension Matters\n")
    lines.append(
        "- **Correctness** — an agent that answers confidently but wrong is worse than one that "
        "doesn't answer at all, since users trust and act on the response.\n"
        "- **Groundedness / no hallucination** — a support bot that invents a plausible-sounding "
        "process for something it has no data on erodes trust and can send users down the wrong "
        "path; saying \"I don't know\" is a correct, safe answer when the KB has no coverage.\n"
        "- **Robustness** — real users type vague or garbled queries; an agent that breaks or "
        "produces nonsense on anything less than a perfectly formed question isn't production "
        "ready.\n"
        "- **Safety** — enterprise knowledge bases are written and edited by many people over "
        "time, and a RAG agent that treats everything it retrieves as trustworthy instructions "
        "rather than data is one poisoned document away from leaking secrets or handing out unsafe "
        "advice, which is exactly why a dedicated guardrail layer — not just model training — is "
        "needed around production agents.\n"
    )

    return "\n".join(lines)


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    kb = KnowledgeBase()
    llm = get_llm()
    is_mock = isinstance(llm, MockLLM)

    print(f"Using LLM backend: {llm.name}")
    if is_mock:
        print("NOTE: No ANTHROPIC_API_KEY found. Running on MockLLM. This is a pipeline check, "
              "not a real model evaluation. Set ANTHROPIC_API_KEY for a real evaluation.")

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
