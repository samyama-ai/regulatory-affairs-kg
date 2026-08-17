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

# Values altered to be representable. A module global because `lit()` is called
# from everywhere and threading a collector through every call site would be
# worse; `reset()` exists so a process loading twice does not report the first
# run's total as the second's.
SANITISED: list[dict] = []


def reset() -> None:
    SANITISED.clear()


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
        return repr(value)
    if isinstance(value, (list, tuple)):
        # openFDA harmonised fields arrive as arrays. str() on one yields a
        # Python repr — ['a', 'b'] — which is not data. Join instead, and keep
        # the ordering the API gave.
        value = "; ".join(str(v) for v in value)
    # Every control character, not just the three common ones. NUL, vertical
    # tab and form feed reach the parser otherwise, and the engine's response
    # to those is not something to discover during a 54,000-statement load.
    text = "".join(" " if ch < " " or ch == "\x7f" else ch for ch in str(value))

    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"

    SANITISED.append({"original": text[:200], "reason": "contains both quote types"})
    return '"' + text.replace('"', "”") + '"'


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
    return ", ".join(
        f"{var}.{key} = {lit(value)}"
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
    assignments = props(attributes, var)
    statement = f"MERGE ({var}:{label} {{{key}: {lit(key_value)}}})"
    if assignments:
        statement += f" ON CREATE SET {assignments} ON MATCH SET {assignments}"
    return statement
