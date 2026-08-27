"""Talking to the engine, and expressing a value it will accept.

Split out of `mcp_server/queries.py` when that file passed the 500-line review
limit — review skips a file over it and blocks the PR, so an oversized module
is an unread one.

Split by SUBJECT, on the seam the file already had: everything here is about
the TRANSPORT and the LITERAL — where the graph is, how a statement is sent,
what comes back, and how a Python value becomes Cypher text an engine with no
escape sequences can parse. None of it knows what a Regulation is.

`queries.py` keeps the eight traversals and imports these. Neither half has an
MCP dependency, which is the property that makes the queries testable without
`fastmcp` installed.
"""


from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# The loader's literal encoder, not a second one. `etl/cypher.py` is pure text
# functions with no engine dependency, so this costs nothing and keeps one
# measured implementation of "what this engine accepts inside a literal".
from etl.cypher import Unrepresentable, lit_exact
from etl.transport import post_query

DEFAULT_URL = "http://127.0.0.1:8080"

# The largest LIMIT this module will send. Above every label count in the graph
# — Submission is the biggest at 19,127 — so it never truncates a real answer.
CEILING = 50_000


# A search term longer than this is refused. An agent picks these, so an
# accidental paste is likelier than an attack — a 100k-character term produced
# a 100KB statement the engine accepted. Refusing keeps the failure legible.
MAX_TERM = 512

ALLOWED_SCHEMES = ("http", "https")


def engine_url() -> str:
    """Where the graph is. Environment first, then config.yaml, then the default.

    Read at call time rather than at import, so a server started before the
    engine moved does not keep talking to the old address.
    """
    if os.environ.get("SAMYAMA_URL"):
        return checked_scheme(os.environ["SAMYAMA_URL"].rstrip("/"))
    config = Path(__file__).resolve().parent / "config.yaml"
    if config.exists():
        host = port = None
        # Deliberately naive — this is two keys out of a small file we own, and
        # a YAML dependency for that would be the larger cost. But it tracks
        # WHICH BLOCK it is in: matching bare `host:`/`port:` anywhere meant a
        # `server.host` added later would silently repoint the MCP server at
        # something that is not the graph.
        section = None
        for line in config.read_text().splitlines():
            if line.strip() and not line.startswith((" ", "\t")):
                section = line.strip().rstrip(":")
                continue
            if section != "graph":
                continue
            key, _, value = line.strip().partition(":")
            # An inline `# comment` and surrounding quotes are both ordinary
            # YAML. Unstripped, `host: 127.0.0.1  # local` became a hostname
            # ending in "# local" and `port: "8080"` a port with quotes in it —
            # a URL the engine never answers, and no error saying why.
            value = value.split("#")[0].strip().strip("\"'")
            if key == "host":
                host = value
            elif key == "port":
                port = value
        if host and port:
            return f"http://{host}:{port}"
    return DEFAULT_URL


@dataclass
class Result:
    """Rows, or the reason there are none.

    An agent asking "which clearances are affected by this rule" must be able to
    tell an empty answer from a broken one. Returning a bare list makes those
    identical, and the wrong one of them is a fact about the world.
    """

    rows: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def run(cypher: str) -> Result:
    # `engine_url()` is INSIDE the try. It validates the scheme now, so it can
    # raise `Unbounded` — and built outside, that escaped every tool as an
    # exception rather than arriving as `Result(error=...)`. Exactly the
    # pattern `quoted()` was moved inside a try for two rounds ago: a function
    # that gained the ability to refuse, still called where nothing catches it.
    try:
        # Read ONCE and reused below. `engine_url()` was called again inside
        # the `URLError` handler, which is a second network-config read in the
        # one path that must not fail — and it can itself raise `Unbounded`,
        # which nothing catches there, so a malformed config turned "no engine"
        # into a traceback out of the error reporter.
        url = engine_url()
    except Unbounded as exc:
        return Result(error=str(exc))

    try:
        # The request comes from `etl.transport.post_query`; every `except`
        # below is this module's own policy. A tool answering an agent must
        # never raise — it returns a `Result` for anything, including the
        # failures the loader retries and the benchmark runner exits on.
        #
        # 60 seconds, not the loader's 120: a caller is waiting for this one.
        payload, _ = post_query(cypher=cypher, url=url, timeout=60)
    except urllib.error.HTTPError as exc:
        # HTTPError subclasses URLError and must be caught first, or a 500 from
        # a running engine is reported as "no engine" — the one wrong diagnosis
        # that sends someone to restart a container that is already up.
        #
        # Read defensively: `.read()` on a broken error stream raises, and
        # `.decode()` with no `errors=` raises on a non-UTF-8 byte — either
        # would put a traceback in the one path that must not fail.
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001 - reporting the status still beats raising
            detail = "<the error body could not be read>"
        return Result(error=f"engine returned {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        return Result(error=f"no engine at {url}: {exc.reason}")
    except (TimeoutError, OSError) as exc:
        return Result(error=f"engine unreachable: {exc}")
    # A 200 carrying something that is not JSON: a proxy login page, a
    # truncated body. `post_query` decodes, so this arrives as an exception
    # rather than as a value — and the body is still named, because
    # `JSONDecodeError.doc` carries the text it failed on and
    # `UnicodeDecodeError.object` the bytes. A message that says only "not
    # JSON" sends someone to the wrong place; one that shows a login page
    # ends the search.
    except json.JSONDecodeError as exc:
        return Result(error=f"the engine did not return JSON ({exc}): "
                            f"{exc.doc[:120]!r}")
    except UnicodeDecodeError as exc:
        return Result(error=f"the engine did not return JSON ({exc}): "
                            f"{exc.object[:120]!r}")

    if not isinstance(payload, dict):
        return Result(error=f"the engine returned {type(payload).__name__}, "
                            f"not a JSON object: {str(payload)[:120]}")

    if "error" in payload:
        # `payload['error'][:200]` assumes a string. A dict or a list slices to
        # something unreadable, and an int raises TypeError inside the error
        # path — the one place that must not fail.
        return Result(error=f"query rejected: {str(payload['error'])[:200]}")
    columns = payload.get("columns") or []
    records = payload.get("records") or []
    if not isinstance(columns, list) or not isinstance(records, list):
        return Result(error=f"the engine returned columns/records this module "
                            f"cannot read: {type(columns).__name__}/"
                            f"{type(records).__name__}")
    # `strict=True`: a record shorter than `columns` used to yield a row with
    # the trailing fields simply absent and `error: None`, so an agent got a
    # row missing a key with nothing saying so. A string `columns` silently
    # produced one key per character.
    try:
        rows = [dict(zip(columns, row, strict=True)) for row in records]
    except (ValueError, TypeError) as exc:
        return Result(error=f"the engine returned a row that does not match its "
                            f"own columns ({exc})")
    return Result(rows=rows)


def bounded(limit) -> int:
    """A LIMIT the engine will accept, or a reason it will not.

    `int(limit)` turned "ten" into a ValueError escaping to the caller, and
    `-5` into `LIMIT -5`, which the engine rejects with a message about the
    whole statement. An agent gets `Result(error=…)` for everything else in
    this module; a bad argument should not be the one case that raises.

    **A ceiling as well as a floor.** The floor was here from the start and the
    ceiling was not, so `limit=10**9` rendered `LIMIT 1000000000` — a real
    request against a real engine, from a tool an agent calls unprompted and
    can pass any number to. CEILING is above every count this graph holds
    (19,127 Submissions is the largest label), so it cannot truncate a genuine
    answer; it only refuses a number nobody meant.
    """
    # `bool` before `int` (`isinstance(True, int)` is true, and `int(True)` is
    # 1, so `limit=True` rendered `LIMIT 1`), and a non-integral float refused
    # rather than truncated (`int(2.9)` is 2 — a silent narrowing in a module
    # whose argument is that an agent is never handed an unaccountable number).
    if isinstance(limit, bool) or (
            isinstance(limit, float) and not limit.is_integer()):
        raise Unbounded(f"limit must be a whole number, got {limit!r}")
    try:
        value = int(limit)
    except (TypeError, ValueError):
        raise Unbounded(f"limit must be a whole number, got {limit!r}") from None
    if value < 1:
        raise Unbounded(f"limit must be at least 1, got {value}")
    if value > CEILING:
        raise Unbounded(
            f"limit must be at most {CEILING:,}, got {value:,}. That is larger "
            f"than any label in this graph, so it asks for rows that cannot "
            f"exist.")
    return value


def checked_scheme(url: str) -> str:
    """Refuse anything that is not HTTP(S).

    `SAMYAMA_URL=file:///tmp/fake` with a crafted `api/query` file returns
    fabricated rows to the agent with `error: None`. This is operator-set
    rather than agent-set, so it is hardening and not a live exploit — the
    realistic version is `http://` pointed at the wrong host poisoning what the
    agent reads. Either way the answer is not a graph.
    """
    scheme = urllib.parse.urlparse(url).scheme
    if scheme not in ALLOWED_SCHEMES:
        raise Unbounded(
            f"the engine URL must be http or https, got {scheme or 'no'} "
            f"scheme in {url!r}. A non-HTTP source can return anything and it "
            f"would reach the caller as a query result.")
    return url


class Unbounded(Exception):
    """A limit this module will not send."""


def quoted(value) -> str:
    """A Cypher literal that is EXACTLY the term asked for, or a refusal.

    `etl.cypher.lit_exact` chooses the quote style per value and raises on
    anything it cannot express, rather than substituting and recording. That
    distinction is the whole of this function: writing an altered value is a
    reported compromise the loader makes deliberately; MATCHING on one is not,
    because the term compared against is no longer the term asked for and the
    query comes back with no rows and no error — which an agent reads as "no
    such device".

    **This used to establish the same thing by watching a global.** It read
    `len(SANITISED)`, called `lit()`, then deleted back to the length it had
    read — one module trimming a list another module owns, under a lock that
    only covered callers arriving through this one while `etl.cypher.props`
    and `merge` appended without it. Safe only because the loader and the
    server are separate processes. The lock, the delete and the
    `reset()`-ownership question all go with it.

    It also had a hole that the watching could not close: `lit()` collapsed
    control characters to a space and recorded nothing, so the one alteration
    the invariant could not see was the one that silently changed the term.
    `lit_exact` refuses those too.

    **Everything reaching this is forced to `str` first.** `lit()` emits
    numbers unquoted, correctly — but every value these tools compare against
    is stored as text, `device_class` included (measured: "1", "2", "3", "N",
    "U" and "f"). An int reaching `lit()` would render `2` and match nothing.
    """
    # `None` and containers are refused; a number is still coerced, and the
    # difference is deliberate.
    #
    # `None` used to map to `""`, so `regulations_for_product(None)` searched
    # for the empty string and returned `found: False, error: None`.
    # `regulation_for_clearance` had a type guard; the other four did not, and
    # this covers all five in one place.
    if isinstance(value, bool):
        raise Unbounded(
            f"a search term must be text or a number, got {value!r}. It would "
            f"be sent as the word \"{value}\", which matches nothing and "
            f"returns the same empty answer as a term that has no rows.")
    if isinstance(value, float):
        if not value.is_integer():
            raise Unbounded(
                f"a search term must be text or a whole number, got {value!r}. "
                f"Every value these tools compare against is stored as text, "
                f"and no stored value has a fractional part.")
        value = int(value)
    if value is None or isinstance(value, (list, dict, tuple, set)):
        raise Unbounded(
            f"a search term must be text or a number, got "
            f"{type(value).__name__}. An empty answer for a bad argument is "
            f"indistinguishable from an empty answer for a good one.")

    # A BACKSLASH is refused, because two engine builds disagree about it and
    # `/api/status` cannot tell them apart.
    #
    #   public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0   RETURN "C:\temp" -> C:\temp
    #   samyama:1.7.0-oss-2a86307                     RETURN "C:\temp" -> C:<TAB>emp
    #
    # Both report `"version": "1.7.0"`. Refused rather than escaped, because
    # no escaping is correct on both: doubling it is right on the decoding
    # build and wrong on the preserving one. Query-side only — widening what a
    # 54,000-statement load rewrites is a change to stored data.
    if isinstance(value, str) and "\\" in value:
        raise Unbounded(
            "the search term contains a backslash, which this module will not "
            "send. Two Samyama-Graph builds both reporting version 1.7.0 "
            "disagree about whether a backslash inside a string literal is an "
            "escape: one preserves it and one decodes it, so the same term "
            "matches different things depending on which engine answers, with "
            "no error either way. No CFR section, product code or K-number "
            "contains one.")

    # C1 (U+0080–U+009F) is refused HERE and collapsed nowhere. `lit()` leaves
    # it untouched — measured — and it round-trips through the engine
    # unaltered, so this is prevention rather than correction: U+0085 is a line
    # terminator to some parsers, every C1 is invisible in any interface a
    # caller reads an answer in, and a term nobody can see is one nobody can
    # check. C0 and DEL are refused by `lit_exact` below, with everything else
    # it cannot express exactly.
    if isinstance(value, str) and any("\x7f" <= ch <= "\x9f" for ch in value):
        raise Unbounded(
            "the search term contains a control character, which cannot be "
            "sent as part of a Cypher string on this engine. It would be "
            "silently replaced and the term matched would not be the term "
            "asked for.")

    if isinstance(value, str) and not value.strip():
        raise Unbounded(
            "a search term cannot be empty. An empty string matches nothing "
            "and returns the same empty answer as a term that genuinely has "
            "no rows — the third meaning this module exists to keep out.")

    text = str(value)
    if len(text) > MAX_TERM:
        raise Unbounded(
            f"the search term is {len(text):,} characters; the limit is "
            f"{MAX_TERM}. A term that long is an accidental paste rather than "
            f"a device identifier, and it would be sent as one statement.")

    try:
        return lit_exact(text)
    except Unrepresentable as exc:
        raise Unbounded(
            f"the search term {value!r} {exc.reason}, so it cannot be matched "
            f"exactly. This engine has no escape sequence inside a string "
            f"literal, so a value holding both quote characters cannot be "
            f"written at all. Returning an error rather than a query that "
            f"would find nothing and look like an empty answer.") from None
