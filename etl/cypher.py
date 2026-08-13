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

SANITISED: list[str] = []   # values we had to alter to make representable


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

    SANITISED.append(text[:120])
    return '"' + text.replace('"', "”") + '"'


def split_statements(text: str) -> list[str]:
    """Cypher statements from a script, respecting string literals.

    A `//` inside a quoted value starts no comment and a `;` inside one ends no
    statement. Walking the text once with a quote flag is enough — the engine
    has no escape sequences, so a delimiter inside a literal is impossible and
    the next matching quote always closes it.
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
    """
    return ", ".join(
        f"{var}.{key} = {lit(value)}"
        for key, value in pairs.items()
        if value not in (None, "", [], {})
    )


def merge(label: str, key: str, key_value, attributes: dict, var: str = "n") -> str:
    """MERGE on the key, then set the rest — guarding the empty-SET case."""
    assignments = props(attributes, var)
    statement = f"MERGE ({var}:{label} {{{key}: {lit(key_value)}}})"
    if assignments:
        statement += f" ON CREATE SET {assignments} ON MATCH SET {assignments}"
    return statement
