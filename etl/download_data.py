"""Download source regulatory-affairs data into ./data.

Replace stubs with real fetchers (regulatory authority registries, submission
trackers, regulation catalogs). Keep each source in its own function.
"""
from pathlib import Path
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

def download_all() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    # TODO: download_regulations(); download_submissions(); download_authorities()
    print(f"[download] wrote sources into {DATA_DIR}")

if __name__ == "__main__":
    download_all()
