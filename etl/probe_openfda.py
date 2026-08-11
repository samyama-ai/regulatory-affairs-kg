"""Probe the openFDA device endpoints — record counts and field inventory.

Every number in `docs/sources/openfda-devices.md` and in the openFDA rows of
`DATASET-CARD.md` comes from this script. Re-run it to verify them; do not
hand-edit counts into the docs.

openFDA returns `meta.results.total` on every response — the number of records
matching the query. With `limit=1` that gives an exact count for one request.

Usage:
    python -m etl.probe_openfda                 # counts for all endpoints
    python -m etl.probe_openfda --fields        # + field inventory per endpoint
    python -m etl.probe_openfda --endpoint 510k # a single endpoint
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://api.fda.gov"

# endpoint -> what it holds
ENDPOINTS = {
    "510k": "Premarket notifications (substantial-equivalence clearances)",
    "pma": "Premarket approvals (Class III)",
    "classification": "Device classification: product codes, class, regulation number",
    "registrationlisting": "Establishment registrations and device listings",
    "recall": "Device recalls",
    "enforcement": "Enforcement reports for recalls",
    "event": "MAUDE adverse event reports",
    "udi": "Unique Device Identification (GUDID)",
}

RATE_LIMIT_SECONDS = 0.4  # courtesy pause; openFDA allows 240 req/min unauthenticated


def fetch(endpoint: str, params: dict) -> dict:
    url = f"{BASE}/device/{endpoint}.json?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def total(endpoint: str) -> int | None:
    """Exact record count for an endpoint, or None if the request failed."""
    try:
        return fetch(endpoint, {"limit": 1})["meta"]["results"]["total"]
    except Exception as exc:  # noqa: BLE001 - report and continue
        print(f"  ! {endpoint}: {exc}", file=sys.stderr)
        return None


def fields(endpoint: str, sample: int = 5) -> list[str]:
    """Union of field names across a small sample of records.

    Sampled rather than taken from one record because openFDA omits empty
    fields, so a single record under-reports the schema.
    """
    try:
        results = fetch(endpoint, {"limit": sample})["results"]
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {endpoint}: {exc}", file=sys.stderr)
        return []
    names: set[str] = set()
    for record in results:
        names |= set(record.keys())
        openfda = record.get("openfda")
        if isinstance(openfda, dict):
            names |= {f"openfda.{k}" for k in openfda}
    return sorted(names)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fields", action="store_true", help="also list fields per endpoint")
    parser.add_argument("--endpoint", help="probe a single endpoint instead of all")
    args = parser.parse_args()

    targets = [args.endpoint] if args.endpoint else list(ENDPOINTS)

    print(f"{'endpoint':<22} {'records':>12}  description")
    print("-" * 92)
    grand_total = 0
    for name in targets:
        count = total(name)
        if count is not None:
            grand_total += count
        shown = f"{count:,}" if count is not None else "ERROR"
        print(f"{'device/' + name:<22} {shown:>12}  {ENDPOINTS.get(name, '')}")
        time.sleep(RATE_LIMIT_SECONDS)
    print("-" * 92)
    print(f"{'TOTAL':<22} {grand_total:>12,}")

    if args.fields:
        for name in targets:
            print(f"\n=== device/{name} fields ===")
            for field in fields(name):
                print(f"  {field}")
            time.sleep(RATE_LIMIT_SECONDS)


if __name__ == "__main__":
    main()
