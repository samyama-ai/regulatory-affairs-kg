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
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
CARD = ROOT / "DATASET-CARD.md"

RELEASES = ("https://git.samyama.ai/api/v1/repos/Samyama.ai/"
            "regulatory-affairs-kg/releases")
ASSET = "regulatory-affairs.sgsnap"


def _release() -> dict | None:
    """The newest release carrying the snapshot, or `None` if unreachable.

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
    except (OSError, ValueError):
        return None
    for release in releases:
        for asset in release.get("assets", []):
            if asset.get("name") == ASSET:
                return {"tag": release["tag_name"], "size": asset["size"]}
    return None


@pytest.fixture(scope="module")
def release() -> dict:
    found = _release()
    if found is None:
        pytest.skip("the releases API is unreachable or unauthorised — set "
                    "GITEA_TOKEN to check the documents against the release")
    return found


def _bytes_claimed(text: str) -> set[int]:
    """Every byte figure the document states for the snapshot.

    A SET, not the first match: the two documents state it in three places
    between them, and a check that reads one is a check the other two can
    drift past. Written with thousands separators, which is how a reader can
    tell 2,107,181 bytes from a rounded 2.1 MB at a glance.

    The emphasis is allowed to fall either side of the word — `**N bytes**`
    and `**N** bytes` both read the same and both appear here. Pinning one
    made this report "states no byte figure" about a document that states it,
    which is a false failure and the fastest way to get a check deleted.
    """
    return {int(m.replace(",", ""))
            for m in re.findall(r"([\d,]{7,})\s*(?:\*\*)?\s*bytes", text)}


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
        text = document.read_text(encoding="utf-8")
        for phrase in ("not yet published", "no release exists"):
            assert phrase not in text, (
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
