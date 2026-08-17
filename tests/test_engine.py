"""`Engine.run`'s retry behaviour, with no engine running.

A load is ~54,000 statements against a process that can blip. This retry logic
is what stops a single failure leaving a half-populated graph, and it decides
which failures are worth retrying at all — a parse error will fail identically
four times, so retrying it only delays the report.

All of it is testable by stubbing `urlopen`, and none of it was covered.
"""

import json
import urllib.error

import pytest

from etl.load_openfda import Engine


class Response:
    def __init__(self, payload=b'{"records": [[1]]}'):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.payload


def http_error(code, body=b"boom"):
    import io
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr("etl.load_openfda.time.sleep", lambda s: None)
    return Engine("http://localhost:9", "default")


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_transient_codes_are_retried_and_counted(engine, monkeypatch, code):
    attempts = []

    def flaky(*a, **k):
        attempts.append(1)
        if len(attempts) < 3:
            raise http_error(code)
        return Response()

    monkeypatch.setattr("etl.load_openfda.urllib.request.urlopen", flaky)
    assert engine.run("MATCH (n) RETURN 1")["records"] == [[1]]
    assert len(attempts) == 3
    assert engine.retries == 2, "retries were not counted"
    assert engine.statements == 1


def test_a_parse_error_is_not_retried(engine, monkeypatch):
    """A 4xx fails identically every time. Four attempts only delay the report,
    and the message must carry the statement — a bare 400 is unactionable."""
    attempts = []

    def bad(*a, **k):
        attempts.append(1)
        raise http_error(400, b"parse error near UNWIND")

    monkeypatch.setattr("etl.load_openfda.urllib.request.urlopen", bad)
    with pytest.raises(RuntimeError, match="parse error near UNWIND"):
        engine.run("UNWIND [1] AS x RETURN x")
    assert len(attempts) == 1, f"a 400 was retried {len(attempts)} times"


def test_a_network_failure_uses_every_attempt_then_reports(engine, monkeypatch):
    attempts = []

    def down(*a, **k):
        attempts.append(1)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("etl.load_openfda.urllib.request.urlopen", down)
    with pytest.raises(RuntimeError, match="unreachable"):
        engine.run("MATCH (n) RETURN 1")
    assert len(attempts) == 4, attempts


def test_an_error_inside_a_200_body_is_still_an_error(engine, monkeypatch):
    """The engine answers 200 with an `error` key rather than an HTTP status.
    Trusting the status code alone would let a failed write report success."""
    monkeypatch.setattr("etl.load_openfda.urllib.request.urlopen",
                        lambda *a, **k: Response(b'{"error": "no such label"}'))
    with pytest.raises(RuntimeError, match="no such label"):
        engine.run("MATCH (n:Nope) RETURN 1")


def test_a_body_that_is_not_json_is_retried_not_a_traceback(engine, monkeypatch):
    """A proxy returning an HTML error page. urllib wraps a failure to connect
    but not one while reading the body, so this used to escape as a traceback."""
    attempts = []

    def html(*a, **k):
        attempts.append(1)
        return Response(b"<html>502 Bad Gateway</html>")

    monkeypatch.setattr("etl.load_openfda.urllib.request.urlopen", html)
    with pytest.raises((RuntimeError, json.JSONDecodeError)):
        engine.run("MATCH (n) RETURN 1")
    assert len(attempts) == 4, "a malformed body should use the retry ladder"


def test_attempts_below_one_is_refused(engine):
    """Otherwise the loop never runs and `result` is unbound — a NameError
    reported as if the engine had failed."""
    with pytest.raises(ValueError, match="at least 1"):
        engine.run("MATCH (n) RETURN 1", attempts=0)


def test_statements_are_counted_only_when_they_succeed(engine, monkeypatch):
    monkeypatch.setattr("etl.load_openfda.urllib.request.urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(http_error(400)))
    with pytest.raises(RuntimeError):
        engine.run("bad")
    assert engine.statements == 0, "a failed statement was counted as applied"
