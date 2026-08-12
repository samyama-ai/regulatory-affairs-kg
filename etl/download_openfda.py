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


def fetch(endpoint: str, *, search: str | None = None, limit: int = 1, skip: int = 0) -> dict:
    """One request. Raises on anything that is not a clean 200 with results."""
    params = {"limit": limit, "skip": skip}
    if search:
        params["search"] = search
    url = f"{API}/device/{endpoint}.json?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
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
    """
    count = total(endpoint, search)
    if count > SKIP_CAP:
        raise ValueError(
            f"{endpoint} matches {count:,} records, over openFDA's {SKIP_CAP:,} "
            f"skip cap. Narrow the filter or use bulk downloads — do not load a "
            f"truncated set and call it complete."
        )
    rows: list[dict] = []
    while len(rows) < count:
        page = fetch(endpoint, search=search, limit=PAGE, skip=len(rows))["results"]
        if not page:
            break
        rows.extend(page)
        print(f"  {endpoint}: {len(rows):,}/{count:,}", end="\r", flush=True)
    print(f"  {endpoint}: {len(rows):,}/{count:,}      ")
    return rows


def write(name: str, rows: list[dict], meta: dict) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{name}.json"
    path.write_text(json.dumps({"meta": meta, "results": rows}, indent=None))
    print(f"  -> {path.relative_to(path.parent.parent)}  ({path.stat().st_size / 1e6:.1f} MB)")
    return path


def download(part: str = "870") -> None:
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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
