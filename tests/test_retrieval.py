from eval_cases import EVAL_CASES
from rag_bot import KnowledgeBase


def test_knowledge_base_loads_all_articles():
    kb = KnowledgeBase()
    assert len(kb.documents) == 16
    assert "account_lockout.txt" in kb.filenames


def test_retrieve_returns_ranked_docs():
    kb = KnowledgeBase()
    docs, confident = kb.retrieve("how do I reset my password?")
    assert len(docs) == 3
    assert docs[0].score >= docs[1].score >= docs[2].score
    assert confident
    assert docs[0].filename == "password_reset.txt"


def test_out_of_scope_query_has_no_confident_match():
    kb = KnowledgeBase()
    _, confident = kb.retrieve("what is the 401k employer matching percentage?")
    assert not confident


def test_retrieval_recall_on_cases_with_expected_docs():
    kb = KnowledgeBase()
    checked = 0
    hits = 0
    for case in EVAL_CASES:
        expected = case.get("expected_docs")
        if not expected:
            continue
        checked += 1
        docs, _ = kb.retrieve(case["query"])
        names = {d.filename for d in docs}
        if any(e in names for e in expected):
            hits += 1
    # Retrieval is TF-IDF, so allow a couple of misses on the deliberately
    # garbled robustness queries, but most cases should land their article.
    assert hits >= checked - 2
