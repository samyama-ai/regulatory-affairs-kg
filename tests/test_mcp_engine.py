"""The transport and the literal encoder, tested without an engine.

Split out of `tests/test_mcp_queries.py` when `mcp_server/queries.py` was split
into `queries.py` and `engine.py` — and the tests followed the code rather than
staying in a file named for the half they no longer exercise.

Split by SUBJECT: everything here drives `mcp_server/engine.py` — where the
graph is, how a statement is sent, what a failure comes back as, and how a
Python value becomes Cypher text an engine with no escape sequences can parse.
Nothing here knows what a Regulation is; `test_mcp_queries.py` keeps the eight
traversals, and `test_mcp_live.py` keeps the ones needing a running engine.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from mcp_server import engine, queries
from tests.mcp_support import ROOT, serve


def test_an_unreachable_engine_is_an_error_not_an_empty_result(monkeypatch):
    """An agent cannot act on `[]` if it means both "no clearances" and "the
    engine is down"."""
    monkeypatch.setattr(engine.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.URLError("connection refused")))
    got = queries.clearances_under_regulation("870.5150")
    assert got["error"] is not None
    assert got["clearances"] == []


def test_a_500_is_not_reported_as_a_missing_engine(monkeypatch):
    """HTTPError subclasses URLError, so catching URLError first turns a 500
    from a running engine into "no engine" — which sends someone to restart a
    container that is already up."""
    import io
    monkeypatch.setattr(engine.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b"bad"))))
    error = queries.busiest_regulations()["error"]
    assert "500" in error and "no engine" not in error


def test_a_rejected_query_is_an_error_not_an_empty_result(monkeypatch):
    """The engine answers 200 with an `error` key for a parse failure."""
    serve(monkeypatch, {"error": "Parse error: unexpected token"})
    got = queries.regulations_for_product("DXY")
    assert got["error"] and "rejected" in got["error"]


def test_a_genuinely_empty_answer_has_no_error(monkeypatch):
    serve(monkeypatch, {"columns": ["cfr_section"], "records": []})
    got = queries.regulations_for_product("NOSUCH")
    assert got["error"] is None
    assert got["regulations"] == []
    assert got["count"] == 0


def test_rows_are_returned_as_named_fields(monkeypatch):
    """Columns and records arrive separately; an agent needs them joined."""
    serve(monkeypatch, {"columns": ["clearance", "device"],
                        "records": [["K233820", "A scanner"]]})
    rows = queries.clearances_under_regulation("870.5150")["clearances"]
    assert rows == [{"clearance": "K233820", "device": "A scanner"}]


def test_the_environment_wins_over_the_config(monkeypatch):
    monkeypatch.setenv("SAMYAMA_URL", "http://elsewhere:9999/")
    assert engine.engine_url() == "http://elsewhere:9999"


def test_the_config_file_is_read_when_the_environment_is_silent(monkeypatch, tmp_path):
    """Against values that DIFFER from the fallback, which is the whole point.

    This asserted `http://127.0.0.1:8080` — and `DEFAULT_URL` is
    `http://127.0.0.1:8080`, and the shipped `config.yaml` declares the same
    host and port. So it passed with the entire config-parsing branch deleted,
    a test of nothing in the code that decides where the server points. The
    docstring even claimed to have fixed that weakness; it had moved it from
    `startswith("http://")` to an equality against the default.

    Driven from a config the fallback cannot produce.
    """
    home = tmp_path / "mcp_server"
    home.mkdir()
    (home / "config.yaml").write_text("graph:\n  host: 10.1.2.3\n  port: 7777\n")
    monkeypatch.delenv("SAMYAMA_URL", raising=False)
    monkeypatch.setattr(engine, "__file__", str(home / "engine.py"))
    assert engine.engine_url() == "http://10.1.2.3:7777", (
        "the config file was not read — this is the assertion that fails when "
        "the parsing branch is removed, which the previous one did not")


def test_the_shipped_config_is_the_one_the_server_would_use():
    """Separately, that the file in the repo says what the server assumes.

    Kept apart from the test above because this one CAN agree with
    `DEFAULT_URL` — that is the shipped config's actual content — so it must
    not be the test that proves the parser runs.
    """
    text = (ROOT / "mcp_server" / "config.yaml").read_text()
    block = text.split("graph:")[1]
    assert "host: 127.0.0.1" in block and "port: 8080" in block, (
        f"mcp_server/config.yaml no longer declares the host and port the "
        f"tests above assume: {block[:120]!r}")


def test_a_host_outside_the_graph_block_does_not_repoint_the_server(monkeypatch, tmp_path):
    """The parser matched a bare `host:`/`port:` under ANY section, so it
    worked by luck — `config.yaml` happens to declare them only under `graph:`.
    Someone adding `server.host` later would have silently repointed the MCP
    server at something that is not the graph."""
    home = tmp_path / "mcp_server"
    home.mkdir()
    # `graph:` FIRST and `server:` second, deliberately. With the sections
    # reversed a last-wins parser lands on the right answer by accident, which
    # is how the first version of this test passed against the very parser it
    # was written to catch.
    #
    # The graph block also differs from DEFAULT_URL, for the same reason as the
    # test above: asserting `127.0.0.1:8080` here would have passed whether the
    # parser read the graph block, read nothing, or crashed into the fallback.
    (home / "config.yaml").write_text(
        "graph:\n  host: 10.1.2.3\n  port: 7777\n"
        "server:\n  host: 10.0.0.1\n  port: 9999\n")
    monkeypatch.delenv("SAMYAMA_URL", raising=False)
    monkeypatch.setattr(engine, "__file__", str(home / "engine.py"))
    assert engine.engine_url() == "http://10.1.2.3:7777", (
        "a host declared outside the graph block repointed the server")


def test_an_engine_error_that_is_not_a_string_is_still_reported(monkeypatch):
    """`payload['error'][:200]` assumes a string. A dict slices to something
    unreadable and an int raises TypeError — inside the error path, which is
    the one place that must not fail."""
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"error": {"code": 42, "detail": "nope"}}).encode()

    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda *a, **k: R())
    got = queries.busiest_regulations()
    assert got["error"] and "42" in got["error"], got


def test_a_config_value_with_a_comment_or_quotes_is_read_cleanly(monkeypatch, tmp_path):
    """An inline `# comment` and surrounding quotes are ordinary YAML.
    Unstripped, `host: 127.0.0.1  # local` became a hostname ending in
    "# local" — a URL the engine never answers, and no error saying why."""
    home = tmp_path / "mcp_server"
    home.mkdir()
    (home / "config.yaml").write_text(
        'graph:\n  host: "127.0.0.1"  # local docker\n  port: 8080  # default\n')
    monkeypatch.delenv("SAMYAMA_URL", raising=False)
    monkeypatch.setattr(engine, "__file__", str(home / "engine.py"))
    assert engine.engine_url() == "http://127.0.0.1:8080"


def test_a_limit_above_the_ceiling_is_refused():
    """`bounded()` had a floor and no ceiling, so `limit=10**9` rendered
    `LIMIT 1000000000` — a real request against a real engine, from a tool an
    agent calls unprompted and can pass any number to."""
    with pytest.raises(engine.Unbounded, match="at most"):
        engine.bounded(10 ** 9)
    assert engine.bounded(engine.CEILING) == engine.CEILING


def test_the_ceiling_is_above_every_label_in_the_graph():
    """A ceiling that could truncate a real answer would be worse than none.
    Submission is the largest label at 19,127."""
    assert engine.CEILING > 19_127


def test_a_fractional_or_boolean_limit_is_refused_not_quietly_rounded():
    """`int()` accepts more than it should, and both narrowings were silent.

    `int(2.9)` is 2, so an agent asking for 2.9 rows got 2 and was told
    nothing. `isinstance(True, int)` is true and `int(True)` is 1, so
    `limit=True` — a caller passing no number at all — rendered `LIMIT 1` and
    returned a single row that looked like an answer.

    Everything else in this module hands back a reason; these two did not.
    """
    for value in (2.9, 0.5, True, False):
        with pytest.raises(engine.Unbounded, match="whole number"):
            engine.bounded(value)

    # Integral floats still work — the refusal is about losing information,
    # not about the type.
    assert engine.bounded(3.0) == 3
    assert engine.bounded("10") == 10


def test_a_two_hundred_that_is_not_json_is_an_error_not_a_traceback(monkeypatch):
    """The body every tool funnels through, given something that is not JSON.

    A proxy login page or a truncated body raised `json.JSONDecodeError` out of
    the module — past every `except` a tool has, so an agent got a traceback
    instead of the `Result(error=...)` this module promises for everything.
    """
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"<!DOCTYPE html><title>Sign in</title>"
    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda *a, **k: R())

    got = engine.run("MATCH (n) RETURN n")
    assert not got.ok, "an HTML login page was read as a successful query"
    assert "did not return JSON" in got.error
    assert got.rows == []


def test_a_json_body_that_is_not_an_object_is_an_error_not_an_attribute_error(monkeypatch):
    """A bare JSON list parses fine and then breaks on `payload.get` — the
    same escape, one line further down."""
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'["not", "an", "object"]'
    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda *a, **k: R())

    got = engine.run("MATCH (n) RETURN n")
    assert not got.ok
    assert "not a JSON object" in got.error


def test_an_unreadable_error_body_still_reports_the_status(monkeypatch):
    """The error path must not have an error path of its own.

    `exc.read()` on a consumed stream raises, and `.decode()` with no
    `errors=` raises on any non-UTF-8 byte — either would replace a reportable
    engine failure with a traceback from inside the reporting.
    """
    class Exploding(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://x", 500, "boom", {}, None)

        def read(self):
            raise OSError("the error stream was already consumed")

    def raise_it(*a, **k):
        raise Exploding()
    monkeypatch.setattr(engine.urllib.request, "urlopen", raise_it)

    got = engine.run("MATCH (n) RETURN n")
    assert not got.ok
    assert "500" in got.error, "the status was lost while reporting the failure"


def test_an_error_body_that_is_not_utf8_is_still_reported(monkeypatch):
    """Same path, the other way it broke."""
    class Binary(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://x", 502, "bad gateway", {}, None)

        def read(self):
            return b"\xff\xfe not utf-8"

    def raise_it(*a, **k):
        raise Binary()
    monkeypatch.setattr(engine.urllib.request, "urlopen", raise_it)

    got = engine.run("MATCH (n) RETURN n")
    assert not got.ok
    assert "502" in got.error


def test_a_non_http_engine_url_is_refused(monkeypatch):
    """`file:///tmp/fake` with a crafted `api/query` file returns fabricated
    rows to the agent with `error: None`. Operator-set rather than agent-set,
    so hardening — but the realistic version is http:// to the wrong host."""
    monkeypatch.setenv("SAMYAMA_URL", "file:///tmp/fake")
    with pytest.raises(engine.Unbounded, match="must be http or https"):
        engine.engine_url()
    monkeypatch.setenv("SAMYAMA_URL", "http://localhost:9999")
    assert engine.engine_url() == "http://localhost:9999"


def test_a_row_that_does_not_match_its_columns_is_an_error_not_a_short_row(monkeypatch):
    """`dict(zip(...))` truncated silently: three columns against a two-element
    record produced two keys and `error: None`, so an agent got a row missing a
    field with nothing saying so."""
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"columns": ["a", "b", "c"],
                               "records": [["1", "2"]]}).encode()
    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda *a, **k: R())

    got = engine.run("MATCH (n) RETURN n")
    assert not got.ok, "a short row was returned as a successful answer"
    assert "does not match its own columns" in got.error


def test_a_bad_engine_url_reaches_the_caller_as_an_error_not_an_exception(monkeypatch):
    """`engine_url()` validates the scheme now, so it can raise — and it was
    called while BUILDING the request, outside the try. That escaped every
    tool as an exception instead of arriving as `Result(error=...)`.

    Exactly the pattern `quoted()` was moved inside a try for: a function that
    gained the ability to refuse, still called where nothing catches it.
    """
    monkeypatch.setenv("SAMYAMA_URL", "file:///tmp/fake")
    got = engine.run("MATCH (n) RETURN n")
    assert not got.ok, "a refused URL did not come back as an error"
    assert "http or https" in got.error


def test_the_engine_url_is_read_once_per_call():
    """It was read again inside the `URLError` handler.

    That is a second network-config read in the one path that must not fail —
    and `engine_url()` validates the scheme, so it can raise `Unbounded`, which
    nothing catches there. A malformed config turned "no engine" into a
    traceback out of the error reporter, in the module whose whole argument is
    that a failure arrives as a `Result`.
    """
    reads = []
    real = engine.engine_url

    def counted():
        reads.append(1)
        return real()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine, "engine_url", counted)
        mp.setattr(engine.urllib.request, "urlopen",
                   lambda *a, **k: (_ for _ in ()).throw(
                       engine.urllib.error.URLError("connection refused")))
        result = engine.run("RETURN 1")

    assert result.error and "no engine" in result.error, result
    assert "connection refused" in result.error
    assert len(reads) == 1, (
        f"the engine URL was read {len(reads)} times for one call; the second "
        f"read is in the error path, where it can raise")
