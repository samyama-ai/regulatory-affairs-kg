"""Cypher literals and script splitting for Samyama-Graph 1.1.0.

Extracted from the loader because these are pure text functions with no engine
dependency, tested on their own, and the loader was over the reviewable file
size with them inline.

Two engine facts make this module load-bearing rather than cosmetic, both
measured:

  * `/api/query` accepts only `query` and `graph` — **no parameters**. Every
    value is interpolated into statement text.
  * **There are no escape sequences inside string literals.** A literal is
    delimited by `"` or `'` and that delimiter cannot appear inside it.

Everything below follows from those two.
"""

from __future__ import annotations

import re

# Values altered to be representable. A module global because `lit()` is called
# from everywhere and threading a collector through every call site would be
# worse; `reset()` exists so a process loading twice does not report the first
# run's total as the second's.
SANITISED: list[dict] = []

#: How many alterations happened, by reason — exact however large the load.
#: `SANITISED` holds examples and is capped; this is the figure a report
#: quotes, and it cannot be read off a truncated list.
SANITISED_TOTAL: dict[str, int] = {}

#: Examples kept in memory. The load report prints five.
SANITISED_CAP = 50


def reset() -> None:
    SANITISED.clear()
    SANITISED_TOTAL.clear()


class Unrepresentable(Exception):
    """A value this engine's literal syntax cannot express exactly.

    Carries the reason and the value, because both callers report them: the
    loader records the substitution it made, and the query side refuses and
    names the term it would not send.
    """

    def __init__(self, reason: str, original: str):
        super().__init__(reason)
        self.reason = reason
        self.original = original


def _literal(value, altered) -> str:
    """The literal, with every alteration routed through `altered`.

    ONE implementation, two policies. `altered(reason, original,
    substitute)` is called wherever this cannot express a value exactly and
    returns what to use — so `lit()` records and continues while
    `lit_exact()` raises, and neither re-derives what "exactly" means.
    """
    if value is None:
        return "null"
    # Numbers and booleans are emitted unquoted. Measured 2026-08-17: the engine
    # accepts `42` and `true` and preserves the type, and `WHERE n.v > 40`
    # against a numeric property works — while the same comparison against the
    # string "42" is a 400. Quoting everything did not merely lose type, it made
    # numeric filtering impossible. bool is checked first because it subclasses
    # int in Python.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        # NaN and the infinities have no Cypher literal. `repr()` gives `nan`,
        # `inf`, `-inf`, which are bare identifiers the parser rejects — a 400
        # partway through a 54,000-statement load, from a value that looked
        # ordinary. Recorded and written as null instead, because losing one
        # value loudly beats failing the run at row 12,000.
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return altered("no Cypher literal for non-finite float",
                           repr(value), "null")
        return repr(value)
    if isinstance(value, dict):
        # A dict has no place as a property value, and `str()` on one produces
        # a Python repr — {'a': 1} — which is not data. Raising beats storing
        # it: this can only be a caller mistake, and the first row shows it
        # rather than all 19,127.
        raise TypeError(
            f"dict passed to lit(): {str(value)[:80]}. Pick the fields you want "
            f"rather than storing the block — see how `openfda` is unpacked in "
            f"load_clearances."
        )
    if isinstance(value, (list, tuple)):
        # openFDA harmonised fields arrive as arrays. str() on one yields a
        # Python repr — ['a', 'b'] — which is not data. Join instead, and keep
        # the ordering the API gave.
        value = "; ".join(str(v) for v in value)
    # Every control character, not just the three common ones. NUL, vertical
    # tab and form feed reach the parser otherwise, and the engine's response
    # to those is not something to discover during a 54,000-statement load.
    # U+2028 and U+2029 as well, and DEL: they are line separators, invisible
    # in an editor, and the parser treats them as it treats a newline.
    #
    # NOT C1 (U+0080–U+009F). This said "the C0/C1 ranges" and collapsed
    # neither — measured, `lit("a\x85b")` returns the byte untouched. The
    # range is left alone deliberately rather than silently: C1 round-trips
    # through the engine unaltered (measured 2026-08-27), and widening what a
    # 54,000-statement load rewrites is a change to stored data, not to a
    # comment. `mcp_server.engine.quoted` refuses C1 on the QUERY side, where
    # refusing costs nothing, and says so.
    raw = str(value)
    text = "".join(
        " " if ch < " " or ch in ("\x7f", "\u2028", "\u2029") else ch
        for ch in raw
    )
    if text != raw:
        # Reported, and it never was. `lit()` collapsed these and recorded
        # nothing, so the one alteration a "refuse anything lit() altered"
        # invariant could not see was the one that silently changed the term
        # being matched.
        text = altered("contains a control character", raw[:200], text)

    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"

    return altered("contains both quote types", text[:200],
                   '"' + text.replace('"', "”") + '"')


def lit(value) -> str:
    """A Cypher string literal for Samyama-Graph 1.1.0.

    Two engine facts make this harder than it looks, both measured:

    1. `/api/query` accepts only `query` and `graph` — **no parameters**. Every
       value is interpolated into statement text, so this function is
       load-bearing, not cosmetic.

    2. **The engine has no escape sequences inside string literals.** `\\"` and
       `\\'` are parse errors, and `\\n` / `\\t` / `\\\\` pass through as literal
       backslash-n, backslash-t, backslash-backslash rather than being decoded.
       A literal is delimited by `"` or `'` and that delimiter simply cannot
       appear inside it.

    So the quote style is chosen per value rather than fixed. Double quotes by
    default — apostrophes are far commoner in this corpus than double quotes
    (377 values against 26). Single quotes when the value contains a double
    quote. When it contains **both**, the value cannot be represented at all,
    and the inner double quotes are replaced with typographic ones. That is a
    real alteration of source data, so every instance is recorded and reported
    rather than done silently. In this slice it happens twice, out of the
    456,154 values a full load passes through here — measured 2026-08-13 by
    counting the calls, not estimated.

    Newlines and tabs are collapsed to spaces — not for escaping, but because a
    literal newline inside a statement breaks the parser and the engine offers
    no way to encode one.

    Alterations are RECORDED and the substitute used, because a load that
    stopped at row 12,000 over one unrepresentable value is worse than one
    that reports two of them. `lit_exact` is this function with the
    opposite policy.
    """
    def record(reason, original, substitute):
        # COUNTED always, STORED up to a cap.
        #
        # Control-character collapses are recorded now and were not before, so
        # this list can grow with the data rather than with the handful of
        # both-quote values it used to hold — 456,154 values pass through here
        # in a full load, and a source that carries a stray tab in every row
        # would put an entry in memory for each of them, then write them all
        # into the load report.
        #
        # The COUNT is what the report quotes and stays exact; the report
        # prints five examples, so keeping a few more than that loses nothing
        # a reader sees. `SANITISED_TOTAL` is per reason, because "how many"
        # and "of what kind" are the two questions and one number cannot
        # answer both.
        SANITISED_TOTAL[reason] = SANITISED_TOTAL.get(reason, 0) + 1
        if len(SANITISED) < SANITISED_CAP:
            SANITISED.append({"original": original, "reason": reason})
        return substitute

    return _literal(value, record)


def lit_exact(value) -> str:
    """A literal that is EXACTLY the value, or `Unrepresentable`.

    The query side's policy. Writing an altered value is a recorded,
    reported compromise; MATCHING on one is not — the term compared against
    is no longer the term asked for, so the query returns no rows and no
    error, and a caller reads that as "no such record".

    Touches no global and takes no lock, which is the point. Establishing
    "was this altered?" by reading `len(SANITISED)`, calling `lit()`, then
    deleting back to the length just read is one module trimming another
    module's list — and the lock around it only ever covered callers
    arriving through the first one, while `props` and `merge` append
    without it.
    """
    def refuse(reason, original, substitute):
        raise Unrepresentable(reason, original)

    return _literal(value, refuse)


def split_statements(text: str) -> list[str]:
    """Cypher statements from a script, respecting string literals.

    A `//` inside a quoted value starts no comment and a `;` inside one ends no
    statement. Walking the text once with a quote flag is enough — the engine
    has no escape sequences, so a delimiter inside a literal is impossible and
    the next matching quote always closes it.

    `/* ... */` is handled too. Measured 2026-08-17: 1.1.0 parses block comments,
    including ones containing a `;` — so splitting naively on that semicolon
    would cut a statement in half. Backtick-quoted identifiers are deliberately
    not handled: the same probe showed the engine rejects them with a 400, so
    there is nothing to protect.
    """
    out: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in "\"'":
            quote = char
            current.append(char)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            current.append(" ")
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end == -1 else end + 2
            current.append(" ")
            continue
        elif char == ";":
            out.append(" ".join("".join(current).split()))
            current = []
        else:
            current.append(char)
        i += 1
    tail = " ".join("".join(current).split())
    if tail:
        out.append(tail)
    return [s for s in out if s]


IDENTIFIER = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


def identifier(name: str, what: str) -> str:
    """A label, property name or variable, checked rather than trusted.

    These are interpolated unquoted — they cannot be quoted, the engine rejects
    backticks — so a name carrying a space or a brace changes the statement's
    shape rather than its data. Every caller today passes a hardcoded string,
    which is exactly the kind of "safe because everyone is careful" that stops
    being true when someone maps a source field straight through.
    """
    if not IDENTIFIER.match(name):
        raise ValueError(f"{what} {name!r} is not a plain identifier")
    return name


def props(pairs: dict, var: str) -> str:
    """`var.key = <literal>` assignments, skipping empties.

    Returns "" when nothing survives — callers must check, because
    `ON CREATE SET ` with an empty tail is a parse error.

    **A property cannot be cleared on Samyama-Graph 1.1.0, so a re-load cannot
    remove a value the source has dropped.** `SET x = null` is accepted, reports
    success, and does nothing — measured 2026-08-17 in all three forms:
    `MATCH ... SET ... RETURN`, `MATCH ... SET` followed by a separate read, and
    `MERGE ... ON MATCH SET`. Each left the previous value in place. Setting an
    empty string behaves the same way.

    So the policy here is **last populated value wins**, and it is a limitation
    rather than a choice: a field the FDA later clears keeps its old value in
    this graph. `tests/test_engine_limits.py` pins the engine behaviour, and
    will fail if a future version starts honouring it — at which point the
    ON MATCH branch should start writing nulls.
    """
    identifier(var, "variable")
    return ", ".join(
        f"{var}.{identifier(key, 'property')} = {lit(value)}"
        for key, value in pairs.items()
        if value not in (None, "", [], {})
    )


def merge(label: str, key: str, key_value, attributes: dict, var: str = "n") -> str:
    """MERGE on the key, then set the rest — guarding the empty-SET case.

    Both branches get the same assignments. Writing `null` on the ON MATCH
    branch to clear an emptied field was tried and reverted: the engine accepts
    `SET x = null`, reports success, and leaves the value in place. See `props`
    — "loading twice changes nothing" therefore means nothing was duplicated,
    not that anything was refreshed.
    """
    identifier(label, "label")
    identifier(key, "key")
    identifier(var, "variable")
    assignments = props(attributes, var)
    statement = f"MERGE ({var}:{label} {{{key}: {lit(key_value)}}})"
    if assignments:
        statement += f" ON CREATE SET {assignments} ON MATCH SET {assignments}"
    return statement
