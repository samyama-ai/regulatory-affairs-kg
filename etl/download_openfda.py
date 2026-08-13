"""Download the openFDA slices this build loads, into ./data.

Two slices, chosen because of what the API will and will not give you:

  classification   all 7,085 records — the complete product-code -> device-class
                   -> 21 CFR section map. The whole device-to-law backbone, and
                   small enough to take whole.

  510k             part 870 (cardiovascular) only, ~19,127 records. openFDA caps
                   `skip` at 25,000 without an API key, so the full 175,686
                   clearances cannot be walked page by page. Filtering works, so
                   a scoped load is the honest first build. Part 870 was picked
                   because it fits under the cap and contains 870.5150 — the
                   worked example in the README.

Raw rows are never committed; ./data is gitignored, per the KG-repo convention.

    python -m etl.download_openfda
    python -m etl.download_openfda --part 892     # a different CFR part
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.fda.gov"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PAGE = 1000                     # openFDA's maximum
SKIP_CAP = 25_000               # hard limit without an API key
USER_AGENT = "regulatory-affairs-kg/0.1 (+https://github.com/samyama-ai)"

# openFDA allows 240 requests/minute without an API key. Staying under it by
# construction is better than discovering it as a 429 mid-download: a whole
# endpoint is re-paged on a failed integrity check, so a throttled run is
# cheaper than a retried one.
MIN_REQUEST_INTERVAL = 60 / 240
_last_request_at = 0.0

# A CFR part is three digits (800-898 for devices). Validated because it is
# interpolated into the openFDA `search` expression, where a value carrying
# `+AND+` or a quote would silently change what the query means rather than
# erroring — and a query that returns the wrong rows still writes a file.
PART_PATTERN = re.compile(r"^\d{3}$")

# How many times to re-page an endpoint whose integrity check fails. Unordered
# paging on `classification` can shuffle between requests; that is transient
# and a second pass usually lands clean, so this is not a hard failure on the
# first try. It still refuses in the end rather than writing a suspect set.
PAGING_ATTEMPTS = 3


# Paging is a sequence of independent requests, and openFDA guarantees no order
# unless `sort` is given. Without one, a record can land on two pages while
# another lands on none — and the run still reports the expected total, so
# nothing looks wrong.
#
# `sort` is only half available. Measured against the live API on 2026-08-13:
#
#   510k            sort=k_number.exact:asc        200
#   classification  sort=product_code:asc          400
#                   sort=product_code.exact:asc    500
#
# and likewise 400 or 500 for every other classification field tried
# (regulation_number, device_class, device_name, definition, review_panel,
# medical_specialty_description, submission_type_id). The endpoint has no
# sortable field.
#
# So order is pinned where the API allows it, and the result is *verified*
# either way — see `check_unique`. The check is the part that actually holds:
# it catches a short or duplicated download whether or not sort was available.
SORT_KEY = {"510k": "k_number.exact"}

# The field that uniquely identifies a row, per endpoint.
UNIQUE_KEY = {"classification": "product_code", "510k": "k_number"}


def throttle() -> None:
    """Space requests so the unauthenticated rate limit is never reached."""
    global _last_request_at
    wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def fetch(endpoint: str, *, search: str | None = None, limit: int = 1, skip: int = 0) -> dict:
    """One request. Raises on anything that is not a clean 200 with results."""
    params = {"limit": limit, "skip": skip}
    if endpoint in SORT_KEY:
        params["sort"] = f"{SORT_KEY[endpoint]}:asc"

    if search:
        params["search"] = search
    url = f"{API}/device/{endpoint}.json?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        throttle()
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # 404 from openFDA means "no matches", which is a real answer.
            if exc.code == 404:
                return {"meta": {"results": {"total": 0}}, "results": []}
            if exc.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"giving up on {url}")


def total(endpoint: str, search: str | None = None) -> int:
    return fetch(endpoint, search=search)["meta"]["results"]["total"]


def fetch_all(endpoint: str, search: str | None = None) -> list[dict]:
    """Page through a result set.

    Refuses rather than silently truncates if the set exceeds the skip cap —
    a partial download that looks complete is the failure mode worth avoiding.
    Refuses on an empty result set for the same reason: a mistyped `--part` or
    a renamed field returns zero matches, and writing that out produces a valid
    file that loads into an empty graph without ever reporting an error.
    """
    count = total(endpoint, search)
    if count == 0:
        raise ValueError(
            f"{endpoint} matched 0 records"
            + (f" for search `{search}`" if search else "")
            + ". That is a bad filter or a changed field name, not a real "
              "answer — refusing to write an empty file."
        )
    if count > SKIP_CAP:
        raise ValueError(
            f"{endpoint} matches {count:,} records, over openFDA's {SKIP_CAP:,} "
            f"skip cap. Narrow the filter or use bulk downloads — do not load a "
            f"truncated set and call it complete."
        )
    for attempt in range(1, PAGING_ATTEMPTS + 1):
        rows = page_through(endpoint, search, count)
        problem = integrity_problem(endpoint, rows, count)
        if problem is None:
            return rows
        if attempt < PAGING_ATTEMPTS:
            print(f"  {problem}\n  re-paging {endpoint} "
                  f"(attempt {attempt + 1} of {PAGING_ATTEMPTS})")
            time.sleep(2 ** attempt)
    raise ValueError(
        f"{problem} Still wrong after {PAGING_ATTEMPTS} attempts — refusing to "
        f"write a set that would look complete."
    )


def page_through(endpoint: str, search: str | None, count: int) -> list[dict]:
    rows: list[dict] = []
    while len(rows) < count:
        page = fetch(endpoint, search=search, limit=PAGE, skip=len(rows))["results"]
        if not page:
            break
        rows.extend(page)
        print(f"  {endpoint}: {len(rows):,}/{count:,}", end="\r", flush=True)
    print(f"  {endpoint}: {len(rows):,}/{count:,}      ")
    return rows


def integrity_problem(endpoint: str, rows: list[dict], expected: int) -> str | None:
    """Describe what is wrong with a paged result set, or None if it is sound.

    This is what actually guards the download. `sort` pins the order on the one
    endpoint that supports it; this catches the failure on both, by counting
    distinct keys rather than trusting the row count.

    Rows missing the key are reported separately rather than quietly dropped —
    a renamed field would otherwise arrive disguised as a paging fault, sending
    the reader after the wrong bug.
    """
    key = UNIQUE_KEY.get(endpoint)
    if not key:
        return None
    missing = sum(1 for r in rows if not r.get(key))
    if missing:
        return (f"{endpoint}: {missing:,} of {len(rows):,} rows carry no `{key}`. "
                f"That is a renamed or absent field, not a paging fault.")
    unique = len({r[key] for r in rows})
    if unique != expected:
        return (f"{endpoint}: paged {len(rows):,} rows carrying {unique:,} distinct "
                f"`{key}` values, but the API reported {expected:,} — paging "
                f"dropped or duplicated records.")
    return None


def write(name: str, rows: list[dict], meta: dict) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{name}.json"
    path.write_text(json.dumps({"meta": meta, "results": rows}, indent=None))
    print(f"  -> {path.relative_to(path.parent.parent)}  ({path.stat().st_size / 1e6:.1f} MB)")
    return path


def download(part: str = "870") -> None:
    if not PART_PATTERN.match(part):
        raise ValueError(
            f"`--part {part}` is not a three-digit CFR part. This value is "
            f"interpolated into the openFDA search expression, so anything else "
            f"changes what the query means rather than failing — devices are "
            f"parts 800-898."
        )
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"openFDA download  ({stamp})\n")

    print("classification — all records, the device-to-law backbone")
    classifications = fetch_all("classification")
    write(
        "classification",
        classifications,
        {"endpoint": "device/classification", "search": None,
         "retrieved_at": stamp, "count": len(classifications)},
    )

    search = f"openfda.regulation_number:{part}*"
    print(f"\n510k — CFR part {part}")
    clearances = fetch_all("510k", search=search)
    write(
        "510k",
        clearances,
        {"endpoint": "device/510k", "search": search,
         "retrieved_at": stamp, "count": len(clearances)},
    )

    print(f"\n{len(classifications):,} classifications + {len(clearances):,} clearances")


def main(argv: list[str] | None = None) -> int:
    # __doc__ is None under `python -OO`, which strips docstrings.
    summary = (__doc__ or "").splitlines()
    parser = argparse.ArgumentParser(description=summary[0] if summary else None)
    parser.add_argument(
        "--part", default="870",
        help="21 CFR part to scope 510(k) to (default 870, cardiovascular).",
    )
    args = parser.parse_args(argv)
    try:
        download(args.part)
    except ValueError as exc:
        print(f"\nrefused: {exc}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        # Network trouble after the retries in `fetch` are spent. A traceback
        # here reads as a bug in this script rather than as the API being
        # unreachable, which is what it actually means.
        print(f"\nopenFDA unreachable: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
