"""Fixtures shared by the two benchmark test modules.

Split out when `tests/test_benchmarks.py` passed the 500-line review limit and
its measurement half moved to `tests/test_benchmark_measure.py`. All three of
these are used by both files, and a second copy of a fixture is how two test
modules end up describing different graphs while both pass — the same reason
`tests/mcp_support.py` exists.
"""

from __future__ import annotations

import json

from benchmarks import measure as bench


def fake_stats(**overrides) -> dict:
    """A `shape()` result, in one place.

    Six tests built this inline, so adding a key to `shape()` broke all six at
    once — which is how a helper earns its place. `test_the_fake_stats_match_shape`
    below fails if the two ever describe different dicts, so the drift that
    caused this cannot happen quietly again.
    """
    stats = {"nodes": 1, "edges": 1,
             "by_label": {"Submission": 1},
             "by_edge": {"CLASSIFIED_AS": 1},
             "product_codes": 1,
             "blank_definitions": 0,
             "class_three": 1,
             "class_three_regulated": 1}
    stats.update(overrides)
    return stats


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


def fake_constraint(**overrides) -> dict:
    """One `constraint_indexes()` result, built in ONE place.

    Five render tests wrote this dict out by hand. Adding `established_here`
    to the probe left all five passing a shape the run never produces, and the
    renderer read a key none of them supplied — a `KeyError` reachable only in
    production, in the branch that decides which of two opposite headline
    findings the page prints.

    `tests/test_benchmark_probes.py` asserts these keys against what the real
    probe returns, so this cannot drift away from it silently.
    """
    base = {"label": "P",
            "established_here": True,
            "indexed_by_constraint": False,
            "entry": None,
            "correlation": {"constraints": 0, "also_indexed": 0},
            "note": None}
    unknown = set(overrides) - set(base)
    if unknown:
        raise AssertionError(
            f"{sorted(unknown)} is not part of a constraint result; a test "
            f"setting a key the probe does not return is testing a shape "
            f"nothing produces")
    return {**base, **overrides}
