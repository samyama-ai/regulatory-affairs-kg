"""The downloader's guards, tested without a network.

`etl/download_openfda.py` had no tests at all. Every function below exists to
stop a wrong dataset being written to `./data` — a truncated set that looks
complete, an empty one that loads into an empty graph, a filter that quietly
means something else. Each of those is a silent failure by construction, so
"it worked when I ran it" is not evidence.

All of these are pure functions over lists and strings, or use a stubbed
`fetch`, so nothing here touches api.fda.gov.
"""

import json

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


@pytest.mark.parametrize("part", [
    "870+AND+x", "87", "8700", "abc", "'", "870 ", "",
    # Both of these passed `^\d{3}$`. `$` matches before a trailing newline,
    # and `\d` matches every Unicode decimal digit — neither is a CFR part.
    "870\n",
    "\u0668\u0666\u0660",          # Arabic-Indic digits
    "\uff18\uff17\uff10",          # fullwidth digits
])
def test_anything_else_is_refused(part):
    """`--part` is interpolated into the openFDA search expression, where a
    value carrying `+AND+` or a quote changes what the query means rather than
    erroring — and a query returning the wrong rows still writes a file."""
    assert not dl.PART_PATTERN.match(part)


def test_download_refuses_a_bad_part_before_any_request(monkeypatch):
    called = []
    monkeypatch.setattr(dl, "fetch_all", lambda *a, **k: called.append(1) or [])
    with pytest.raises(ValueError, match="not three digits"):
        dl.download("870+AND+x")
    assert not called, "a request was made before the part was validated"


def test_main_reports_a_refusal_as_exit_1(capsys):
    assert dl.main(["--part", "zzz"]) == 1
    assert "refused" in capsys.readouterr().err


# --------------------------------------------------------------------------
# fetch_all()'s two refusals, with `fetch` stubbed
# --------------------------------------------------------------------------

def stub_fetch(monkeypatch, total_count, pages=()):
    """Replace `fetch` with one reporting `total_count` and serving `pages`.

    Both refusal tests below bail before any page is fetched, so they pass no
    pages — the parameter exists for tests that do get that far.
    """
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
    failed integrity check re-pages a whole endpoint.

    `_last_request_at` is a module global; monkeypatch restores it after, so
    this test cannot bleed a timestamp into the next one.
    """
    slept = []
    clock = [0.0]
    monkeypatch.setattr(dl.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(dl.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(dl, "_last_request_at", 0.0)
    dl.throttle()
    dl.throttle()
    assert slept and slept[-1] == pytest.approx(dl.MIN_REQUEST_INTERVAL)
    assert dl.MIN_REQUEST_INTERVAL == pytest.approx(60 / 240)


# --------------------------------------------------------------------------
# fetch() — the 404 mapping and the retry ladder, both stubbed
# --------------------------------------------------------------------------

class FakeHTTPError(dl.urllib.error.HTTPError):
    def __init__(self, code):
        super().__init__("http://x", code, "boom", {}, None)


def test_a_404_means_no_matches_not_a_failure(monkeypatch):
    """openFDA returns 404 for an empty result set. Treating it as an error
    would make a legitimate "nothing matched" indistinguishable from a broken
    request; `fetch_all` is what decides an empty set is unacceptable."""
    monkeypatch.setattr(dl, "throttle", lambda: None)
    monkeypatch.setattr(dl.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(FakeHTTPError(404)))
    assert dl.fetch("510k") == {"meta": {"results": {"total": 0}}, "results": []}


@pytest.mark.parametrize("code", [429, 500, 502, 503])
def test_transient_http_codes_are_retried(monkeypatch, code):
    monkeypatch.setattr(dl, "throttle", lambda: None)
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    attempts = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def flaky(*a, **k):
        attempts.append(1)
        if len(attempts) < 3:
            raise FakeHTTPError(code)
        return Response()

    monkeypatch.setattr(dl.urllib.request, "urlopen", flaky)
    assert dl.fetch("510k") == {"ok": True}
    assert len(attempts) == 3


def test_a_4xx_is_not_retried(monkeypatch):
    """A bad request fails identically every time; four attempts only delay
    the report."""
    monkeypatch.setattr(dl, "throttle", lambda: None)
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    attempts = []

    def bad(*a, **k):
        attempts.append(1)
        raise FakeHTTPError(400)

    monkeypatch.setattr(dl.urllib.request, "urlopen", bad)
    with pytest.raises(dl.urllib.error.HTTPError):
        dl.fetch("510k")
    assert len(attempts) == 1, f"a 400 was retried {len(attempts)} times"


def test_the_retry_ladder_gives_up_rather_than_looping(monkeypatch):
    monkeypatch.setattr(dl, "throttle", lambda: None)
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    monkeypatch.setattr(dl.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(dl.urllib.error.URLError("down")))
    with pytest.raises(dl.urllib.error.URLError):
        dl.fetch("510k")


def test_backoff_grows_and_is_jittered(monkeypatch):
    """Jitter matters because a failed integrity check re-pages a whole
    endpoint: two runs that hit the same 429 should not march in lockstep."""
    monkeypatch.setattr(dl, "throttle", lambda: None)
    slept = []
    monkeypatch.setattr(dl.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(dl.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(FakeHTTPError(503)))
    with pytest.raises(dl.urllib.error.HTTPError):
        dl.fetch("510k")
    assert len(slept) == 3
    assert slept[0] < slept[1] < slept[2], slept       # grows
    assert not all(float(s).is_integer() for s in slept), slept   # jittered


# --------------------------------------------------------------------------
# write()
# --------------------------------------------------------------------------

def test_write_records_meta_alongside_the_rows(tmp_path, monkeypatch):
    """The meta block is how a later reader knows which slice this file is —
    which endpoint, which search, and when it was taken."""
    monkeypatch.setattr(dl, "DATA_DIR", tmp_path)
    rows = [{"k_number": "K1"}]
    meta = {"endpoint": "device/510k", "search": "openfda.regulation_number:870*",
            "retrieved_at": "2026-08-14T00:00:00+00:00", "count": 1}
    path = dl.write("510k", rows, meta)

    import json
    payload = json.loads(path.read_text())
    assert payload["results"] == rows
    assert payload["meta"] == meta
    assert path.name == "510k.json" and path.parent == tmp_path


def test_paging_that_stops_early_is_reported():
    """`page_through` breaks on an empty page. Without this the run keeps
    whatever it managed to fetch and the distinct-key check passes, because
    the keys it did get are all distinct."""
    rows = [{"k_number": f"K{i}"} for i in range(5)]
    problem = dl.integrity_problem("510k", rows, 100)
    assert problem is not None and "stopped early" in problem


def test_an_endpoint_with_no_unique_key_is_refused_not_waved_through():
    """`if not key: return None` made the guard opt-in by dictionary entry —
    add registrationlisting, forget the UNIQUE_KEY line, and every paging fault
    passes verification."""
    problem = dl.integrity_problem("registrationlisting", [{"x": 1}], 1)
    assert problem is not None and "UNIQUE_KEY" in problem


def test_a_non_json_response_is_not_reported_as_a_bad_filter(monkeypatch, capsys):
    """`json.JSONDecodeError` subclasses `ValueError`, so it was caught by the
    `refused:` branch — the message this script uses for "your filter is wrong"
    — sending the reader to check their arguments when the API is at fault."""
    def broken(part):
        raise json.JSONDecodeError("Expecting value", "<html>502</html>", 0)

    monkeypatch.setattr(dl, "download", broken)
    assert dl.main(["--part", "870"]) == 2
    err = capsys.readouterr().err
    assert "not JSON" in err and "refused" not in err
