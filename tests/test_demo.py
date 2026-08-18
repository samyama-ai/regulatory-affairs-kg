"""The demo's two pieces of logic, tested against stubbed responses.

A presentation script does not usually earn tests. These two do:

  * the preflight guard is what stands between a corrupted graph and an
    audience watching empty tables, and it exists because running the test
    suite against a loaded engine silently breaks MERGE-key lookups
    (DATASET-CARD.md, known issue 10);
  * `table()` pads rows, and a short row used to raise IndexError mid-demo.

Both are pure functions of a response dict, so neither needs an engine.
"""

import sys

import pytest

from demo import demo


def response(columns, records):
    return {"columns": columns, "records": records}


# --------------------------------------------------------------------------
# table()
# --------------------------------------------------------------------------

def test_a_short_row_does_not_raise(capsys):
    """A row with fewer cells than the header used to be an IndexError, which
    on a demo means the script dies with an audience watching."""
    demo.table(["a", "b", "c"], [["1", "2", "3"], ["4"]])
    out = capsys.readouterr().out
    assert "4" in out and "a" in out


def test_columns_are_width_aligned_to_the_widest_value(capsys):
    demo.table(["code"], [["short"], ["a-much-longer-value"]])
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    widths = {len(l.rstrip()) for l in lines}
    assert len(lines) >= 3, lines            # header, rule, two rows
    assert max(widths) >= len("a-much-longer-value")


def test_no_rows_prints_a_header_rather_than_crashing(capsys):
    demo.table(["code"], [])
    assert "code" in capsys.readouterr().out


# --------------------------------------------------------------------------
# the preflight
# --------------------------------------------------------------------------

def test_preflight_refuses_when_the_key_lookup_returns_nothing(monkeypatch, capsys):
    """The failure this guards: node count intact, MERGE-key equality broken.
    Reproduced by running pytest against a loaded engine — see known issue 10.
    """
    def answers(cypher):
        if "count(n)" in cypher and ":" not in cypher.split("count(n)")[0][-14:]:
            return response(["c"], [[28496]]), 1.0
        if "count(r)" in cypher and "870.5150" in cypher:
            return response(["found"], [[0]]), 1.0        # the broken lookup
        return response(["c"], [[25310]]), 1.0

    monkeypatch.setattr(demo, "query", answers)
    with pytest.raises(SystemExit) as exc:
        demo.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "cannot answer its own questions" in out


def test_the_failure_message_reports_the_graph_it_actually_found(monkeypatch, capsys):
    """It used to say "28,496 nodes are present" regardless. On any other load
    that reports a number the graph does not have — in a script whose docstring
    says nothing is hard-coded."""
    def answers(cypher):
        if "870.5150" in cypher:
            return response(["found"], [[0]]), 1.0
        return response(["c"], [[56992]]), 1.0

    monkeypatch.setattr(demo, "query", answers)
    with pytest.raises(SystemExit):
        demo.main()
    out = capsys.readouterr().out
    assert "56,992 nodes are present" in out
    assert "28,496" not in out


def test_an_empty_graph_says_so_rather_than_blaming_the_key_lookup(monkeypatch, capsys):
    monkeypatch.setattr(demo, "query", lambda c: (response(["c"], [[0]]), 1.0))
    with pytest.raises(SystemExit) as exc:
        demo.main()
    assert exc.value.code == 1
    assert "graph is empty" in capsys.readouterr().out


# --------------------------------------------------------------------------
# pause()
# --------------------------------------------------------------------------

def test_pause_returns_immediately_without_a_terminal(monkeypatch):
    """`python -m demo.demo < /dev/null`, a CI check or a piped run has no
    stdin. input() then raised EOFError at step 1 and the demo died."""
    class NotATty:
        def isatty(self): return False
    monkeypatch.setattr(sys, "stdin", NotATty())
    demo.pause()          # must not raise, must not block


def test_pause_survives_eof_even_if_a_terminal_is_claimed(monkeypatch):
    class LyingTty:
        def isatty(self): return True
    monkeypatch.setattr(sys, "stdin", LyingTty())
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError))
    demo.pause()          # must not raise
