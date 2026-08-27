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
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# The loader's literal encoder, not a second one. `etl/cypher.py` is pure text
# functions with no engine dependency, so this costs nothing and keeps one
# measured implementation of "what this engine accepts inside a literal".
from etl.cypher import SANITISED, lit

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
        request = urllib.request.Request(
            url + "/api/query",
            data=json.dumps({"query": cypher}).encode(),
            headers={"Content-Type": "application/json"},
        )
    except Unbounded as exc:
        return Result(error=str(exc))

    try:
        # `with`: an unclosed response holds its socket until the garbage
        # collector gets to it, and an MCP server is long-lived.
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
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

    # A 200 carrying something that is not a JSON object: a proxy login page, a
    # truncated body, a bare JSON list. All three raised out of this module —
    # `json.loads` on the first two, `payload.get` on the third — and every
    # tool funnels through here, so it must return a Result for anything.
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return Result(error=f"the engine did not return JSON ({exc}): "
                            f"{body[:120]!r}")
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


# Guards the read-call-delete sequence in `quoted()` against a second call
# arriving on another thread. See the comment there.
_SANITISED_LOCK = threading.Lock()


def quoted(value) -> str:
    """A Cypher literal, via the encoder the loader already uses.

    **This used to escape with backslashes, which this engine does not have.**
    **Stated as measured behaviour, never as a version number.** `1.1.0` is
    the image TAG; the engine behind it reports **1.7.0** on `/api/status`, so
    every "1.1.0 cannot do X" in this repo named a version nobody was running
    and could not visibly go stale. `tests/test_engine_limits.py
    ::test_a_backslash_is_not_an_escape_character` asks the running engine
    instead, in both directions — what the parser accepts inside a literal and
    what a stored value matches back against — so the day a build starts
    decoding escapes the suite fails rather than `quoted()` silently sending a
    different term than the one asked for.

    `\\'` is a parse error on this engine, not an escaped quote — measured, and
    recorded in DATASET-CARD.md known issue 6 and in `etl/cypher.lit`. So
    `regulations_for_product("O'Brien")` did not inline safely, it produced a
    statement the engine rejects, surfaced to the agent as "query rejected".
    Fail-closed, but not what the code claimed. And `\\\\` did not double a
    backslash; it inserted two literal ones, silently changing the value
    matched.

    `etl.cypher.lit` chooses the quote style per value instead, which is the
    only thing that works against an engine with no escape sequences. It is a
    pure text function with no engine dependency, so importing it costs
    nothing — and two divergent literal encoders in one repo is how the second
    one drifts, which is exactly what happened here.

    **Everything reaching this function is forced to `str` first.** `lit()`
    emits numbers unquoted, correctly — but every value these tools compare
    against is stored as text, `device_class` included (measured: "1", "2",
    "3", "N", "U" and "f"). An int reaching `lit()` would render `2` and match
    nothing, silently.

    **And a value `lit()` had to ALTER is refused rather than sent.** This is
    the one place the loader's encoder and a query encoder must differ. When a
    value holds both quote characters, the engine can express neither, so `lit()`
    substitutes a typographic quote and records it in `SANITISED`. Writing a
    value that way is a recorded, reported compromise. MATCHING on one is not:
    the term compared against is no longer the term asked for, so the query
    returns no rows and no error, and an agent reads that as "no such device
    exists".

    That is the third meaning this module exists to keep out of an empty list —
    its own docstring names two. So `quoted()` watches `SANITISED` across the
    call and raises when it grew; every tool already turns `Unbounded` into
    `Result(error=…)`, and this takes the same route.
    """
    # Cleared, not just measured. `SANITISED` is a module-level list that
    # `etl.cypher` appends to and never trims; the loader calls `reset()` at
    # the start of a run, and an MCP server is a long-lived process that never
    # does. Every entry it accumulates here is one this function is about to
    # refuse and report, so nothing is lost by dropping it — and a list that
    # only grows in a server that only runs is a leak whose contents nobody
    # reads.
    #
    # Under a lock: this reads a length, calls `lit()`, then deletes by that
    # length. An MCP server can serve two calls at once against one imported
    # `etl.cypher`, so without it two `quoted()` calls interleave and one
    # reports the other's reason as its own.
    # `None` is refused; an int or float is still coerced, and the difference
    # is deliberate.
    #
    # `None` used to map to `""`, so `regulations_for_product(None)` searched
    # for the empty string and returned `found: False, error: None` — the third
    # meaning in an empty answer this module exists to keep out, arriving
    # through a different door. `regulation_for_clearance` had a type guard;
    # the other four did not, and this covers all five in one place.
    #
    # But refusing every non-`str` would break something correct: every value
    # these tools compare against is stored as TEXT, `device_class` included
    # ("1", "2", "3", "N", "U", "f" — measured), so `device_class=2` MUST
    # render as `"2"` rather than being rejected. A number has an unambiguous
    # text meaning; `None` and a list do not.
    # Control characters are refused HERE, because `lit()` collapses them to a
    # space and records nothing in `SANITISED`. That is a hole in this
    # function's whole invariant — "refuse anything `lit()` altered" — since
    # the one alteration it cannot see is the one that silently changes the
    # term being matched. Measured: `lit("a\nb")` returns `"a b"` with
    # `SANITISED` untouched, so `regulations_for_product("a\nb")` searched for
    # something the caller never asked for and returned an empty answer with
    # `error: None`.
    #
    # Refusing before `lit()` runs is what closes it.
    #
    # C1 (U+0080–U+009F) is refused HERE and collapsed NOWHERE. Both this
    # comment and `etl/cypher.py`'s claimed the loader collapses "the C0/C1
    # ranges"; measured, neither does — `lit("a\x85b")` returns the C1 byte
    # untouched. It round-trips through this engine unaltered (measured
    # 2026-08-27), so this is prevention rather than correction: U+0085 is a
    # line terminator to some parsers, every C1 is invisible in any UI a
    # caller reads the answer in, and a term nobody can see is a term nobody
    # can check. `etl/cypher.py` is left as it is — changing what a 54,000
    # statement load writes is not this module's call — so the two encoders
    # deliberately differ here, and both now say so.
    if isinstance(value, str):
        bad = [ch for ch in value
               if ch < " " or "\x7f" <= ch <= "\x9f"
               or ch in ("\u2028", "\u2029")]
        if bad:
            raise Unbounded(
                f"the search term contains {len(bad)} control character(s), "
                f"which cannot be sent as part of a Cypher string on this "
                f"engine. They would be silently replaced with spaces and the "
                f"term matched would not be the term asked for.")

    if isinstance(value, str) and not value.strip():
        raise Unbounded(
            "a search term cannot be empty. An empty string matches nothing "
            "and returns the same empty answer as a term that genuinely has "
            "no rows — the third meaning this module exists to keep out.")
    # `True` is not a search term, and `str(True)` is `"True"`. Refused
    # BEFORE the number branch, because `isinstance(True, int)` is true — the
    # same reason `bounded()` checks it first. Every value these tools compare
    # against is text like "1", "2", "N"; `"True"` matches none of them and
    # returns the empty answer this module exists to keep out.
    if isinstance(value, bool):
        raise Unbounded(
            f"a search term must be text or a number, got {value!r}. It would "
            f"be sent as the word \"{value}\", which matches nothing and "
            f"returns the same empty answer as a term that has no rows.")
    # `2.0` rendered `"2.0"`, and `device_class` is stored as `"2"` — so a
    # limit-free, error-free, silently empty answer for an argument that was
    # right. JSON has no integer type distinct from float, so a caller sending
    # 2 can arrive here as 2.0 through no fault of its own; an integral float
    # is narrowed rather than refused. A fractional one is refused, because
    # there is no text it could have meant.
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
    text = str(value)
    if len(text) > MAX_TERM:
        raise Unbounded(
            f"the search term is {len(text):,} characters; the limit is "
            f"{MAX_TERM}. A term that long is an accidental paste rather than "
            f"a device identifier, and it would be sent as one statement.")

    with _SANITISED_LOCK:
        before = len(SANITISED)
        literal = lit(text)
        grew = len(SANITISED) > before
        reason = SANITISED[-1].get("reason", "could not be expressed") if grew else None
        del SANITISED[before:]
    if grew:
        raise Unbounded(
            f"the search term {value!r} {reason}, so it cannot be matched "
            f"exactly. "
            f"This engine has no escape sequence inside a string "
            f"literal, so a value holding both quote characters cannot be "
            f"written at all. Returning an error rather than a query that "
            f"would find nothing and look like an empty answer.")
    return literal
