"""Shared by the two MCP test modules.

Split out when `tests/test_mcp_queries.py` passed the 500-line review limit and
its engine-backed half moved to `tests/test_mcp_live.py`. All three of these
are used by both files, and a second copy of a fixture is how two test modules
end up describing different graphs while both pass.
"""

import ast
import inspect
import json
import os
import pathlib
import textwrap
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
    # `regulation_number` is on the record because the loader writes it
    # (etl/load_openfda.py) and the clearances tool's contract is about the
    # relationship between it and the product-code traversal. A fixture without
    # it cannot exercise that contract at all — which is why the test asserting
    # the two joins agree could not be written until it was here.
    "CREATE (s:Submission {id: 'K999001', device_name: 'A test device', "
    "applicant: 'Acme', decision_date: '2024-01-02', "
    "regulation_number: '870.5150', "
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


def return_aliases(cypher: str) -> list[str]:
    """The column names a statement's own RETURN clause produces.

    A fake engine that hardcodes its columns cannot fail when the query's
    aliases change. Renaming `count(DISTINCT s) AS total` to `AS c` left the
    fake still answering a column called `total`, so the tool read its figure
    out of a column the real engine would no longer send, and the suite stayed
    green. Columns derived from the statement under test cannot drift away
    from it.

    Deliberately a small parser rather than a full one: these are the repo's
    own statements, and anything it cannot read it refuses instead of
    returning an empty list, which would fake a result with no columns at all.
    """
    _, sep, tail = cypher.rpartition("RETURN ")
    if not sep:
        raise AssertionError(f"no RETURN clause to read columns from: {cypher!r}")
    for stop in (" ORDER BY ", " LIMIT ", " SKIP "):
        tail = tail.split(stop)[0]

    names, depth, item = [], 0, ""
    for ch in tail:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            names.append(item)
            item = ""
        else:
            item += ch
    names.append(item)

    columns = []
    for name in names:
        name = name.strip()
        lowered = name.lower()
        if " as " in lowered:
            name = name[lowered.rindex(" as ") + 4:].strip()
        else:
            name = name.split(".")[-1].strip()
        if not name:
            raise AssertionError(f"unreadable RETURN item in {cypher!r}")
        columns.append(name)
    return columns


def cypher_text(node) -> str:
    """One Cypher statement as text, from the AST node that spells it.

    Interpolated values become `?`: a value is not a keyword, and rendering it
    as one would let `f"...{clause}"` satisfy a check for a clause the
    statement does not contain.
    """
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.JoinedStr):
        return "".join(cypher_text(part) for part in node.values)
    if isinstance(node, ast.FormattedValue):
        return " ? "
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return cypher_text(node.left) + cypher_text(node.right)
    raise AssertionError(
        f"a statement is built in a way this reader cannot follow "
        f"({type(node).__name__}); it would be skipped rather than checked")


def statements_of(function) -> list[str]:
    """Every Cypher statement a tool passes to `run()`, read from the AST.

    Not from the source text. The first version of the ordering check scanned
    raw source and was defeated by its own explanatory comment, which contains
    the words it looked for. Stripping `#` comments closed that and left the
    docstrings, so writing "ORDER BY" into prose still passed a query that had
    lost the clause. The AST sees only the argument actually sent.

    Refuses rather than returns nothing: a tool whose statements cannot be
    found is a tool no check ran against, and an empty list reads as a pass.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "run"
                and node.args):
            found.append(cypher_text(node.args[0]))
    if not found:
        raise AssertionError(
            f"no statement found in {function.__name__}; a check over an "
            f"empty list passes without reading anything")
    return found
