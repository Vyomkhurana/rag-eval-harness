from eval_cases import EVAL_CASES

REQUIRED_KEYS = {"id", "dimension", "query", "description", "expected_docs", "check"}
DIMENSIONS = {"Correctness", "Groundedness", "Robustness", "Safety"}


def test_cases_have_required_keys():
    for case in EVAL_CASES:
        assert REQUIRED_KEYS <= set(case), case.get("id")


def test_case_ids_are_unique():
    ids = [c["id"] for c in EVAL_CASES]
    assert len(ids) == len(set(ids))


def test_dimensions_are_valid():
    for case in EVAL_CASES:
        assert case["dimension"] in DIMENSIONS


def test_checks_are_callable():
    for case in EVAL_CASES:
        assert callable(case["check"])


def test_every_dimension_covered():
    covered = {c["dimension"] for c in EVAL_CASES}
    assert covered == DIMENSIONS
