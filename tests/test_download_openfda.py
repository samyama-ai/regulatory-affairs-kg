"""The downloader's guards, tested without a network.

`etl/download_openfda.py` had no tests at all. Every function below exists to
stop a wrong dataset being written to `./data` — a truncated set that looks
complete, an empty one that loads into an empty graph, a filter that quietly
means something else. Each of those is a silent failure by construction, so
"it worked when I ran it" is not evidence.

All of these are pure functions over lists and strings, or use a stubbed
`fetch`, so nothing here touches api.fda.gov.
"""

import pytest

from etl import download_openfda as dl


# --------------------------------------------------------------------------
# integrity_problem() — described in its own docstring as "what actually
# guards the download", and previously unchecked
# --------------------------------------------------------------------------

def test_a_complete_set_has_no_problem():
    rows = [{"k_number": "K1"}, {"k_number": "K2"}, {"k_number": "K3"}]
    assert dl.integrity_problem("510k", rows, 3) is None


def test_duplicate_keys_are_reported_as_a_paging_fault():
    """Unordered paging can serve one record twice and another never. The row
    count still matches the total, so only distinct keys catch it."""
    rows = [{"k_number": "K1"}, {"k_number": "K1"}, {"k_number": "K3"}]
    problem = dl.integrity_problem("510k", rows, 3)
    assert problem is not None
    assert "2 distinct" in problem and "paging" in problem


def test_a_short_set_is_reported():
    problem = dl.integrity_problem("510k", [{"k_number": "K1"}], 3)
    assert problem is not None and "reported 3" in problem


def test_rows_missing_the_key_are_named_as_a_renamed_field():
    """Dropping these silently would disguise a renamed field as a paging
    fault, sending the reader after the wrong bug."""
    rows = [{"k_number": "K1"}, {"nope": 1}, {"k_number": ""}]
    problem = dl.integrity_problem("510k", rows, 3)
    assert problem is not None
    assert "carry no" in problem and "not a paging fault" in problem


def test_classification_is_guarded_too_despite_having_no_sort():
    """The endpoint has no sortable field, so this check is the only guard it
    gets — see the SORT_KEY comment."""
    assert "classification" not in dl.SORT_KEY
    assert "classification" in dl.UNIQUE_KEY
    rows = [{"product_code": "ABC"}, {"product_code": "ABC"}]
    assert dl.integrity_problem("classification", rows, 2) is not None


# --------------------------------------------------------------------------
# --part validation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("part", ["870", "892", "800", "898"])
def test_a_three_digit_part_is_accepted(part):
    assert dl.PART_PATTERN.match(part)


@pytest.mark.parametrize("part", ["870+AND+x", "87", "8700", "abc", "'", "870 ", ""])
def test_anything_else_is_refused(part):
    """`--part` is interpolated into the openFDA search expression, where a
    value carrying `+AND+` or a quote changes what the query means rather than
    erroring — and a query returning the wrong rows still writes a file."""
    assert not dl.PART_PATTERN.match(part)


def test_download_refuses_a_bad_part_before_any_request(monkeypatch):
    called = []
    monkeypatch.setattr(dl, "fetch_all", lambda *a, **k: called.append(1) or [])
    with pytest.raises(ValueError, match="three-digit CFR part"):
        dl.download("870+AND+x")
    assert not called, "a request was made before the part was validated"


def test_main_reports_a_refusal_as_exit_1(capsys):
    assert dl.main(["--part", "zzz"]) == 1
    assert "refused" in capsys.readouterr().err


# --------------------------------------------------------------------------
# fetch_all()'s two refusals, with `fetch` stubbed
# --------------------------------------------------------------------------

def stub_fetch(monkeypatch, total_count, pages):
    """Replace `fetch` with one that reports `total_count` and serves `pages`."""
    served = iter(pages)

    def fake(endpoint, *, search=None, limit=1, skip=0):
        if limit == 1:
            return {"meta": {"results": {"total": total_count}}, "results": []}
        return {"results": next(served, [])}

    monkeypatch.setattr(dl, "fetch", fake)


def test_an_empty_result_set_is_refused(monkeypatch):
    """A mistyped filter returns zero matches. Writing that out produces a
    valid-looking file that loads into an empty graph with no error."""
    stub_fetch(monkeypatch, 0, [])
    with pytest.raises(ValueError, match="matched 0 records"):
        dl.fetch_all("510k", search="openfda.regulation_number:999*")


def test_a_set_over_the_skip_cap_is_refused(monkeypatch):
    """openFDA caps `skip` at 25,000 without a key, so a larger set cannot be
    walked page by page. Refusing beats loading a truncated set and calling it
    complete."""
    stub_fetch(monkeypatch, dl.SKIP_CAP + 1, [])
    with pytest.raises(ValueError, match="skip cap"):
        dl.fetch_all("510k")


def test_a_transient_shuffle_is_retried_and_recovers(monkeypatch):
    """Unordered paging on classification can shuffle between requests. That is
    transient, so it is retried rather than failing on the first attempt."""
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    attempts = []

    def fake_page_through(endpoint, search, count):
        attempts.append(1)
        if len(attempts) == 1:
            return [{"k_number": "K1"}, {"k_number": "K1"}]   # duplicate
        return [{"k_number": "K1"}, {"k_number": "K2"}]

    monkeypatch.setattr(dl, "page_through", fake_page_through)
    monkeypatch.setattr(dl, "total", lambda e, s=None: 2)
    rows = dl.fetch_all("510k")
    assert len(rows) == 2 and len(attempts) == 2


def test_a_persistent_fault_still_refuses(monkeypatch):
    """Retrying is not the same as giving up on correctness."""
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    monkeypatch.setattr(dl, "page_through",
                        lambda e, s, c: [{"k_number": "K1"}, {"k_number": "K1"}])
    monkeypatch.setattr(dl, "total", lambda e, s=None: 2)
    with pytest.raises(ValueError, match="Still wrong after"):
        dl.fetch_all("510k")


# --------------------------------------------------------------------------
# throttle
# --------------------------------------------------------------------------

def test_requests_are_spaced_under_the_rate_limit(monkeypatch):
    """openFDA allows 240 requests/minute unauthenticated. Staying under it by
    construction beats discovering it as a 429 mid-download, especially now a
    failed integrity check re-pages a whole endpoint."""
    slept = []
    clock = [0.0]
    monkeypatch.setattr(dl.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(dl.time, "monotonic", lambda: clock[0])
    dl._last_request_at = 0.0
    dl.throttle()
    dl.throttle()
    assert slept and slept[-1] == pytest.approx(dl.MIN_REQUEST_INTERVAL)
    assert dl.MIN_REQUEST_INTERVAL == pytest.approx(60 / 240)
