"""The documents' snapshot claims, against the release they describe.

Three claims drifted at once — `README.md` and `DATASET-CARD.md` both said the
snapshot was "not yet published", while the same dataset card told readers to
download it from the releases page two hundred lines further down, and both
stated a size 5% off the file's.

They drifted because nothing held them. No test in this repo referenced
`.sgsnap` at all, in a repo whose stated rule is that no number reaches a
document by being typed. The size, the tag and whether a release exists are now
read from the API and compared.

**Network-gated, and it says which it did.** A repo check that silently skips
is indistinguishable from one that passes, so the skip reason names the reason.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
CARD = ROOT / "DATASET-CARD.md"

RELEASES = ("https://git.samyama.ai/api/v1/repos/Samyama.ai/"
            "regulatory-affairs-kg/releases")
ASSET = "regulatory-affairs.sgsnap"


def _release() -> dict | str:
    """The release carrying the snapshot, or a string saying why not.

    A STRING rather than `None`, because three different things landed on that
    one value: unauthorised, offline, and the release genuinely deleted. The
    third is the failure this file exists to catch, and a skip reason that
    cannot tell it from a missing token hides exactly the case worth seeing.

    A token is used when one is in the environment and omitted otherwise: the
    repository is private today, so an unauthenticated run cannot see the
    release and must skip rather than fail. `None` covers both that and an
    offline machine — the caller turns it into a skip with a reason.
    """
    request = urllib.request.Request(RELEASES, headers={"Accept": "application/json"})
    token = os.environ.get("GITEA_TOKEN")
    if token:
        request.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            releases = json.loads(response.read())
    except (OSError, ValueError) as exc:
        return (f"the releases API could not be read ({exc!r}) — set "
                f"GITEA_TOKEN, or check the network")
    for release in releases:
        for asset in release.get("assets", []):
            if asset.get("name") == ASSET:
                return {"tag": release["tag_name"], "size": asset["size"]}
    # Reached the API and it answered. No release carries the asset, which is
    # not a skip: it is the documents describing something that is not there.
    return "REACHED"


@pytest.fixture(scope="module")
def release() -> dict:
    found = _release()
    if found == "REACHED":
        pytest.fail(
            f"the releases API answered and no release carries {ASSET}. The "
            f"documents describe a published snapshot that is not there — "
            f"this is the failure this file exists to catch, not a skip.")
    if isinstance(found, str):
        pytest.skip(found)
    return found


def _bytes_claimed(text: str) -> set[int]:
    """Every byte figure the document states for the snapshot.

    A SET, not the first match: the two documents state it in three places
    between them, and a check that reads one is a check the other two can
    drift past. Written with thousands separators, which is how a reader can
    tell 2,107,181 bytes from a rounded 2.1 MB at a glance.

    The pattern spells out grouped thousands rather than "digits and commas",
    and is BOUNDED on both sides. Spelling out the grouping alone was not
    enough: against `2,,107,181` it matched the `107,181` inside and reported a
    number the document does not state — quieter than the looser form it
    replaced, and worse, because a wrong figure now had a plausible value. The
    lookarounds stop a match beginning or ending mid-figure.

    The emphasis is allowed to fall either side of the word — `**N bytes**`
    and `**N** bytes` both read the same and both appear here. Pinning one
    made this report "states no byte figure" about a document that states it,
    which is a false failure and the fastest way to get a check deleted.
    """
    return {int(m.replace(",", ""))
            for m in re.findall(
                r"(?<![\d,])(\d{1,3}(?:,\d{3})+)(?![\d,])\s*(?:\*\*)?\s*bytes",
                text)}


def test_the_documents_state_the_size_the_release_actually_has(release):
    """2.2 MB was wrong under both conventions — the file is 2.11 MB / 2.01 MiB.

    Bytes are asserted rather than megabytes because the two conventions
    disagree by 5% and both appear in the wild; a document quoting one and a
    reader assuming the other is how "2.2" survived three revisions.
    """
    for document in (README, CARD):
        claimed = _bytes_claimed(document.read_text(encoding="utf-8"))
        assert claimed, (
            f"{document.name} states no byte figure for the snapshot — it "
            f"should, so this check has something to compare")
        assert claimed == {release["size"]}, (
            f"{document.name} claims {sorted(claimed)} bytes; the release "
            f"asset is {release['size']:,}")


def test_no_document_still_says_the_snapshot_is_unpublished(release):
    """The claim that drifted, and the contradiction it left behind.

    `DATASET-CARD.md` said "not yet published" and, two hundred lines later,
    "download it from the repository's Releases page". Both cannot be true and
    the second one is.
    """
    for document in (README, CARD):
        # LOWERCASED. The card wrote "**No release exists yet**" at the start
        # of a sentence, and a case-sensitive check walked past the live
        # instance of the exact string it names — the one thing this test is
        # for. Prose capitalises; a phrase check has to not care.
        lowered = document.read_text(encoding="utf-8").lower()
        for phrase in ("not yet published", "no release exists"):
            assert phrase not in lowered, (
                f"{document.name} still says {phrase!r}, but "
                f"{release['tag']} exists and carries {ASSET}")


def test_the_documents_name_the_release_that_holds_it(release):
    """A reader told a release exists needs to be told which one.

    Named rather than counted: a check that some tag is mentioned passes on a
    stale tag, which is the same defect one revision later.
    """
    for document in (README, CARD):
        assert release["tag"] in document.read_text(encoding="utf-8"), (
            f"{document.name} does not name {release['tag']}, the release "
            f"carrying the snapshot it describes")


def _says_unpublished(text: str) -> list[str]:
    """The phrase check, over arbitrary text.

    Extracted so it can be exercised against a document that DOES carry the
    phrase. The parametrised checks above run over the real files, which no
    longer do — so making the check case-sensitive again passes them, and the
    mutation that reintroduces the original defect survives a test written to
    catch it.
    """
    lowered = text.lower()
    return [p for p in ("not yet published", "no release exists") if p in lowered]


@pytest.mark.parametrize("spelling", [
    "**No release exists yet**, so there is nowhere to fetch it from.",
    "no release exists yet",
    "The snapshot is Not Yet Published.",
    "NOT YET PUBLISHED",
])
def test_the_phrase_check_does_not_care_about_capitals(spelling):
    """The card wrote it capitalised at the start of a sentence.

    `assert phrase not in text` walked past the live instance of the exact
    string it names — the single thing this file exists to catch, missed by
    the check written to catch it. Prose capitalises; a phrase check must not
    care.
    """
    assert _says_unpublished(spelling), (
        f"the check misses {spelling!r} — it will miss it in the document too")


def test_a_document_that_says_neither_is_clean():
    """The false-positive direction. A check that flags everything gets
    switched off as fast as one that flags nothing."""
    assert _says_unpublished(
        "The snapshot is published as snapshot-2026-08-24.") == []


def test_a_deleted_release_fails_rather_than_skips(monkeypatch):
    """The one failure this file exists to catch must not look like a skip.

    `None` meant unauthorised, offline, and "the release is gone" all at once.
    The third is the case worth seeing, and a skip reason covering all three
    hides it behind the two that are ordinary.

    Caught as `pytest.fail.Exception`, not `Exception`. Both `fail` and `skip`
    raise `BaseException` subclasses, so `pytest.raises(Exception)` does not
    catch either — the first version of this test failed, and its sibling
    below SKIPPED ITSELF, which is the same trap one level up.
    """
    monkeypatch.setattr(sys.modules[__name__], "_release", lambda: "REACHED")
    # NOT `pytest.raises`. If the fixture skips instead of failing — which is
    # the regression this guards — the `Skipped` escapes `raises` and skips
    # THIS test, reporting green. Caught by hand so the outcome can be named.
    try:
        release.__wrapped__()
    except BaseException as raised:          # noqa: BLE001 — the type is the assertion
        outcome = raised
    else:
        outcome = None
    assert isinstance(outcome, pytest.fail.Exception), (
        f"a deleted release produced {type(outcome).__name__} — it must FAIL. "
        f"A skip here is indistinguishable from a missing token, and this is "
        f"the one case the file exists to catch.")
    assert "not there" in str(outcome)


def test_an_unreachable_api_skips_and_says_why(monkeypatch):
    """And the ordinary cases stay skips, with the reason surfaced.

    The underlying exception is in the message: "unauthorised" and "the host
    is down" need different responses from whoever reads the log.
    """
    monkeypatch.setattr(sys.modules[__name__], "_release",
                        lambda: "the releases API could not be read (boom)")
    try:
        release.__wrapped__()
    except BaseException as raised:          # noqa: BLE001
        outcome = raised
    else:
        outcome = None
    assert isinstance(outcome, pytest.skip.Exception), (
        f"an unreachable API produced {type(outcome).__name__} — it must skip")
    assert "could not be read" in str(outcome)


@pytest.mark.parametrize("text,expected", [
    ("**2,107,181 bytes**", {2107181}),
    ("2,107,181 bytes", {2107181}),
    ("**2,107,181** bytes", {2107181}),
    # Malformed. The looser pattern accepted these and stripped the commas
    # before `int()`, so a mistyped figure compared EQUAL to a correct one and
    # the check passed on a document stating nonsense.
    ("2,,107,181 bytes", set()),
    ("2107181 bytes", set()),
    ("2,10,181 bytes", set()),
])
def test_only_a_properly_grouped_figure_is_read_as_a_size(text, expected):
    """`\d{1,3}(?:,\d{3})+` says what the check means.

    `[\d,]{7,}` said "digits and commas, at least seven of them", which is a
    description of the characters rather than of a number.
    """
    assert _bytes_claimed(text) == expected
