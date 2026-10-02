"""search_knowledge_base prints each passage's scores under their own names.

The retrieval (fusion) score does not follow the order once the reranker has
run, so it is not shown as the bare "score" of a ranked list.
"""

from backend.tools.rag_tools import _render


def _result(name, score=None, rerank=None):
    result = {"text": f"Text of {name}.", "metadata": {"source_filename": name}}
    if score is not None:
        result["score"] = score
    if rerank is not None:
        result["rerank_score"] = rerank
    return result


def test_reranked_results_show_both_scores_by_name():
    out = _render("q", [_result("a.md", 0.75, 0.3072), _result("b.md", 0.0, 0.0081)], {})

    assert "[1] a.md (rerank 0.307 · retrieval 0.750)" in out
    assert "[2] b.md (rerank 0.008 · retrieval 0.000)" in out
    assert "(score " not in out


def test_without_a_rerank_score_the_line_is_unchanged():
    out = _render("q", [_result("a.md", 0.75)], {})

    assert "[1] a.md (score 0.750)" in out


def test_a_result_with_no_scores_prints_none():
    out = _render("q", [_result("a.md")], {})

    assert "[1] a.md\n" in out


def test_the_order_of_results_is_not_touched():
    results = [_result("low.md", 0.9, 0.1), _result("high.md", 0.1, 0.9)]

    out = _render("q", results, {})

    assert out.index("low.md") < out.index("high.md")
