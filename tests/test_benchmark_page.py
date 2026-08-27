"""Every claim the generated page makes about indexes and constraints.

Split out of `tests/test_benchmarks.py` when it passed the 500-line review
limit, and split by SUBJECT: each test here pins one sentence the page can
print, against a run where that sentence is FALSE. That is the whole group —
they were scattered through a file that also tests table rendering, CLI gating
and placeholder substitution, and being scattered is how two of them ended up
contradicting each other on the same page.

The section this covers has been wrong in three different ways. It asserted a
finding that had stopped being true; it printed the retraction's opposite two
paragraphs below the retraction; and it predicted what the comparison below it
would find instead of reading it. None of those were visible as a failing test,
because a page that renders is not a page that is right.
"""

from __future__ import annotations

from benchmarks import run_queries as bench
from tests.benchmark_support import fake_constraint
from tests.test_benchmarks import stub_report


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
    constraint DOES produce a BTREE entry, and a subsequent `CREATE INDEX`
    adds nothing. The page now reports whichever answer the run finds, so it
    cannot go stale again.

    No timings here. This docstring used to quote a lookup going "44.5 ms to
    2.1 ms", which was measured once in a shell and typed — those figures are
    printed by nothing, appear on no page, and are exactly what the rest of
    this file refuses to let the README carry.
    """
    indexed = fake_constraint(
        indexed_by_constraint=True,
        entry={"label": "P", "property": "p", "type": "BTREE"})
    stub_report(monkeypatch, constraint=indexed)
    page = bench.report("http://x")
    assert "On this engine it does." in page
    assert "BTREE" in page
    assert "stopped being true" in page, (
        "the page reports the new answer without recording that it contradicts "
        "what this page used to assert")

    stub_report(monkeypatch, constraint=fake_constraint(
        indexed_by_constraint=False))
    other = bench.report("http://x")
    assert "On this engine it does not." in other
    assert "scans the whole label" in other


def test_the_measuring_run_still_answers_the_constraint_question(monkeypatch):
    """The other half: with the flag set, the finding is reported. Gating it
    must not quietly remove the page's headline."""
    stub_report(monkeypatch, constraint=fake_constraint(
        indexed_by_constraint=True,
        entry={"label": "P", "property": "p", "type": "BTREE"}))
    page = bench.report("http://x", with_index_effect=True)
    assert "On this engine it does." in page
    assert "BTREE" in page


def test_the_page_cannot_say_a_constraint_indexes_and_also_that_it_does_not(monkeypatch):
    """The page contradicted itself in text a program wrote.

    The constraint section reported "**On this engine it does.**" — the
    measured, corrected finding — and eighty lines below, the index-comparison
    commentary printed the RETRACTED claim, "it does not index it either, and
    that had not been measured until now", as generated prose. Both arms fired
    on the same run, because the second was written when the first said the
    opposite and was never re-read after the correction.

    A stale figure is a number that disagrees with the code. This was the
    page's conclusion disagreeing with itself, which no reader would trust and
    no test was looking for.
    """
    stub_report(
        monkeypatch,
        constraint=fake_constraint(
            indexed_by_constraint=True,
            entry={"label": "P", "property": "p", "type": "BTREE"}),
        index_effect=lambda url, sizes: [
            {"key": "Submission.id", "nodes": 19127, "scan_ms": 152.7,
             "indexed_ms": 1.9, "speedup": 82.0, "clamped": False}],
        indexed_before=set())

    page = bench.report("http://x", with_index_effect=True)

    assert "On this engine it does." in page, "the finding is not reported"
    assert "does not index it" not in page, (
        "the page reported the retracted claim underneath the finding that "
        "retracts it")
    assert "correctly refuses" not in page, (
        "the page said the comparison had nothing to measure, directly above "
        "a table of measurements — that sentence predicted the comparison "
        "instead of reading it")
    assert "**82×**" in page, "the measured comparison is missing"


def test_the_page_does_not_answer_the_question_the_run_could_not_establish():
    """`indexed_by_constraint` is `None` on a re-used instance, and `None` is
    falsy.

    Without a branch of its own it falls through to the "it does not" arm and
    the page prints **the opposite of the measured answer**, as a headline,
    from a run that established nothing. That is a worse failure than the one
    the snapshot was added to fix: the old code over-claimed a true finding,
    this would have confidently published a false one.
    """
    rendered = "\n".join(bench.constraint_section(
        {"label": "P", "established_here": False,
         "indexed_by_constraint": None, "entry": None,
         "correlation": {"constraints": 3, "also_indexed": 3},
         "note": "…"}))

    assert "does not" not in rendered, (
        "the page answered a question this run could not establish, and "
        "answered it wrongly")
    assert "Not established on this run" in rendered
    assert "**3**" in rendered, "the read-only correlation was not reported"


def test_the_unindexed_timings_are_not_explained_by_the_retracted_finding(monkeypatch):
    """The third sentence in this section that outlived its reason.

    "Every timing above is the unindexed figure, **because that is what the
    shipped schema produces today**" was written when the page still claimed a
    constraint does not index the key. Once that was corrected the sentence
    contradicted the headline directly above it: a schema whose constraints do
    index would produce indexed lookups, not unindexed ones.

    The gate was never wrong — nothing WAS indexed when the run started. Only
    the reason was, and a reason is the part a reader takes away.
    """
    stub_report(
        monkeypatch,
        constraint=fake_constraint(
            indexed_by_constraint=True,
            entry={"label": "P", "property": "p", "type": "BTREE"}),
        index_effect=lambda url, sizes: [
            {"key": "Submission.id", "nodes": 19127, "scan_ms": 175.5,
             "indexed_ms": 1.3, "speedup": 140.0, "clamped": False}],
        indexed_before=set())

    page = bench.report("http://x", with_index_effect=True)

    assert "**unindexed** figure" in page, "the caveat itself is gone"
    assert "shipped schema produces" not in page, (
        "the page explains unindexed timings by the finding it retracts two "
        "paragraphs above")
    assert "property of the instance" in page, (
        "the caveat no longer says what it is a property of, which is the "
        "whole reason a reader needs it")
