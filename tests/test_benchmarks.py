"""The benchmark runner, tested without an engine.

What matters here is not the timings — those are the point of running it — but
that the document it writes describes the graph it says it describes. The
instance used while writing this held two KGs at once, and reporting label
counts from it would have produced a page wrong in a way nobody could see.
"""

from __future__ import annotations


import re

import pytest

from benchmarks import queries as catalogue
from benchmarks import run_queries as bench
from tests.benchmark_support import fake_stats


def stub_report(monkeypatch, *, stats=None, measure=None, index_effect=None,
                indexed_before=None, version=None, constraint=None):
    """Patch the three things `report()` calls out to, in one place.

    Six render tests set these up by hand, and the block runs to seven lines
    each — so a change to `measure()`'s return shape meant editing six copies,
    which is the drift `fake_stats()` already exists to stop one layer up.

    Each is overridable: pass a callable to watch or to fail.
    """
    monkeypatch.setattr(bench, "shape", stats or (lambda url: fake_stats()))
    monkeypatch.setattr(bench, "measure", measure or (
        lambda q, url: {**q, "rows": [], "median_ms": 1.0,
                        "min_ms": 1.0, "max_ms": 1.0}))
    monkeypatch.setattr(bench, "index_effect",
                        index_effect or (lambda url, sizes: []))
    # `report()` reads this BEFORE the query loop, to decide whether the
    # "these timings are unindexed" paragraph is true on this run.
    monkeypatch.setattr(bench, "existing_indexes",
                        lambda url: indexed_before or set())
    # Observed from /api/status in a real run, so the stub has to supply it or
    # every render test reaches the network.
    monkeypatch.setattr(bench, "engine_version", lambda url: version or "1.7.0")
    # Re-derived every run, so a render test has to say what the answer is.
    monkeypatch.setattr(bench, "constraint_indexes", lambda url: (
        constraint if constraint is not None else
        {"label": "P", "indexed_by_constraint": False, "entry": None}))


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


def test_the_index_comparison_runs_last(monkeypatch):
    """It creates indexes. Every timing above it must be the unindexed figure,
    because that is what the shipped schema produces.

    Asserted by ORDER OF CALLS, not by where the words appear in the source.
    Reading source text meant a comment mentioning `index_effect` above the
    measurement loop would have failed it, and moving the call while leaving
    the name in a docstring would have passed.
    """
    order = []
    stub_report(
        monkeypatch,
        stats=lambda url: (order.append("shape"), fake_stats())[1],
        measure=lambda q, url: (order.append("measure"),
                                {**q, "rows": [], "median_ms": 1.0,
                                 "min_ms": 1.0, "max_ms": 1.0})[1],
        index_effect=lambda url, sizes: (order.append("index_effect"), [])[1])
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
    stub_report(monkeypatch)
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


def test_the_page_links_the_dataset_card_by_a_path_that_resolves(monkeypatch):
    """`QUERY_RESULTS.md` sits in `benchmarks/` and the card is at the repo
    root, so a bare `DATASET-CARD.md` is a link to a file that is not there.

    Checked on the RENDERED page and resolved against the real tree, because
    this document is generated — fixing the committed markdown alone would be
    overwritten by the next run.
    """
    import pathlib
    stub_report(monkeypatch)
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
    stub_report(monkeypatch)
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
                        lambda url: fake_stats())
    monkeypatch.setattr(bench, "measure",
                        lambda q, url: {**q, "rows": [], "median_ms": 1.0,
                                        "min_ms": 1.0, "max_ms": 1.0})

    def refuse(url, sizes):
        raise AssertionError("the index comparison ran during a dry run")

    stub_report(monkeypatch, index_effect=refuse)
    page = bench.report("http://x", with_index_effect=False)
    assert "Not measured on this run" in page, page[-400:]
    assert "speedup tracks label size" not in page, (
        "the page claims the speedup tracks label size with nothing measured")


def test_the_speedup_claim_is_not_made_when_nothing_was_measured(monkeypatch):
    """The claim was emitted unconditionally, so a run finding every key
    already indexed printed "the speedup tracks label size almost exactly"
    above a table of dashes."""
    stub_report(monkeypatch, index_effect=lambda url, sizes: [
        {"key": "Submission.id", "nodes": 1, "scan_ms": None,
         "indexed_ms": None, "speedup": None, "note": "already indexed"}])
    page = bench.report("http://x")
    assert "already indexed" in page
    assert "speedup tracks label size" not in page, (
        "the claim was made above a table that measured nothing")


# --------------------------------------------------------------------------
# the page may not type in what it says it measured
# --------------------------------------------------------------------------


def test_every_placeholder_in_the_commentary_is_a_figure_the_run_measures():
    """`why` is rendered through `format`, so a catalogue entry naming a figure
    `shape()` does not produce raises KeyError at render — loudly, which is
    right, but only when someone runs it against an engine.

    Checked here instead, against the same map `report()` builds, so the
    failure lands in the suite rather than mid-run.
    """
    figures = bench.figures_for(fake_stats())
    assert figures, "figures_for() built nothing — move this test"

    # Rendered, not pattern-matched. The previous version regexed `report()`
    # for `"x": stats[`, so it agreed with how the map was SPELLED rather than
    # with what it held — and it broke when the map moved into its own
    # function without a single figure changing.
    for query in catalogue.QUERIES:
        bench.rendered(query["why"], figures, query["name"])


def test_the_commentary_does_not_type_the_figures_it_quotes():
    """Two `why` fields carried `19,127 submissions` and `holds 531` as typed
    text, under a page asserting nothing on it is typed in. Both were correct
    on the day and both go stale on a refresh.

    Matched loosely — any comma-grouped number, or any bare number of four
    digits or more — because the point is not those two figures, it is that
    prose on a generated page should not carry any.
    """
    for query in catalogue.QUERIES:
        typed = re.findall(r"(?<![\d.{])\d{1,3}(?:,\d{3})+(?![\d}])", query["why"])
        typed += re.findall(r"(?<![\d.{,])\d{4,}(?![\d},])", query["why"])
        assert not typed, (
            f"{query['name']!r} types {typed} into prose the page renders; "
            f"name a measured figure with a placeholder instead")


# --------------------------------------------------------------------------
# rendering a `why`, and the two ways `str.format` refuses one
# --------------------------------------------------------------------------

def test_a_literal_brace_in_a_why_names_the_entry_rather_than_raising():
    """A CFR section in braces is an ordinary thing to write on a page about
    CFR sections, and `str.format` reads it as a positional field:
    `{870.5150}` gave `IndexError: Replacement index 870 out of range` from
    inside report generation, AFTER every query had been measured, with a
    traceback pointing at the renderer rather than at the catalogue entry.
    """
    with pytest.raises(SystemExit, match="literal brace has to be doubled"):
        bench.rendered("a rule like {870.5150} matters", {"submissions": 1},
                       "Which devices does a rule change touch?")


def test_a_why_asking_for_an_unmeasured_figure_says_what_is_available():
    """`KeyError` carried the key name and nothing else — not which entry
    asked for it, and not what it could have asked for instead."""
    with pytest.raises(SystemExit) as raised:
        bench.rendered("uses {recalls:,}", {"submissions": 1}, "A question")
    assert "A question" in str(raised.value), "the failing entry is not named"
    assert "submissions" in str(raised.value), "the available figures are not listed"


def test_a_why_that_names_a_measured_figure_is_substituted():
    """The behaviour the guards must not have broken: the whole point is that
    a figure on the page comes from the run rather than being typed."""
    assert bench.rendered("{submissions:,} submissions", {"submissions": 19127},
                          "A question") == "19,127 submissions"


# --------------------------------------------------------------------------
# the catalogue, and the run that writes to the graph
# --------------------------------------------------------------------------

def test_an_empty_catalogue_is_refused_rather_than_raising_from_max():
    """`max()` on an empty sequence raises `ValueError` with no context. A
    report of no queries would otherwise render a timings section with no
    timings — a page that looks finished."""
    with pytest.raises(SystemExit, match="catalogue is empty"):
        bench.slowest_of([])


def test_the_default_run_says_it_will_change_the_graph_before_it_does(monkeypatch, capsys, tmp_path):
    """The index comparison CREATES INDEXES, so the default invocation changes
    the engine it is pointed at — irreversibly for measurement, since the
    unindexed timings cannot be taken again on that instance.

    The report disclosed it afterwards, which is the wrong end: by then the
    indexes exist. It is announced on stderr first now, and `--help` is not
    the place an operator reads it.
    """
    monkeypatch.setattr(bench, "report", lambda url, with_index_effect: "report body")
    monkeypatch.setattr(bench, "OUT", tmp_path / "results.md")
    bench.main(["--url", "http://x"])
    warned = capsys.readouterr().err
    assert "CREATES INDEXES" in warned, (
        "the default run mutates the graph and said nothing before doing it")


def test_print_is_a_dry_run_and_says_nothing_about_indexes(monkeypatch, capsys):
    """`--print` reads as a dry run, so it must not warn about a mutation it
    is not going to make — a warning that cries wolf is one nobody reads."""
    seen = {}

    def fake_report(url, with_index_effect):
        seen["with_index_effect"] = with_index_effect
        return "report body"
    monkeypatch.setattr(bench, "report", fake_report)
    bench.main(["--url", "http://x", "--print"])
    out = capsys.readouterr()
    assert seen["with_index_effect"] is False, "--print asked for the mutating measurement"
    assert "CREATES INDEXES" not in out.err
    assert "report body" in out.out, "--print did not write the report to stdout"


def test_the_blank_definition_figures_come_from_the_graph(monkeypatch):
    """The page says "Every figure on this page is written by
    `python -m benchmarks.run_queries`. Nothing is typed in." — and then
    carried `4,487 of 7,085` as Python string literals, attributed to the
    provenance query, which groups nodes by source and says nothing about
    definitions.

    Correct on the day it was written and stale on the next refresh, under a
    sentence promising it was measured this run. That is the failure this whole
    suite exists to prevent, in the artifact it exists to produce.

    Asserted against the RENDERED page with two different sets of stats, not by
    reading the source for `{stats['blank_definitions']}`. A source scan agreed
    with how the interpolation was SPELLED; rendering twice proves the number
    tracks the run, which is the actual claim.
    """
    def page_with(**figures):
        stub_report(monkeypatch, stats=lambda url: fake_stats(**figures))
        return bench.report("http://x")

    first = page_with(blank_definitions=4487, product_codes=7085)
    assert "4,487" in first and "7,085" in first, (
        "the blank-definition figures are not on the page")

    second = page_with(blank_definitions=11, product_codes=22)
    assert "11" in second and "22" in second
    assert "4,487" not in second, (
        "the figure did not move with the run — it is typed into the page, "
        "which is the exact thing the page promises it is not")


def test_the_unindexed_claim_is_not_made_on_a_run_where_it_is_false(monkeypatch):
    """The paragraph printed unconditionally, including on the run where it is
    false — which is the COMMON one.

    Q4 and Q6 are point lookups on `product_code` and `s.id`, the keys the
    first run indexes. So a second run against the same instance produces
    timings an order of magnitude faster under a sentence saying they are
    unindexed, and the default invocation is what creates those indexes.
    """
    stub_report(monkeypatch)
    fresh = bench.report("http://x")
    assert "is the **unindexed** figure" in fresh, (
        "a genuinely unindexed run no longer says so")

    stub_report(monkeypatch, indexed_before={("ProductCode", "product_code")})
    second = bench.report("http://x")
    assert "is the **unindexed** figure" not in second, (
        "the page claimed unindexed timings on a run that started with an "
        "index already in place")
    assert "not all unindexed" in second
    assert "`ProductCode.product_code`" in second, (
        "the page does not say WHICH key was already indexed, so a reader "
        "cannot tell which timings to distrust")


def test_the_dry_run_also_gets_the_correct_claim(monkeypatch):
    """`--print` runs no comparison at all, and still printed the unindexed
    claim. The gate is read before the query loop precisely so this path is
    covered too."""
    stub_report(monkeypatch, indexed_before={("Submission", "id")})
    page = bench.report("http://x", with_index_effect=False)
    assert "is the **unindexed** figure" not in page
    assert "not all unindexed" in page


def test_the_constraint_finding_is_reported_from_this_run_not_asserted(monkeypatch):
    """The page's headline was a claim in prose that was never re-checked, and
    it stopped being true.

    Measured on the engine these containers actually run: a uniqueness
    constraint DOES produce a BTREE entry, and a point lookup goes 44.5 ms to
    2.1 ms on it — with a subsequent `CREATE INDEX` adding nothing. The page
    now reports whichever answer the run finds, so it cannot go stale again.
    """
    indexed = {"label": "P", "indexed_by_constraint": True,
               "entry": {"label": "P", "property": "p", "type": "BTREE"}}
    stub_report(monkeypatch, constraint=indexed)
    page = bench.report("http://x")
    assert "On this engine it does." in page
    assert "BTREE" in page
    assert "stopped being true" in page, (
        "the page reports the new answer without recording that it contradicts "
        "what this page used to assert")

    stub_report(monkeypatch, constraint={"label": "P",
                                         "indexed_by_constraint": False,
                                         "entry": None})
    other = bench.report("http://x")
    assert "On this engine it does not." in other
    assert "scans the whole label" in other


def test_the_page_stamps_the_version_the_engine_reports(monkeypatch):
    """It was a module constant, so a run against any engine stamped the same
    string — and the finding on the page is version-scoped.

    Worse than stale: the constant said 1.1.0 because that is the IMAGE TAG,
    and every container from that tag reports engine 1.7.0. The page was
    naming the tag and reading as the engine.
    """
    stub_report(monkeypatch, version="9.9.9")
    page = bench.report("http://x")
    assert "**9.9.9**" in page, "the observed version is not on the page"
    assert "image `public.ecr.aws" in page, (
        "the image tag is gone — it is what someone reads off `docker ps`, and "
        "keeping both is what stops the two being confused again")


def test_the_dry_run_writes_nothing_at_all(monkeypatch):
    """`--print` is a dry run, and the constraint probe broke that.

    Answering "does a constraint create an index?" requires DECLARING a
    constraint, which writes. Added unconditionally, it mutated the graph on
    exactly the path this file had just been fixed to keep clean — a probe
    added to keep one promise breaking another.
    """
    ran = []
    stub_report(monkeypatch,
                index_effect=lambda url, sizes: ran.append("index") or [],
                constraint=None)
    monkeypatch.setattr(bench, "constraint_indexes",
                        lambda url: ran.append("constraint") or {
                            "label": "P", "indexed_by_constraint": True,
                            "entry": {"type": "BTREE"}})

    page = bench.report("http://x", with_index_effect=False)
    assert ran == [], (
        f"a dry run called {ran} — both of those write to the graph")
    assert "Not measured on this run" in page
    assert "probe label" in page, (
        "the page does not say WHY the constraint question went unanswered, "
        "so a reader cannot tell a dry run from an engine that said no")


def test_the_measuring_run_still_answers_the_constraint_question(monkeypatch):
    """The other half: with the flag set, the finding is reported. Gating it
    must not quietly remove the page's headline."""
    stub_report(monkeypatch, constraint={
        "label": "P", "indexed_by_constraint": True,
        "entry": {"label": "P", "property": "p", "type": "BTREE"}})
    page = bench.report("http://x", with_index_effect=True)
    assert "On this engine it does." in page
    assert "BTREE" in page
