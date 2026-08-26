"""Shared by the two MCP test modules.

Split out when `tests/test_mcp_queries.py` passed the 500-line review limit and
its engine-backed half moved to `tests/test_mcp_live.py`. All three of these
are used by both files, and a second copy of a fixture is how two test modules
end up describing different graphs while both pass.
"""

import ast
import json
import os
import pathlib
import urllib.request

from mcp_server import engine


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


ROOT = pathlib.Path(__file__).resolve().parents[1]


def server_tools() -> tuple[str, ...]:
    """`TOOLS` as `server.py` declares it, read without importing the module.

    `mcp_server/server.py` imports `fastmcp`, which is an optional extra and
    is not installed for this suite — the whole point of keeping the queries
    in a module with no MCP dependency. So the tuple is parsed out of the
    source rather than imported.
    """
    tree = ast.parse((ROOT / "mcp_server" / "server.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "TOOLS" for t in node.targets):
            return tuple(el.value for el in node.value.elts)
    raise AssertionError("server.py no longer declares a TOOLS tuple")


def queries_that_run() -> set[str]:
    """Every public function in `queries.py` that sends a query.

    Discovered, not listed: a list here would be the fourth copy of the same
    eight names, and the drift being guarded is precisely a name that exists
    in one place and not another.
    """
    source = (ROOT / "mcp_server" / "queries.py").read_text()
    tree = ast.parse(source)
    found = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        if node.name == "run":
            continue  # the transport itself, not a tool
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "run":
                found.add(node.name)
                break
    return found


def serve(monkeypatch, payload):
    """Make the engine answer with `payload`, without an engine.

    Shared because both MCP test modules fake the same transport, and a
    second copy is how two test files end up faking different engines
    while both pass.
    """
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(payload).encode()
    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda *a, **k: R())
