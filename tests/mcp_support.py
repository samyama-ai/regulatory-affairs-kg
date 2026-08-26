"""Shared by the two MCP test modules.

Split out when `tests/test_mcp_queries.py` passed the 500-line review limit and
its engine-backed half moved to `tests/test_mcp_live.py`. All three of these
are used by both files, and a second copy of a fixture is how two test modules
end up describing different graphs while both pass.
"""

import os
import urllib.request


def configured_test_url() -> str | None:
    """Only `SAMYAMA_TEST_URL`. Never `SAMYAMA_URL`, never a default.

    A test that writes and deletes must not be able to find an engine by
    accident. Requiring its own variable means pointing these at a loaded graph
    has to be a decision somebody typed.
    """
    return os.environ.get("SAMYAMA_TEST_URL")


def engine_available(url: str) -> bool:
    try:
        urllib.request.urlopen(f"{url}/api/tenants", timeout=2).read()
        return True
    except Exception:
        return False


FIXTURE = [
    "CREATE (r:Regulation {cfr_section: '870.5150', source: 'test'})",
    "CREATE (p:ProductCode {product_code: 'DXY', device_class: '2', "
    "definition: 'A test category', medical_specialty: 'CV', source: 'test'})",
    # A product code carrying an APOSTROPHE. Every engine-backed test used
    # quote-free fixture values, which is why "verified against a fresh 1.1.0"
    # did not cover the path the old backslash escaping broke.
    "CREATE (p:ProductCode {product_code: \"O'BRIEN\", device_class: '3', "
    "definition: \"A category with an apostrophe\", medical_specialty: 'CV', "
    "source: 'test'})",
    "MATCH (p:ProductCode {product_code: \"O'BRIEN\"}), "
    "(r:Regulation {cfr_section: '870.5150'}) CREATE (p)-[:GOVERNED_BY]->(r)",
    "CREATE (s:Submission {id: 'K999001', device_name: 'A test device', "
    "applicant: 'Acme', decision_date: '2024-01-02', "
    "advisory_committee: 'Cardiovascular', source: 'test'})",
    "MATCH (p:ProductCode {product_code: 'DXY'}), (r:Regulation {cfr_section: '870.5150'}) "
    "CREATE (p)-[:GOVERNED_BY]->(r)",
    "MATCH (s:Submission {id: 'K999001'}), (p:ProductCode {product_code: 'DXY'}) "
    "CREATE (s)-[:CLASSIFIED_AS]->(p)",
]
