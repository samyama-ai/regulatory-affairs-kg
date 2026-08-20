"""The benchmark runner, tested without an engine.

What matters here is not the timings — those are the point of running it — but
that the document it writes describes the graph it says it describes. The
instance used while writing this held two KGs at once, and reporting label
counts from it would have produced a page wrong in a way nobody could see.
"""

from __future__ import annotations

import json

import pytest

from benchmarks import run_queries as bench


def serve(monkeypatch, answers: dict):
    """Answer each query by substring match on the cypher."""
    class R:
        def __init__(self, payload): self._p = payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(self._p).encode()

    def urlopen(request, *a, **k):
        cypher = json.loads(request.data)["query"]
        for needle, payload in answers.items():
            if needle in cypher:
                return R(payload)
        return R({"columns": ["c"], "records": [[0]]})
    monkeypatch.setattr(bench.urllib.request, "urlopen", urlopen)


def counts(total, submissions=0, product_codes=0, regulations=0, edges=0):
    return {
        "(n:Submission)": {"columns": ["c"], "records": [[submissions]]},
        "(n:ProductCode)": {"columns": ["c"], "records": [[product_codes]]},
        "(n:Regulation)": {"columns": ["c"], "records": [[regulations]]},
        "MATCH (n) RETURN count(n)": {"columns": ["c"], "records": [[total]]},
        "MATCH ()-[r]->() RETURN": {"columns": ["c"], "records": [[edges]]},
    }


def test_a_co_mingled_engine_is_refused(monkeypatch):
    """The engine used while writing this held the regulatory graph and a
    drug-interactions graph — 273,279 nodes between them. Label counts from
    that describe neither, and nothing in the output would have shown it."""
    serve(monkeypatch, counts(total=273_279, submissions=19_127,
                              product_codes=7_085, regulations=2_284))
    with pytest.raises(SystemExit, match="Another KG is loaded"):
        bench.shape("http://x")


def test_a_clean_graph_is_accepted(monkeypatch):
    serve(monkeypatch, counts(total=28_496, submissions=19_127,
                              product_codes=7_085, regulations=2_284, edges=25_310))
    got = bench.shape("http://x")
    assert got["nodes"] == 28_496
    assert got["by_label"]["Submission"] == 19_127


def test_an_empty_engine_is_refused(monkeypatch):
    """A snapshot that failed to import would otherwise produce a page of
    zeroes that reads as a finding."""
    serve(monkeypatch, counts(total=5, submissions=0, product_codes=0, regulations=0))
    with pytest.raises(SystemExit, match="Another KG is loaded"):
        bench.shape("http://x")


def test_a_rejected_query_stops_the_run(monkeypatch):
    """A parse error must not become an empty results table."""
    serve(monkeypatch, {"MATCH": {"error": "Parse error: unexpected token"}})
    with pytest.raises(SystemExit, match="rejected"):
        bench.run("MATCH (n) RETURN n", "http://x")


def test_a_500_is_not_reported_as_a_missing_engine(monkeypatch):
    import io
    import urllib.error
    monkeypatch.setattr(bench.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b"x"))))
    with pytest.raises(SystemExit, match="500"):
        bench.run("MATCH (n) RETURN n", "http://x")


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------

def test_every_query_carries_its_question_and_its_reason():
    """A benchmark that lists cypher without saying what it answers is a
    performance table, not evidence the graph is useful."""
    for query in bench.QUERIES:
        assert query["question"].endswith("?"), query["name"]
        assert len(query["why"]) > 40, query["name"]
        assert query["cypher"].strip().startswith("MATCH"), query["name"]


def test_the_timings_are_a_median_not_a_single_reading():
    assert bench.REPEATS >= 3


def test_the_index_comparison_runs_last(monkeypatch):
    """It creates indexes. Every timing above it must be the unindexed figure,
    because that is what the shipped schema produces."""
    import inspect
    source = inspect.getsource(bench.report)
    assert source.index("index_effect(") > source.index("measure(q, url)")


def test_the_page_makes_no_comparison_it_has_not_measured():
    """Nothing here has been run against another database, so no claim about
    relative speed belongs on the page."""
    import inspect
    source = inspect.getsource(bench.report)
    assert "not** a comparison" in source or "not a comparison" in source
    for word in ("faster than", "outperform", "beats"):
        assert word not in source.lower(), f"unmeasured comparison: {word}"
