"""LLM-as-judge scoring for the semantic dimensions (correctness, groundedness).

The keyword checks in eval_cases.py are deterministic but brittle against
phrasing variance. When a real LLM backend is available, the harness can also
run these judge calls and report both scores side by side.

Each judge returns (passed: bool, reason: str).
"""

from __future__ import annotations

import json
import re

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

CORRECTNESS_RUBRIC = (
    "You are grading an IT support assistant's answer against the single source "
    "document it should have used. Reply with JSON only: "
    '{"pass": true|false, "reason": "<one sentence>"}. '
    "pass is true only if the answer is factually consistent with the source and "
    "actually answers the question. Extra caution: a confident answer that "
    "contradicts the source is a fail."
)

GROUNDEDNESS_RUBRIC = (
    "You are grading whether an IT support assistant correctly declined to answer. "
    "The knowledge base has no article covering this question. Reply with JSON only: "
    '{"pass": true|false, "reason": "<one sentence>"}. '
    "pass is true only if the answer clearly says it does not have the information "
    "(optionally pointing the user elsewhere) and does NOT invent a specific "
    "process, number, or policy."
)


def _ask(llm, rubric: str, payload: str) -> tuple[bool, str]:
    raw = llm.generate(
        system_prompt=rubric,
        context="",
        query=payload,
        has_confident_match=True,
    )
    match = _JSON_BLOCK.search(raw or "")
    if match:
        try:
            data = json.loads(match.group(0))
            return bool(data.get("pass")), str(data.get("reason", "")).strip()
        except json.JSONDecodeError:
            pass
    # Fallback: look for a bare verdict.
    lowered = (raw or "").lower()
    if "pass" in lowered and "true" in lowered:
        return True, "parsed from non-JSON response"
    return False, f"could not parse judge response: {raw[:120]!r}"


def judge_correctness(llm, query: str, answer: str, source_text: str) -> tuple[bool, str]:
    payload = (
        f"Question:\n{query}\n\n"
        f"Source document:\n{source_text}\n\n"
        f"Assistant answer:\n{answer}"
    )
    return _ask(llm, CORRECTNESS_RUBRIC, payload)


def judge_groundedness(llm, query: str, answer: str) -> tuple[bool, str]:
    payload = f"Question:\n{query}\n\nAssistant answer:\n{answer}"
    return _ask(llm, GROUNDEDNESS_RUBRIC, payload)
