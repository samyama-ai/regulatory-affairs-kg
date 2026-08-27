"""Turning a Python value into Cypher text an engine will parse.

Split out of `tests/test_mcp_engine.py` when it passed the 500-line review
limit, and split on the line that file's own docstring already drew: the
transport is where the graph is and what a failure comes back as; this is what
a value becomes on the way there.

Everything here is about the same failure, which is not a crash. A value the
encoder ALTERS produces a query that runs, returns no rows, and reports no
error — so the caller reads "no such record" about a term nobody searched for.
Every refusal below exists because some character made that happen: a quote
the engine cannot express, a control character collapsed to a space, and a
backslash that two builds reporting the same version disagree about.
"""

from __future__ import annotations

import pytest

from mcp_server import engine


@pytest.mark.parametrize("value,expected", [
    ("870.5150", chr(34) + "870.5150" + chr(34)),
    ("O'Brien", chr(34) + "O'Brien" + chr(34)),
    ('say "hi"', chr(39) + 'say "hi"' + chr(39)),
])


def test_a_literal_is_quoted_by_choosing_a_delimiter_not_by_escaping(value, expected):
    """The engine has NO escape sequences inside a literal, so the delimiter
    is chosen per value rather than the quote being escaped.

    These expectations used to assert the backslash form. That is why they
    passed while the engine rejected the statement: they compared
    `quoted()`'s output against itself, and the one thing neither of them
    consulted was the engine. Measured — the old form returns HTTP 400, this
    one parses.

    `back\\slash` used to be one of these cases, asserting the backslash was
    passed through unaltered. It is refused now, and the test for that is
    below: whether passing it through is right depends on which build answers,
    which is exactly what a caller cannot know.
    """
    assert engine.quoted(value) == expected


def test_a_backslash_is_refused_because_two_builds_disagree_about_it():
    """The one term whose meaning depends on which engine answers.

        public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0   RETURN "C:\\temp" -> C:\\temp
        samyama:1.7.0-oss-2a86307                     RETURN "C:\\temp" -> C:<TAB>emp

    Both report `"version": "1.7.0"` on `/api/status`, so nothing this module
    can read tells them apart. On the second, `quoted("C:\\temp")` sends a
    literal the engine reads as `C:<TAB>emp` — the term matched is not the term
    asked for, and the answer is no rows with `error: None`, which an agent
    reads as "no such device".

    Refused rather than escaped, because no escaping is correct on both:
    doubling the backslash is right on the decoding build and wrong on the
    preserving one. Refusing is right on both and costs nothing measurable —
    0 of 26,212 loaded records carry a backslash in any field these tools
    compare against.
    """
    for term in ("C:\\temp", "50\\%", "a\\nb", "\\"):
        with pytest.raises(engine.Unbounded) as refused:
            engine.quoted(term)
        assert "backslash" in str(refused.value)
        assert "1.7.0" in str(refused.value), (
            "the refusal does not say WHY, and the reason is the whole point: "
            "a caller cannot tell the two builds apart either")

    # And it is the backslash alone that is refused, not everything near it.
    assert engine.quoted("870.5150"), "an ordinary CFR section must still send"
    assert engine.quoted("O'Brien"), "the apostrophe case must still send"


def test_an_integer_is_matched_as_the_text_it_is_stored_as():
    """`lit()` emits numbers unquoted, correctly — the engine keeps the type.
    But every value these tools compare against is stored as TEXT, including
    `device_class`, measured as "1", "2", "3" and "N" on the loaded graph. An
    int reaching the encoder would render `2`, match nothing, and say so in no
    way at all."""
    assert engine.quoted(2) == chr(34) + "2" + chr(34)
    assert engine.quoted("2") == chr(34) + "2" + chr(34)


def test_a_term_holding_both_quote_characters_is_refused_not_altered():
    """`lit()` substitutes a typographic quote when a value holds both `'` and
    `"`, because 1.1.0 can express neither. Writing a value that way is a
    recorded compromise the loader reports. MATCHING on one is not: the term
    compared against is no longer the term asked for, so the query returns no
    rows and no error.

    That is a third meaning inside the same empty list — this module's own
    docstring names two and exists to keep them apart.
    """
    with pytest.raises(engine.Unbounded, match="cannot be matched exactly"):
        engine.quoted('Governor\'s "Special" Device')


def test_a_term_with_only_one_quote_kind_still_works():
    """The fix must not refuse what 1.1.0 CAN express — an apostrophe alone is
    the common case and `lit()` handles it by choosing the other delimiter."""
    assert engine.quoted("O'Brien") == '"O\'Brien"'
    assert engine.quoted('say "hi"') == "'say \"hi\"'"


def test_refusing_a_term_does_not_grow_the_sanitised_record():
    """`etl.cypher.SANITISED` is a module-level list nothing trims, and an MCP
    server is a long-lived process — the loader calls `reset()` at the start of
    a run and the server never does.

    Every entry this path would add is one `quoted()` is about to refuse and
    report, so dropping it loses nothing. A list that only grows, in a process
    that only runs, holding records nobody reads, is a leak.
    """
    from etl.cypher import SANITISED
    before = len(SANITISED)
    for _ in range(50):
        with pytest.raises(engine.Unbounded):
            engine.quoted('a\'b"c')
    assert len(SANITISED) == before, (
        f"SANITISED grew by {len(SANITISED) - before} across 50 refused calls")


def test_a_search_term_longer_than_the_cap_is_refused(monkeypatch):
    """An agent picks these, so an accidental paste is likelier than an attack.
    A 100k-character term produced a 100KB statement the engine accepted."""
    with pytest.raises(engine.Unbounded, match="characters; the limit is"):
        engine.quoted("x" * 100_000)
    assert engine.quoted("x" * 10), "an ordinary term was refused"


def test_a_none_search_term_is_refused_but_a_number_is_still_coerced():
    """Two behaviours that look the same and are not.

    `None` mapped to `""`, so `regulations_for_product(None)` searched for the
    empty string and returned `found: False, error: None` — a bad argument
    producing an answer indistinguishable from a good one finding nothing.

    A NUMBER must keep working, and this is why refusing every non-`str` would
    have been wrong: every value these tools compare against is stored as text,
    `device_class` included, so `device_class=2` has to render as `"2"` rather
    than be rejected. A number has an unambiguous text meaning; `None` does not.
    """
    for refused in (None, ["870.5150"], {"a": 1}):
        with pytest.raises(engine.Unbounded, match="must be text or a number"):
            engine.quoted(refused)

    assert engine.quoted(2) == engine.quoted("2"), (
        "an int stopped coercing to the text the graph stores — device_class "
        "lookups now match nothing")


def test_a_control_character_is_refused_rather_than_silently_replaced():
    """The hole in `quoted()`'s own invariant.

    `quoted()` refuses anything `lit()` altered, by watching `SANITISED`. But
    `lit()` collapses control characters to a space and records NOTHING — so
    the one alteration it cannot see is the one that silently changes the term
    being matched. Measured: `lit("a\nb")` returns `"a b"` with `SANITISED`
    untouched, so the search ran for something the caller never asked for and
    came back empty with `error: None`.
    """
    for bad in ("a\nb", "a\tb", "a\x00b", "a\u2028b"):
        with pytest.raises(engine.Unbounded, match="control character"):
            engine.quoted(bad)

    # An ordinary term, and the apostrophe case, still work.
    assert engine.quoted("normal") == chr(34) + "normal" + chr(34)
    assert "O'BRIEN" in engine.quoted("O'BRIEN")


def test_an_empty_search_term_is_refused():
    """An empty string matches nothing and returns the same empty answer as a
    term that genuinely has no rows — the third meaning in an empty list this
    module exists to keep out."""
    for blank in ("", "   ", "\t "):
        with pytest.raises(engine.Unbounded, match="cannot be empty|control character"):
            engine.quoted(blank)


def test_a_c1_control_character_is_refused_like_every_other_one():
    """The comment said "same C0/C1 set the loader collapses". Neither did.

    `lit("a\\x85b")` returns the C1 byte untouched — measured — and this
    function's control-character guard stopped at C0 and DEL, so U+0080–U+009F
    passed straight through both. The claim was in two files and true in
    neither.

    It round-trips through this engine unaltered today, so this is prevention
    rather than a correction: U+0085 is a line terminator to some parsers,
    every C1 is invisible in any interface a caller reads an answer in, and a
    search term nobody can see is one nobody can check.
    """
    for char in ("\x80", "\x85", "\x9f"):
        with pytest.raises(engine.Unbounded) as refused:
            engine.quoted(f"a{char}b")
        assert "control character" in str(refused.value)

    # The boundary on both sides, so the range cannot quietly widen or narrow.
    assert engine.quoted("a\x7eb"), "~ is printable and must still be sent"
    assert engine.quoted("a\xa0b"), (
        "U+00A0 is a non-breaking space, not a control character; refusing it "
        "would reject a term a source could legitimately carry")


def test_refusing_a_term_touches_no_global_and_takes_no_lock():
    """`quoted()` used to establish "did `lit()` alter this?" by watching a
    list another module owns.

    It read `len(SANITISED)`, called `lit()`, then deleted back to the length
    it had read — under a lock that only covered callers arriving through
    `engine.py`, while `etl.cypher.props` and `merge` append without one. Safe
    today only because the loader and the server are separate processes; the
    ownership was inverted either way.

    `lit_exact` raises instead, so there is nothing to watch and nothing to
    trim. Asserted on the global rather than on the refusal, because the
    refusal worked before too — what changed is what it cost.
    """
    from etl import cypher

    before = list(cypher.SANITISED)
    for term in ('a"b\'c', "a\nb", float("inf")):
        with pytest.raises(engine.Unbounded):
            engine.quoted(term)
    assert cypher.SANITISED == before, (
        "refusing a term left entries in the loader's SANITISED list, or "
        "removed ones it had already recorded")

    # And a term that IS representable does not record anything either.
    assert engine.quoted("870.5150")
    assert cypher.SANITISED == before

    assert not hasattr(engine, "_SANITISED_LOCK"), (
        "the lock is still here, so something still mutates a global it does "
        "not own")


def test_a_control_character_is_reported_by_the_loader_not_only_refused():
    """The hole the watching could never close.

    `lit()` collapsed control characters to a space and recorded NOTHING, so
    the one alteration `quoted()`'s "refuse anything `lit()` altered"
    invariant could not see was the one that silently changed the term being
    matched. Both halves are fixed by the same split: the loader records the
    collapse, and the query side refuses it.
    """
    from etl import cypher

    cypher.reset()
    assert cypher.lit("a\nb") == '"a b"', "the loader still collapses, as it must"
    assert cypher.SANITISED, (
        "the loader collapsed a control character and recorded nothing, so a "
        "load report cannot say it happened")
    assert "control character" in cypher.SANITISED[-1]["reason"]
    cypher.reset()

    with pytest.raises(engine.Unbounded):
        engine.quoted("a\nb")
