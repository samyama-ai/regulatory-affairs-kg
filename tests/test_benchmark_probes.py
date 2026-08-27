"""The two probes that talk to an engine, driven against a fake transport.

Split from `tests/test_benchmarks.py`, which tests what the RUNNER RENDERS and
stubs both of these out to do it. That is the right call there — a render test
should not depend on a probe — but it left the probes themselves with no test
at all, and a green suite hid it: replacing the whole body of
`constraint_indexes()` with a hardcoded `True`, and inverting the speedup to
`indexed / scan`, both passed twenty-five tests.

So these drive the real functions and fake only `urlopen`, one layer below.
Each answer the fake gives is keyed to the statement it is answering, so a
probe that stops asking a question fails rather than being handed the answer
anyway.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from benchmarks import measure


def transport(monkeypatch, answers, sent=None):
    """Answer `urlopen` from `answers`, keyed by a substring of the statement.

    The statements are recorded in `sent`, because half of what these probes
    must get right is WHAT THEY ASK and in WHAT ORDER — a probe that creates
    before it looks reports its own leftovers as this run's finding.
    """
    class R:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(self._payload).encode()

    def urlopen(request, *a, **k):
        cypher = json.loads(request.data)["query"]
        if sent is not None:
            sent.append(cypher)
        for needle, payload in answers.items():
            if needle in cypher:
                value = payload(cypher) if callable(payload) else payload
                return R(value)
        raise AssertionError(f"the probe asked something unfaked: {cypher!r}")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def listing(rows):
    return {"columns": ["label", "property", "type"], "records": rows}


PROBE = measure.PROBE_LABEL


def test_the_constraint_probe_reports_the_index_that_appeared(monkeypatch):
    """A fresh instance: nothing indexed, nothing constrained, and the index
    shows up only after the constraint is created.

    This is the one case where the causal answer is available, and it is the
    answer the whole page turns on.
    """
    state = {"created": False}
    sent = []
    transport(monkeypatch, {
        "SHOW CONSTRAINTS": lambda _: listing(
            [[PROBE, "probe_id", "UNIQUE"]] if state["created"] else []),
        "SHOW INDEXES": lambda _: listing(
            [[PROBE, "probe_id", "BTREE"]] if state["created"] else []),
        "CREATE CONSTRAINT": lambda _: state.update(created=True) or
        {"columns": [], "records": []},
    }, sent)

    got = measure.constraint_indexes("http://engine")

    assert got["established_here"] is True, got
    assert got["indexed_by_constraint"] is True, (
        "the index appeared only after the constraint was created and the "
        "probe did not report it — this is the page's headline finding")
    assert got["entry"] == {"label": PROBE, "property": "probe_id",
                            "type": "BTREE"}
    assert sent.index("SHOW INDEXES") < next(
        i for i, c in enumerate(sent) if "CREATE CONSTRAINT" in c), (
        "the probe created the constraint before reading the index list, so "
        "it cannot tell an index it made from one that was already there")


def test_the_constraint_probe_refuses_on_an_instance_it_has_already_probed(monkeypatch):
    """The defect this rewrite closes.

    The probe leaves its constraint behind — this engine cannot DROP one — so
    on any instance it had run against before, `SHOW INDEXES` showed its own
    index from last time. It reported the finding established, having created
    nothing and established nothing, and re-running on a re-used container is
    the normal case rather than the odd one.
    """
    sent = []
    transport(monkeypatch, {
        "SHOW CONSTRAINTS": listing([[PROBE, "probe_id", "UNIQUE"],
                                     ["Submission", "id", "UNIQUE"]]),
        "SHOW INDEXES": listing([[PROBE, "probe_id", "BTREE"],
                                 ["Submission", "id", "BTREE"]]),
    }, sent)

    got = measure.constraint_indexes("http://engine")

    assert got["established_here"] is False, got
    assert got["indexed_by_constraint"] is None, (
        "a leftover constraint from an earlier run was reported as this run's "
        "finding — the claim is that the constraint BUILT the index, and this "
        "run built nothing")
    assert not any("CREATE" in statement for statement in sent), (
        f"the probe wrote to an instance it had already probed: {sent}")
    assert got["correlation"] == {"constraints": 2, "also_indexed": 2}
    assert got["note"] and "fresh instance" in got["note"]


def test_the_constraint_probe_does_not_claim_an_index_it_did_not_make(monkeypatch):
    """An index already on the probe key, with no constraint declaring it.

    Someone else's `CREATE INDEX`, or a build that indexes on first write. The
    constraint is created and no index appears BECAUSE of it, and reporting
    `True` here is the false positive that a snapshot exists to prevent.
    """
    transport(monkeypatch, {
        "SHOW CONSTRAINTS": listing([]),
        "SHOW INDEXES": listing([[PROBE, "probe_id", "BTREE"]]),
        "CREATE CONSTRAINT": {"columns": [], "records": []},
    })

    got = measure.constraint_indexes("http://engine")

    assert got["established_here"] is True
    assert got["indexed_by_constraint"] is False, (
        "the index was there before the constraint was created, so the "
        "constraint did not build it — but the probe read the list AFTER "
        "creating and reported it as though it had")


def test_a_renamed_constraint_column_stops_the_run_rather_than_reading_none(monkeypatch):
    """`existing_indexes` refuses on renamed columns and `SHOW CONSTRAINTS`
    must too: every row would read as `(None, None)`, the sets would hold one
    meaningless pair, and a constrained key would report as unconstrained.
    """
    transport(monkeypatch, {
        "SHOW CONSTRAINTS": {"columns": ["name", "prop", "type"],
                             "records": [["Submission", "id", "UNIQUE"]]},
        "SHOW INDEXES": listing([]),
    })
    with pytest.raises(SystemExit) as refused:
        measure.constraint_indexes("http://engine")
    assert "SHOW CONSTRAINTS" in str(refused.value)


def test_the_speedup_is_the_scan_divided_by_the_indexed_time(monkeypatch):
    """Inverting this to `indexed / scan` passed the whole suite.

    Every render test stubs `index_effect` out, so nothing executed the
    arithmetic — and the page prints the result as `124×`, a number a reader
    takes as "this many times faster". Inverted it would print `0×` for a real
    speedup, which reads as no effect rather than as a bug.

    Driven by making the indexed lookup measurably faster than the scan and
    asserting the direction, not the value: a fake cannot fix a timing, but it
    can make one call slower than another.
    """
    times = iter([])

    def fake_run(cypher, url):
        # Each timing call returns the next elapsed figure. Warm-up repeats
        # included, so the sequence is consumed at whatever rate `timed()`
        # asks for it.
        return {"columns": [], "records": []}, next(times)

    scan, indexed = 160.0, 1.3
    calls = {"created": False}

    def elapsed():
        while True:
            yield indexed if calls["created"] else scan

    times = elapsed()

    def run(cypher, url):
        if cypher.startswith("CREATE INDEX"):
            calls["created"] = True
            return {"columns": [], "records": []}, 0.0
        return fake_run(cypher, url)

    monkeypatch.setattr(measure, "run", run)
    monkeypatch.setattr(measure, "existing_indexes", lambda url: set())

    rows = measure.index_effect("http://engine", {"Submission": 19127})
    measured = [row for row in rows if row["speedup"] is not None]
    assert measured, "no key was measured, so nothing was asserted"

    for row in measured:
        assert row["scan_ms"] > row["indexed_ms"], (
            "the fake made the indexed lookup the faster one and the probe "
            "recorded it the other way round")
        assert row["speedup"] > 1, (
            f"{row['key']} scanned in {row['scan_ms']}ms and looked up in "
            f"{row['indexed_ms']}ms, and the speedup came out as "
            f"{row['speedup']:.3f}. Scan divided by indexed is the number the "
            f"page prints as `124×`; inverted it prints `0×`, which reads as "
            f"an index that did nothing.")
        assert row["speedup"] == pytest.approx(
            row["scan_ms"] / row["indexed_ms"], rel=1e-6)


def test_the_render_stub_matches_what_the_probe_actually_returns(monkeypatch):
    """The stub in `tests/test_benchmarks.py` is a hand-written copy of this
    function's return shape, and a hand-written copy drifts.

    It drifted once already: adding `established_here` left every render test
    passing a dict without it, so the renderer read a key the real probe
    always supplies and the tests would have missed a `KeyError` in
    production. Asserting the keys match is cheap; noticing later is not.
    """
    from tests import test_benchmarks as renders

    transport(monkeypatch, {
        "SHOW CONSTRAINTS": listing([]),
        "SHOW INDEXES": listing([]),
        "CREATE CONSTRAINT": {"columns": [], "records": []},
    })
    real = measure.constraint_indexes("http://engine")

    captured = {}
    monkeypatch.setattr(renders.bench, "constraint_indexes",
                        lambda url: captured, raising=False)
    with pytest.MonkeyPatch.context() as mp:
        renders.stub_report(mp)
        stub = renders.bench.constraint_indexes("http://engine")

    assert set(stub) == set(real), (
        f"the render stub supplies {sorted(set(stub))} and the probe returns "
        f"{sorted(set(real))}; a render test is exercising a shape the run "
        f"never produces")
