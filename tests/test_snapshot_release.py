"""Every document's snapshot claims, against the release they describe.

Three claims drifted at once — `README.md` and `DATASET-CARD.md` both said the
snapshot was "not yet published", the same dataset card told readers to
download it from the releases page two hundred lines further down, and both
stated a size 5% off the file's.

They drifted because nothing held them. No test referenced `.sgsnap` at all, in
a repo whose stated rule is that no number reaches a document by being typed.

**Two lessons are built into the shape of this file.**

*The document set is DISCOVERED, not listed.* The first version iterated
`(README, CARD)` — and `demo/README.md` carried both errors the whole time,
untouched and unchecked, which is the same drift one document to the left. A
list is what let the third document rot; anything tracked that names the
snapshot is checked now.

*The checks run WITHOUT a token.* They depended on a live, authenticated call
to a private repository, so on any developer machine and in any CI job without
the secret the three real assertions skipped and only the self-referential unit
tests ran — "a silent skip is indistinguishable from a pass", one level up from
the failure this file was written about. The release facts are committed in
`snapshot-release.json` and the documents are checked against that, always. One
test reconciles the record with the live API, and it is the only one that needs
the network.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "snapshot-release.json"

RELEASES = ("https://git.samyama.ai/api/v1/repos/Samyama.ai/"
            "regulatory-affairs-kg/releases")
ASSET = "regulatory-affairs.sgsnap"

UNPUBLISHED = ("not yet published", "no release exists",
               "no release is published", "once a release exists")


def tracked_documents() -> list[Path]:
    """Every tracked markdown file that names the snapshot.

    Discovered rather than enumerated. `demo/README.md` was never in the list
    and carried both of the errors this file exists to catch — a document is
    added to this repo far more often than this test is edited.
    """
    listed = subprocess.run(["git", "ls-files", "*.md"], cwd=str(ROOT),
                            capture_output=True, text=True)
    if listed.returncode != 0:
        return []
    found = []
    for name in listed.stdout.split():
        path = ROOT / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # `.sgsnap`, not the full filename. The root `README.md` writes
        # "`.sgsnap` v2" and never the whole name, so a predicate matching
        # `regulatory-affairs.sgsnap` missed the very document this PR fixed
        # first — a discovery rule with the same defect as the list it
        # replaced, one character narrower.
        if ".sgsnap" in text:
            found.append(path)
    return found


DOCUMENTS = tracked_documents()


@pytest.fixture(scope="module")
def release() -> dict:
    """The committed release facts.

    Not the API. These assertions are about what the documents say, and tying
    them to an authenticated call meant they did not run for anyone without
    the token. `test_the_record_still_matches_the_live_release` is where the
    record is held to reality.
    """
    return json.loads(RECORD.read_text(encoding="utf-8"))


def _bytes_claimed(text: str) -> set[int]:
    r"""Every byte figure a document states for the snapshot.

    A SET, not the first match: the documents state it in several places and a
    check that reads one is a check the others can drift past.

    The pattern spells out grouped thousands and is BOUNDED on both sides.
    Spelling out the grouping alone was not enough — against `2,,107,181` it
    matched the `107,181` inside and reported a number no document states,
    which is quieter than the loose `[\d,]{7,}` it replaced and worse, because
    a wrong figure then has a plausible value.

    The emphasis may fall either side of the word: `**N bytes**` and
    `**N** bytes` both read the same and both appear here.
    """
    return {int(m.replace(",", ""))
            for m in re.findall(
                r"(?<![\d,])(\d{1,3}(?:,\d{3})+)(?![\d,])\s*(?:\*\*)?\s*bytes",
                text)}


def _says_unpublished(text: str) -> list[str]:
    """Which "there is no release" phrasings a document still carries.

    Lowercased. The card wrote "**No release exists yet**" at the start of a
    sentence and a case-sensitive check walked past the live instance of the
    exact string it named.
    """
    lowered = text.lower()
    return [phrase for phrase in UNPUBLISHED if phrase in lowered]


# --------------------------------------------------------------------------
# the documents, checked without a token
# --------------------------------------------------------------------------

def test_the_check_found_documents_to_check():
    """A discovered set that discovers nothing passes everything."""
    assert DOCUMENTS, "no tracked document names the snapshot — did it move?"
    names = {d.relative_to(ROOT).as_posix() for d in DOCUMENTS}
    # Both named. `demo/README.md` drifted while the first version iterated a
    # hardcoded pair; `README.md` was then missed by the predicate that
    # replaced that pair, because it writes only the extension.
    for expected in ("README.md", "demo/README.md", "DATASET-CARD.md"):
        assert expected in names, (
            f"{expected} names the snapshot but is not in the checked set — "
            f"the discovery rule is narrower than the documents")


@pytest.mark.parametrize("document", DOCUMENTS,
                         ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_document_states_the_size_the_release_has(document, release):
    """2.2 MB was wrong under both conventions — 2.11 MB, or 2.01 MiB.

    Bytes rather than megabytes: the two disagree by 5% and both appear in the
    wild, which is how "2.2" survived three revisions.
    """
    claimed = _bytes_claimed(document.read_text(encoding="utf-8"))
    if not claimed:
        # The RELATIVE path. Two files here are called README.md, and a skip
        # naming only "README.md" sent me looking at the wrong one.
        pytest.skip(f"{document.relative_to(ROOT)} states no byte figure")
    assert claimed == {release["size"]}, (
        f"{document.relative_to(ROOT)} claims {sorted(claimed)} bytes; the "
        f"release asset is {release['size']:,}")


@pytest.mark.parametrize("document", DOCUMENTS,
                         ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_document_says_the_snapshot_is_unpublished(document, release):
    """The claim that drifted, and the contradiction it left behind."""
    said = _says_unpublished(document.read_text(encoding="utf-8"))
    assert not said, (
        f"{document.relative_to(ROOT)} still says {said}, but "
        f"{release['tag']} exists and carries {ASSET}")


def test_a_document_naming_a_release_names_the_right_one(release):
    """Named rather than counted — a check for "a tag" passes on a stale one."""
    naming = [d for d in DOCUMENTS
              if "snapshot-" in d.read_text(encoding="utf-8")]
    assert naming, "no document names the release carrying the snapshot"
    for document in naming:
        assert release["tag"] in document.read_text(encoding="utf-8"), (
            f"{document.relative_to(ROOT)} names a release tag that is not "
            f"{release['tag']}")


# --------------------------------------------------------------------------
# the record, held to reality — the only test that needs the network
# --------------------------------------------------------------------------

def _live_release() -> dict | str:
    """The release as the API reports it, or a string saying why not.

    A STRING rather than `None`, because three different things landed on that
    one value: unauthorised, offline, and the release genuinely deleted. The
    third is the failure this file exists to catch, and a reason covering all
    three hides the one worth seeing.

    `except Exception` around the whole walk, not only the fetch: a Gitea
    error body, an HTML proxy or a captive portal answers 200 with a shape
    this code then indexes into, and an `AttributeError` from `release.get` on
    a `str` is an environment problem reported as a test error rather than the
    intended skip.
    """
    request = urllib.request.Request(RELEASES,
                                     headers={"Accept": "application/json"})
    token = os.environ.get("GITEA_TOKEN")
    if token:
        request.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            releases = json.loads(response.read())
        for release in releases:
            for asset in release.get("assets", []):
                if asset.get("name") == ASSET:
                    return {"tag": release["tag_name"], "size": asset["size"]}
    except Exception as exc:                                  # noqa: BLE001
        return (f"the releases API could not be read ({exc!r}) — set "
                f"GITEA_TOKEN, or check the network")
    return "REACHED"


def test_the_record_still_matches_the_live_release():
    """The one network test, and the only one that may skip.

    Everything above runs offline against the committed record. This is what
    stops that record becoming a second typed figure: it re-reads the release
    and diffs. Drift surfaces once, here, as "the record is stale" — rather
    than as four documents quietly describing something that changed.
    """
    live = _live_release()
    if live == "REACHED":
        pytest.fail(
            f"the releases API answered and no release carries {ASSET}. The "
            f"documents describe a published snapshot that is not there — "
            f"this is the failure this file exists to catch, not a skip.")
    if isinstance(live, str):
        pytest.skip(live)
    recorded = json.loads(RECORD.read_text(encoding="utf-8"))
    assert live == {k: recorded[k] for k in ("tag", "size")}, (
        f"{RECORD.name} is stale: it holds {recorded['tag']} at "
        f"{recorded['size']:,} bytes; the API reports {live['tag']} at "
        f"{live['size']:,}. Refresh it and update the documents together.")


# --------------------------------------------------------------------------
# the helpers, against the shapes that fooled them
# --------------------------------------------------------------------------

@pytest.mark.parametrize("spelling", [
    "**No release exists yet**, so there is nowhere to fetch it from.",
    "no release exists yet",
    "The snapshot is Not Yet Published.",
    "NOT YET PUBLISHED",
    "# no release is published yet, so this route is not available.",
    "… or, once a release exists, import the snapshot",
])
def test_the_phrase_check_does_not_care_about_capitals(spelling):
    """The card capitalised it at the start of a sentence.

    `assert phrase not in text` walked past the live instance of the exact
    string it named. Prose capitalises; a phrase check must not care. The last
    two spellings are the ones `demo/README.md` actually carried, and neither
    was in the original list.
    """
    assert _says_unpublished(spelling), (
        f"the check misses {spelling!r} — it will miss it in a document too")


def test_a_document_that_says_neither_is_clean():
    """A check that flags everything gets switched off as fast as one that
    flags nothing."""
    assert _says_unpublished(
        "The snapshot is published as snapshot-2026-08-24.") == []


@pytest.mark.parametrize("text,expected", [
    ("**2,107,181 bytes**", {2107181}),
    ("2,107,181 bytes", {2107181}),
    ("**2,107,181** bytes", {2107181}),
    ("2,,107,181 bytes", set()),
    ("2107181 bytes", set()),
    ("2,10,181 bytes", set()),
])
def test_only_a_properly_grouped_figure_is_read_as_a_size(text, expected):
    r"""`\d{1,3}(?:,\d{3})+`, bounded, says what the check means.

    `[\d,]{7,}` said "digits and commas, at least seven of them", which
    describes the characters rather than the number.
    """
    assert _bytes_claimed(text) == expected


def test_a_deleted_release_fails_rather_than_skips(monkeypatch):
    """The one failure this file exists to catch must not look like a skip.

    Caught by hand, not with `pytest.raises`. Both `fail` and `skip` raise
    `BaseException` subclasses, so `raises(Exception)` catches neither — and
    if the code under test SKIPS, the `Skipped` escapes `raises` and skips
    this test too, reporting green.
    """
    monkeypatch.setattr(sys.modules[__name__], "_live_release",
                        lambda: "REACHED")
    try:
        test_the_record_still_matches_the_live_release()
    except BaseException as raised:                          # noqa: BLE001
        outcome = raised
    else:
        outcome = None
    assert isinstance(outcome, pytest.fail.Exception), (
        f"a deleted release produced {type(outcome).__name__} — it must FAIL. "
        f"A skip here is indistinguishable from a missing token.")
    assert "not there" in str(outcome)


def test_an_unreachable_api_skips_and_says_why(monkeypatch):
    """The ordinary cases stay skips, with the underlying reason surfaced.

    "unauthorised" and "the host is down" need different responses from
    whoever reads the log.
    """
    monkeypatch.setattr(sys.modules[__name__], "_live_release",
                        lambda: "the releases API could not be read (boom)")
    try:
        test_the_record_still_matches_the_live_release()
    except BaseException as raised:                          # noqa: BLE001
        outcome = raised
    else:
        outcome = None
    assert isinstance(outcome, pytest.skip.Exception), (
        f"an unreachable API produced {type(outcome).__name__} — it must skip")
    assert "could not be read" in str(outcome)


def test_a_non_list_response_skips_rather_than_erroring(monkeypatch):
    """A captive portal or an HTML proxy answers 200 with the wrong shape.

    `except (OSError, ValueError)` wrapped only the fetch and the parse, so
    iterating a dict yielded its keys and `release.get` raised `AttributeError`
    on a `str` — an environment problem reported as a test error rather than
    the intended skip.
    """
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"message": "not authorised"}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response())
    assert isinstance(_live_release(), str)
