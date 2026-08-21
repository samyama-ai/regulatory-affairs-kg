"""The benchmark runner, tested without an engine.

What matters here is not the timings — those are the point of running it — but
that the document it writes describes the graph it says it describes. The
instance used while writing this held two KGs at once, and reporting label
counts from it would have produced a page wrong in a way nobody could see.
"""

from __future__ import annotations

import json
import re

import pytest

from benchmarks import run_queries as bench


def serve(monkeypatch, answers: dict):
    """Answer each query by substring match on the cypher."""
    class R:
        def __init__(self, payload):
            self._p = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(self._p).encode()

    def urlopen(request, *a, **k):
        cypher = json.loads(request.data)["query"]
        for needle, payload in answers.items():
            if needle in cypher:
                return R(payload)
        return R({"columns": ["c"], "records": [[0]]})
    monkeypatch.setattr(bench.urllib.request, "urlopen", urlopen)


def counts(total, submissions=0, product_codes=0, regulations=0, edges=0,
           classified_as=None, governed_by=None):
    """Answers for every count `shape()` asks for.

    The per-edge-type answers default to a share of `edges` rather than to
    zero: `EXPECTED_EDGES` is enforced now, so a fixture that leaves them at
    zero is a fixture describing a graph with no edges — which `shape()`
    should and does refuse.
    """
    each = edges // 2 if edges else 0
    return {
        "(n:Submission)": {"columns": ["c"], "records": [[submissions]]},
        "(n:ProductCode)": {"columns": ["c"], "records": [[product_codes]]},
        "(n:Regulation)": {"columns": ["c"], "records": [[regulations]]},
        "MATCH (n) RETURN count(n)": {"columns": ["c"], "records": [[total]]},
        "MATCH ()-[r]->() RETURN": {"columns": ["c"], "records": [[edges]]},
        "[r:CLASSIFIED_AS]": {"columns": ["c"],
                              "records": [[classified_as if classified_as is not None else each]]},
        "[r:GOVERNED_BY]": {"columns": ["c"],
                            "records": [[governed_by if governed_by is not None else each]]},
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


def test_an_engine_holding_unlabelled_nodes_is_refused(monkeypatch):
    """What this test actually covered. Five nodes, none carrying a label this
    graph is made of, trips the co-mingling guard — the message says another KG
    is loaded, and it is right to."""
    serve(monkeypatch, counts(total=5, submissions=0, product_codes=0,
                              regulations=0, edges=2))
    with pytest.raises(SystemExit, match="Another KG is loaded"):
        bench.shape("http://x")


def test_a_genuinely_empty_engine_is_refused(monkeypatch):
    """The case the name promised and nothing covered: a snapshot that failed
    to import leaves ZERO nodes, which passes the co-mingling guard (0 == 0)
    and would produce a page of zeroes that reads as a finding.

    It is caught by the edge guard, which is why that guard had to stop being
    decorative — `EXPECTED_EDGES` was declared and never checked.
    """
    serve(monkeypatch, counts(total=0, submissions=0, product_codes=0,
                              regulations=0, edges=0))
    with pytest.raises(SystemExit, match="no CLASSIFIED_AS, GOVERNED_BY edges"):
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
    because that is what the shipped schema produces.

    Asserted by ORDER OF CALLS, not by where the words appear in the source.
    Reading source text meant a comment mentioning `index_effect` above the
    measurement loop would have failed it, and moving the call while leaving
    the name in a docstring would have passed.
    """
    order = []
    monkeypatch.setattr(bench, "shape",
                        lambda url: (order.append("shape"),
                                     {"nodes": 1, "edges": 1,
                                      "by_label": {"Submission": 1},
                                      "by_edge": {"CLASSIFIED_AS": 1}})[1])
    monkeypatch.setattr(bench, "measure",
                        lambda q, url: (order.append("measure"), {**q, "rows": [],
                                        "median_ms": 1.0, "min_ms": 1.0,
                                        "max_ms": 1.0})[1])
    monkeypatch.setattr(bench, "index_effect",
                        lambda url, sizes: (order.append("index_effect"), [])[1])
    bench.report("http://x")
    assert order[-1] == "index_effect", order
    assert "measure" in order, order


def test_the_page_makes_no_comparison_it_has_not_measured(monkeypatch):
    """Nothing here has been run against another database, so no claim about
    relative speed belongs on the page.

    Against the RENDERED page, not the source of the function that writes it.
    A source scan passes on a disclaimer that never reaches the output and
    fails on the word appearing in a comment.
    """
    monkeypatch.setattr(bench, "shape",
                        lambda url: {"nodes": 1, "edges": 1,
                                     "by_label": {"Submission": 1},
                                     "by_edge": {"CLASSIFIED_AS": 1}})
    monkeypatch.setattr(bench, "measure",
                        lambda q, url: {**q, "rows": [], "median_ms": 1.0,
                                        "min_ms": 1.0, "max_ms": 1.0})
    monkeypatch.setattr(bench, "index_effect", lambda url, sizes: [])
    page = bench.report("http://x").lower()
    assert "not a comparison" in page.replace("**", ""), \
        "the page does not say it is not a database comparison"
    for word in ("faster than", "outperform", "beats"):
        assert word not in page, f"unmeasured comparison on the page: {word}"


def test_a_pipe_in_a_value_does_not_split_the_row():
    """Device names and definitions are free text from openFDA. A `|` inside
    one adds a column and every row from there down renders wrong."""
    got = bench.table([{"name": "A|B", "n": 1}])
    body = got.splitlines()[2]
    assert "A\\|B" in body, got
    # Unescaped pipes only — those are the ones markdown treats as cell
    # boundaries. Three: leading, separator, trailing.
    unescaped = body.replace("\\|", "")
    assert unescaped.count("|") == 3, got


def test_a_column_missing_from_the_first_row_is_still_reported():
    """Columns were taken from `rows[0]` alone, so a field the first row
    happened not to carry was dropped from the table entirely — which is what
    a query with an optional field returns."""
    got = bench.table([{"a": 1}, {"a": 2, "b": 3}])
    assert "| a | b |" in got, got
    assert got.splitlines()[2].endswith("|  |"), got


def test_the_index_measurement_refuses_a_graph_it_has_already_indexed(monkeypatch):
    """It creates the indexes it measures, so a SECOND run against the same
    instance compares an indexed lookup against an indexed lookup — a scan
    time that is not a scan, and a speedup near 1 that reads as "the index did
    nothing". Refused rather than reported wrongly."""
    serve(monkeypatch, {"SHOW INDEXES": {
        "columns": ["label", "property", "type"],
        "records": [[label, prop, "BTREE"] for label, prop, _ in bench.KEYS]}})
    got = bench.index_effect("http://x", {"Submission": 19_127})
    assert got, "nothing reported at all"
    assert all(row["scan_ms"] is None for row in got), got
    assert "already indexed" in got[0]["note"], got


def test_one_stale_index_does_not_remove_the_keys_still_measurable(monkeypatch):
    """Returning only the clashing keys dropped every key that could still be
    measured, so one stale index on one label silently shortened the table and
    nothing in the output said why.

    Every key is reported: the indexed one carries its note, the rest carry
    figures.
    """
    serve(monkeypatch, {"SHOW INDEXES": {
        "columns": ["label", "property", "type"],
        "records": [["Submission", "id", "BTREE"]]}})
    got = bench.index_effect("http://x", {label: 1 for label, _, _ in bench.KEYS})
    assert len(got) == len(bench.KEYS), got
    noted = [row for row in got if row["scan_ms"] is None]
    measured = [row for row in got if row["scan_ms"] is not None]
    assert [row["key"] for row in noted] == ["Submission.id"], noted
    assert len(measured) == len(bench.KEYS) - 1, measured


def test_a_renamed_show_indexes_column_is_refused_not_ignored(monkeypatch):
    """`row.get("label")` returns None for every row if the column is renamed,
    the set holds `(None, None)`, nothing clashes — and the guard silently
    stops guarding, reporting an indexed instance as though it were fresh."""
    serve(monkeypatch, {"SHOW INDEXES": {
        "columns": ["name", "prop", "type"],
        "records": [["Submission", "id", "BTREE"]]}})
    with pytest.raises(SystemExit, match="SHOW INDEXES no longer returns"):
        bench.existing_indexes("http://x")


def test_the_page_links_the_dataset_card_by_a_path_that_resolves(monkeypatch):
    """`QUERY_RESULTS.md` sits in `benchmarks/` and the card is at the repo
    root, so a bare `DATASET-CARD.md` is a link to a file that is not there.

    Checked on the RENDERED page and resolved against the real tree, because
    this document is generated — fixing the committed markdown alone would be
    overwritten by the next run.
    """
    import pathlib
    monkeypatch.setattr(bench, "shape",
                        lambda url: {"nodes": 1, "edges": 1,
                                     "by_label": {"Submission": 1},
                                     "by_edge": {"CLASSIFIED_AS": 1}})
    monkeypatch.setattr(bench, "measure",
                        lambda q, url: {**q, "rows": [], "median_ms": 1.0,
                                        "min_ms": 1.0, "max_ms": 1.0})
    monkeypatch.setattr(bench, "index_effect", lambda url, sizes: [])
    page = bench.report("http://x")

    assert "DATASET-CARD.md" in page
    here = pathlib.Path(bench.__file__).resolve().parent
    for target in re.findall(r"\]\(([^)]+DATASET-CARD\.md)\)", page):
        assert (here / target).resolve().exists(), (
            f"the page links {target}, which does not resolve from "
            f"{here}")


def test_the_page_says_its_timings_are_round_trip(monkeypatch):
    """They include HTTP and JSON decoding. Presented bare, a reader takes
    them for engine execution time."""
    monkeypatch.setattr(bench, "shape",
                        lambda url: {"nodes": 1, "edges": 1,
                                     "by_label": {"Submission": 1},
                                     "by_edge": {"CLASSIFIED_AS": 1}})
    monkeypatch.setattr(bench, "measure",
                        lambda q, url: {**q, "rows": [], "median_ms": 1.0,
                                        "min_ms": 1.0, "max_ms": 1.0})
    monkeypatch.setattr(bench, "index_effect", lambda url, sizes: [])
    page = bench.report("http://x").lower()
    assert "round-trip" in page or "round trip" in page, \
        "the page presents timings without saying what is inside them"


def test_no_measured_figure_is_copied_into_the_benchmarks_readme():
    """A measured number copied into a second file drifts the moment the first
    is regenerated — and this one had, quoting scan times a third lower than a
    later run produced.

    `QUERY_RESULTS.md` is written by the runner and is the one source. The
    README may describe what was found; it may not restate the numbers.
    """
    import pathlib
    here = pathlib.Path(bench.__file__).resolve().parent
    readme = (here / "README.md").read_text(encoding="utf-8")

    timings = re.findall(r"\d+\.\d+\s*ms", readme)
    assert not timings, (
        f"the benchmarks README quotes measured timings {timings}; they belong "
        f"in QUERY_RESULTS.md, which the runner writes")
    speedups = re.findall(r"\*\*\d+×\*\*", readme)
    assert not speedups, (
        f"the benchmarks README quotes measured speedups {speedups}; same "
        f"reason — one source per figure")


def test_the_dry_run_renders_without_the_index_comparison(monkeypatch):
    """`--print` skips the index measurement, and the prose gate below reads
    the measured keys on every path. It was assigned only inside the branch
    that runs the comparison, so the dry run raised `UnboundLocalError` — the
    one path that is supposed to be safe to take."""
    monkeypatch.setattr(bench, "shape",
                        lambda url: {"nodes": 1, "edges": 1,
                                     "by_label": {"Submission": 1},
                                     "by_edge": {"CLASSIFIED_AS": 1}})
    monkeypatch.setattr(bench, "measure",
                        lambda q, url: {**q, "rows": [], "median_ms": 1.0,
                                        "min_ms": 1.0, "max_ms": 1.0})

    def refuse(url, sizes):
        raise AssertionError("the index comparison ran during a dry run")

    monkeypatch.setattr(bench, "index_effect", refuse)
    page = bench.report("http://x", with_index_effect=False)
    assert "Not measured on this run" in page, page[-400:]
    assert "speedup tracks label size" not in page, (
        "the page claims the speedup tracks label size with nothing measured")


def test_the_speedup_claim_is_not_made_when_nothing_was_measured(monkeypatch):
    """The claim was emitted unconditionally, so a run finding every key
    already indexed printed "the speedup tracks label size almost exactly"
    above a table of dashes."""
    monkeypatch.setattr(bench, "shape",
                        lambda url: {"nodes": 1, "edges": 1,
                                     "by_label": {"Submission": 1},
                                     "by_edge": {"CLASSIFIED_AS": 1}})
    monkeypatch.setattr(bench, "measure",
                        lambda q, url: {**q, "rows": [], "median_ms": 1.0,
                                        "min_ms": 1.0, "max_ms": 1.0})
    monkeypatch.setattr(bench, "index_effect", lambda url, sizes: [
        {"key": "Submission.id", "nodes": 1, "scan_ms": None,
         "indexed_ms": None, "speedup": None, "note": "already indexed"}])
    page = bench.report("http://x")
    assert "already indexed" in page
    assert "speedup tracks label size" not in page, (
        "the claim was made above a table that measured nothing")


def test_the_response_is_closed(monkeypatch):
    """An unclosed response holds its socket until the garbage collector gets
    to it, and this issues one request per repeat per query — nine queries at
    five repeats before the index comparison even starts.

    Watched, not read out of the source: a `with` in a comment satisfies a
    source scan, and a correct refactor that closes it another way fails one.
    """
    closed = []

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            closed.append(True)
            return False

        def read(self):
            return json.dumps({"columns": ["c"], "records": [[1]]}).encode()

    monkeypatch.setattr(bench.urllib.request, "urlopen", lambda *a, **k: R())
    bench.run("MATCH (n) RETURN count(n)", "http://x")
    assert closed, "the response was never closed"
