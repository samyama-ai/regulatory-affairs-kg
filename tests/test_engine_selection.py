"""Which engine a writing test is allowed to use.

Its own module because the subject is not any one suite — it is the rule every
suite that writes has to obey, and the rule was absent from three of the four
files that write. `tests/test_engine_limits.py`, `tests/test_schema_cypher.py`
and `tests/test_load_openfda.py` all read `SAMYAMA_URL` with a
`localhost:8080` default, so a plain `pytest` on a machine with any engine on
that port ran their engine-backed halves against it.

The damage is quiet by construction. Every one of those suites tears down what
it wrote, so the node count comes back to where it started and the run reports
green — while `DATASET-CARD.md` known issue 10 records that a `DETACH DELETE`
on this engine does more than remove what it names.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from _pytest.outcomes import Failed, Skipped


def refuses(what: str, *, because: str) -> str:
    """Call the guard and require it to FAIL — never skip, never return.

    `pytest.raises(Failed)` is not enough anywhere in this file. `Skipped` is
    a BaseException too, it is not `Failed`, and one raised inside a
    `pytest.raises(Failed)` block propagates and SKIPS THE TEST rather than
    failing it. A guard that starts skipping where it should fail therefore
    turns every assertion about it into a green skip — which is this file's
    own subject, one level up.

    That happened twice while writing this file. The helper exists so it
    cannot happen a third time.
    """
    from tests import mcp_support

    try:
        got = mcp_support.writable_test_engine(what)
    except Failed as exc:
        assert because in str(exc), (
            f"refused, but not for the stated reason: {str(exc)[:140]}")
        return str(exc)
    except Skipped as exc:
        pytest.fail(f"the guard SKIPPED where it must fail ({because}): {exc}")
    pytest.fail(f"the guard returned {got!r} where it must fail ({because})")


def test_a_writing_test_cannot_find_an_engine_without_being_told(monkeypatch):
    """The hazard this guard exists for, driven rather than described.

    `tests/test_engine_limits.py` and `tests/test_schema_cypher.py` read
    `SAMYAMA_URL` with a `localhost:8080` default, and both WRITE — `CREATE`,
    `MERGE`, `DETACH DELETE`, and schema constraints that cannot be dropped on
    this engine. So a plain `pytest` on a machine with any engine on 8080
    attached to it and wrote into somebody else's graph, with nothing in the
    output naming the engine, because nothing had to be typed to pick one.

    Three rules, each asserted, because each was absent somewhere:
    """
    from tests import mcp_support

    monkeypatch.setattr(mcp_support, "engine_available", lambda url: True)

    # 1. `SAMYAMA_URL` is not consulted. It is the variable a developer has
    #    set for the demo, which is exactly why it must not select a target.
    monkeypatch.delenv("SAMYAMA_TEST_URL", raising=False)
    monkeypatch.setenv("SAMYAMA_URL", "http://localhost:8080")
    monkeypatch.delenv("SAMYAMA_REQUIRE_ENGINE", raising=False)
    with pytest.raises(Skipped):
        mcp_support.writable_test_engine("a writing suite")

    # 2. Port 8080 is refused even when it IS typed — a demo engine is the one
    #    a developer most likely has running.
    #    Refused whether or not anything is LISTENING there, which is the
    #    part that was broken: the port check ran after the reachability
    #    check, so with the demo engine stopped, naming 8080 skipped rather
    #    than failed — and this test stubbed `engine_available` to True for
    #    its whole body, so it could not see that.
    monkeypatch.setenv("SAMYAMA_TEST_URL", "http://localhost:8080")
    for reachable in (True, False):
        monkeypatch.setattr(mcp_support, "engine_available",
                            lambda url, r=reachable: r)
        message = refuses("a writing suite", because="8080")
        assert "WRITES" in message, (
            f"the refusal does not say why, with reachable={reachable}")
    monkeypatch.setattr(mcp_support, "engine_available", lambda url: True)

    # 3. And the skip becomes a failure when CI demands the engine ran, since
    #    a guard that skips is indistinguishable from one that passes.
    #
    #    NOT `pytest.raises(Failed)`. A `Skipped` raised inside that block
    #    propagates and skips THIS test rather than failing it — so the check
    #    for "does not skip" disappeared into a skip, which is the same defect
    #    one level up. Caught by mutation: making the guard skip here left the
    #    suite green with one more skip and nothing to say why.
    monkeypatch.delenv("SAMYAMA_TEST_URL", raising=False)
    monkeypatch.setenv("SAMYAMA_REQUIRE_ENGINE", "1")
    refuses("a writing suite", because="SAMYAMA_REQUIRE_ENGINE=1")

    # A port that is not 8080 is accepted, so the guard refuses the hazard
    # rather than refusing everything.
    monkeypatch.setenv("SAMYAMA_TEST_URL", "http://localhost:8299")
    assert mcp_support.writable_test_engine("a writing suite") == "http://localhost:8299"


def test_no_test_module_reaches_for_an_engine_by_default():
    """The defect was a module-level constant with a fallback, and the sweep
    matters more than the two files that had it: the next writing module added
    here is the one that will repeat it.

    Read off the source of every test module, because the constant is the
    thing being banned and it is visible without importing anything.
    """
    offenders = []
    here = pathlib.Path(__file__).resolve().parent
    # A REGEX over every way of READING the variable, and `rglob`, because
    # this check is for the module nobody has written yet rather than the
    # three fixed here. It matched only `environ.get("SAMYAMA_URL"` — double
    # quotes, `.get`, and `tests/*.py` non-recursively — so
    # `os.environ['SAMYAMA_URL']`, `os.getenv("SAMYAMA_URL")` and anything in
    # a subdirectory walked past a guard advertising a blanket ban.
    #
    # This file is excluded by resolved path, which is what actually keeps it
    # from matching itself; the old `chr(34)` assembly was a second mechanism
    # for the same thing, and its comment claimed to be the one that worked.
    reads = re.compile(
        r"""(?:environ\s*\[|environ\.get\(|getenv\()\s*["']SAMYAMA_URL["']""")
    for path in sorted(here.rglob("*.py")):
        if path.resolve() == pathlib.Path(__file__).resolve():
            continue
        code = "\n".join(line.split("#")[0]
                         for line in path.read_text(encoding="utf-8").splitlines())
        if reads.search(code):
            offenders.append(str(path.relative_to(here)))
    assert not offenders, (
        f"{offenders} select an engine from SAMYAMA_URL. That is the variable "
        f"pointing at a demo or a loaded graph; a test that writes must read "
        f"SAMYAMA_TEST_URL, which has no default, so it cannot find an engine "
        f"by accident.")
