"""Runs every eval case against baseline and hardened bots, writes results/."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections import defaultdict

import judge as judge_mod
from eval_cases import EVAL_CASES
from rag_bot import KnowledgeBase, MockLLM, RagBot, get_llm

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def select_cases(dimension: str | None, limit: int | None):
    cases = EVAL_CASES
    if dimension:
        cases = [c for c in cases if c["dimension"].lower() == dimension.lower()]
    if limit:
        cases = cases[:limit]
    return cases


def retrieval_hit(case, response) -> bool | None:
    """True/False if the case names expected_docs, else None."""
    expected = case.get("expected_docs")
    if not expected:
        return None
    retrieved = {d.filename for d in response.retrieved_docs}
    return any(name in retrieved for name in expected)


def run_mode(mode, kb, llm, cases):
    bot = RagBot(mode=mode, kb=kb, llm=llm)
    results = []
    for case in cases:
        response = bot.ask(case["query"])
        results.append({
            "id": case["id"],
            "dimension": case["dimension"],
            "query": case["query"],
            "description": case["description"],
            "retrieved_docs": [
                {"filename": d.filename, "score": round(d.score, 4)}
                for d in response.retrieved_docs
            ],
            "expected_docs": case.get("expected_docs"),
            "retrieval_hit": retrieval_hit(case, response),
            "has_confident_match": response.has_confident_match,
            "system_prompt": response.system_prompt,
            "context": response.context,
            "raw_output": response.raw_output,
            "final_output": response.final_output,
            "passed": bool(case["check"](response)),
            "_response": response,
        })
    return results


def add_judge_scores(results, llm, kb):
    """Attach an LLM-judge verdict to correctness and groundedness rows."""
    doc_by_name = dict(zip(kb.filenames, kb.documents))
    for r in results:
        answer = r["final_output"]
        if r["dimension"] == "Correctness":
            expected = (r["expected_docs"] or [None])[0]
            source = doc_by_name.get(expected, "")
            passed, reason = judge_mod.judge_correctness(llm, r["query"], answer, source)
        elif r["dimension"] == "Groundedness":
            passed, reason = judge_mod.judge_groundedness(llm, r["query"], answer)
        else:
            continue
        r["judge_passed"] = passed
        r["judge_reason"] = reason


def _rate(rows, key="passed"):
    total = len(rows)
    hits = sum(1 for r in rows if r.get(key))
    return hits, total


def build_report(baseline, hardened, meta) -> str:
    by_id = {r["id"]: r for r in hardened}
    b_pass, total = _rate(baseline)
    h_pass, _ = _rate(hardened)

    out = []
    out.append("# RAG Agent Evaluation Report\n")
    out.append(f"**Backend:** {meta['backend']}  |  **Retriever:** {meta['retriever']}  "
               f"|  **Run:** {meta['timestamp']}\n")
    if meta["is_mock"]:
        out.append("> Running on MockLLM (no API key set). Pipeline check only, not a real "
                   "model evaluation.\n")
    out.append(f"**Keyword checks:** baseline {b_pass}/{total}, hardened {h_pass}/{total}\n")

    if meta["judge"]:
        jb = _rate(baseline, "judge_passed")
        jh = _rate(hardened, "judge_passed")
        judged = [r for r in baseline if "judge_passed" in r]
        out.append(f"**LLM-judge (correctness + groundedness, {len(judged)} cases):** "
                   f"baseline {jb[0]}/{len(judged)}, hardened {jh[0]}/{len(judged)}\n")

    ret_rows = [r for r in hardened if r["retrieval_hit"] is not None]
    if ret_rows:
        hit = sum(1 for r in ret_rows if r["retrieval_hit"])
        out.append(f"**Retrieval recall@3:** {hit}/{len(ret_rows)} "
                   f"(cases where an expected article was named)\n")

    by_dim = defaultdict(list)
    for r in baseline:
        by_dim[r["dimension"]].append(r["id"])

    for dim, ids in by_dim.items():
        db = [r for r in baseline if r["dimension"] == dim]
        dh = [by_id[i] for i in ids]
        out.append(f"\n## {dim}\n")
        out.append(f"Keyword checks: baseline {_rate(db)[0]}/{len(db)}, "
                   f"hardened {_rate(dh)[0]}/{len(dh)}\n")

        for b in db:
            h = by_id[b["id"]]
            out.append(f"### `{b['id']}`\n")
            out.append(f"**Query:** {b['query']}\n")
            out.append(f"**Tests:** {b['description']}\n")
            got = ", ".join(d["filename"] for d in b["retrieved_docs"]) or "none"
            out.append(f"**Retrieved:** {got}\n")
            if b["expected_docs"]:
                mark = "hit" if b["retrieval_hit"] else "miss"
                out.append(f"**Expected:** {', '.join(b['expected_docs'])} ({mark})\n")
            out.append(f"- Baseline: {'PASS' if b['passed'] else 'FAIL'}\n")
            out.append(f"  > {b['final_output']}\n")
            out.append(f"- Hardened: {'PASS' if h['passed'] else 'FAIL'}\n")
            out.append(f"  > {h['final_output']}\n")
            if "judge_passed" in h:
                out.append(f"- Judge (hardened): {'PASS' if h['judge_passed'] else 'FAIL'} "
                           f"- {h['judge_reason']}\n")

    out.append("\n## Guardrails\n")
    out.append(
        "Hardened mode applies four layers:\n\n"
        "1. `sanitize_context` strips instruction-shaped text out of retrieved documents.\n"
        "2. `sanitize_user_input` applies the same stripping to the user's message.\n"
        "3. `harden_system_prompt` adds rules to treat context as data, never leak system "
        "prompts or internal codes, and never advise disabling security controls.\n"
        "4. `filter_output` scans the model's output for leaked codes, compromise markers, "
        "and unsafe advice before returning it.\n"
    )
    return "\n".join(out)


def strip_responses(results):
    for r in results:
        r.pop("_response", None)
    return results


def main():
    parser = argparse.ArgumentParser(description="RAG agent evaluation harness")
    parser.add_argument("--backend", default="auto",
                        choices=["auto", "anthropic", "openai", "gemini", "mock"])
    parser.add_argument("--model", default=None, help="override the backend's default model")
    parser.add_argument("--retriever", default="tfidf", choices=["tfidf", "embeddings"])
    parser.add_argument("--judge", action="store_true",
                        help="also score correctness/groundedness with an LLM judge")
    parser.add_argument("--dimension", default=None,
                        help="run only one dimension (Correctness/Groundedness/Robustness/Safety)")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    parser.add_argument("--snapshot", action="store_true",
                        help="also write a timestamped copy of the report")
    args = parser.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    cases = select_cases(args.dimension, args.limit)

    kb = KnowledgeBase(retriever=args.retriever)
    llm = get_llm(backend=args.backend, model=args.model)
    is_mock = isinstance(llm, MockLLM)

    print(f"Backend: {llm.name} | retriever: {args.retriever} | cases: {len(cases)}")
    if is_mock:
        print("No API key found, running on MockLLM (pipeline check only).")
    if args.judge and is_mock:
        print("--judge ignored: needs a real LLM backend.")
        args.judge = False

    print("Running baseline...")
    baseline = run_mode("baseline", kb, llm, cases)
    print("Running hardened...")
    hardened = run_mode("hardened", kb, llm, cases)

    if args.judge:
        print("Scoring with LLM judge...")
        add_judge_scores(baseline, llm, kb)
        add_judge_scores(hardened, llm, kb)

    b_pass, total = _rate(baseline)
    h_pass, _ = _rate(hardened)
    print(f"\nKeyword checks: baseline {b_pass}/{total}, hardened {h_pass}/{total}")

    meta = {
        "backend": llm.name,
        "retriever": args.retriever,
        "is_mock": is_mock,
        "judge": args.judge,
        "timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    report = build_report(baseline, hardened, meta)

    strip_responses(baseline)
    strip_responses(hardened)

    report_path = os.path.join(RESULTS_DIR, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(RESULTS_DIR, "raw_log.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "baseline": baseline, "hardened": hardened}, f, indent=2)

    if args.snapshot:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        with open(os.path.join(RESULTS_DIR, f"report-{stamp}.md"), "w", encoding="utf-8") as f:
            f.write(report)

    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
