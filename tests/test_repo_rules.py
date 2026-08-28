"""The two rules that hold across the whole repo, not inside one module.

Split out of `tests/test_mcp_engine.py`, which tests the MCP transport: these
cover four modules in three packages and belong to none of them.

Both are read from the AST rather than from source text. Every earlier version
of both was a substring check, and each was weaker than the sentence above it
claimed — one matched its own docstring, one matched the word "exception", and
one would have passed a hand-rolled URL built by concatenation. A guard on a
repo-wide rule that can be satisfied by prose is worse than none, because the
rule reads as enforced.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess

import pytest


def clients() -> list[str]:
    """Modules that must not build the request. DISCOVERED, never listed.

    The docstring below says "a fifth module that hand-rolls one is the
    failure", and a hardcoded list cannot see a fifth module at all — so the
    claim was not enforced by the test making it.

    Everything tracked is a candidate; `tests/` is excluded because a test may
    legitimately fake a request in order to drive the transport.
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo(),
                         capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return sorted(n for n in out.stdout.split("\0")
                  if n.endswith(".py") and not n.startswith("tests/"))


def repo() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def code_strings(tree: ast.AST) -> list[str]:
    """Every string CONSTANT that is not a docstring, folded through f-strings.

    Docstrings are excluded by walking the bodies that can hold one and
    dropping the leading expression, rather than by matching on content —
    prose explaining `/api/query` is documentation, and a docstring that
    fails an architectural check is a guard punishing the explanation.

    f-strings and `+` concatenation are folded, because a URL assembled from
    pieces is exactly how a hand-rolled request would come back: `url + "/api"
    + "/query"` contains neither `/api/query` as a literal nor any clue in a
    substring scan.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))

    def folded(node) -> str:
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else ""
        if isinstance(node, ast.JoinedStr):
            return "".join(folded(v) for v in node.values)
        if isinstance(node, ast.FormattedValue):
            return "\x00"          # a value, never part of a path
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return folded(node.left) + folded(node.right)
        return ""

    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.JoinedStr, ast.BinOp)):
            out.append(folded(node))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                out.append(node.value)
    return out


def calls_named(tree: ast.AST, name: str, module: str) -> bool:
    """Is `name`, IMPORTED FROM `module`, actually called here?

    Both halves matter. Matching any callee with that name counts a local
    helper someone happened to give the same name — the check would pass for
    a module that defines its own `post_query` and never talks to the shared
    one, which is the opposite of what it asserts. And matching the import
    alone counts a module that imports it and never calls it.
    """
    imported = any(
        isinstance(node, ast.ImportFrom) and node.module == module
        and any(alias.name == name for alias in node.names)
        for node in ast.walk(tree))
    if not imported:
        # Reached through the module instead — `transport.post_query(...)`.
        imported = any(
            isinstance(node, ast.Import)
            and any(alias.name == module for alias in node.names)
            for node in ast.walk(tree))
        if not imported:
            return False

    return any(
        isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == name
             or getattr(node.func, "attr", None) == name)
        for node in ast.walk(tree))


def test_no_client_builds_the_request_itself():
    """Four modules constructed an identical `/api/query` POST, and a fix has
    already landed in one of them and not another — `benchmarks/measure.py`
    carries a comment saying so about a sibling it cannot enforce.

    What is asserted is that nobody builds the request themselves. A fifth
    module that hand-rolls one is the failure, whatever it does afterwards.
    """
    offenders, senders = [], set()
    for name in clients():
        if name == "etl/transport.py":
            continue          # the sender itself, which of course builds one
        tree = ast.parse((repo() / name).read_text(encoding="utf-8"))
        if any("/api/query" in text for text in code_strings(tree)):
            offenders.append(name)
        if calls_named(tree, "post_query", "etl.transport"):
            senders.add(name)

    assert not offenders, (
        f"{offenders} build the /api/query request themselves. The request is "
        f"`etl.transport.post_query`; what a FAILURE means stays with each "
        f"caller, which is the part that genuinely differs.")

    # A SET, and a floor rather than an equality. `senders == CLIENTS` failed
    # on a reordering, which is not a defect — and it also asserted that
    # exactly those four talk to the engine, which stops being true the moment
    # a fifth legitimately does.
    #
    # What this is for: the scan above passes for a module that stopped
    # talking to the engine altogether, so it is not evidence on its own.
    assert len(senders) >= 4, (
        f"only {sorted(senders)} call post_query. The scan above passes for a "
        f"module that no longer talks to the engine at all, so it needs this "
        f"beside it.")


def test_the_shared_sender_decides_nothing_about_failure():
    """The half that must NOT be shared.

    The loader retries and raises, the MCP tools return a `Result` an agent
    can read, the benchmark runner exits, the demo prints in colour and stops.
    A shared helper that decided what a failure meant would be a fifth policy
    rather than the end of the duplication.

    Asserted on the AST. `"except" not in inspect.getsource(...)` matched the
    docstring, and the word "exception" in it, so the guard was satisfied by
    its own explanation of what it forbade.
    """
    tree = ast.parse((repo() / "etl" / "transport.py").read_text(encoding="utf-8"))
    # `ExceptHandler` only. `ast.Try` also matches a bare `try/finally`, which
    # handles nothing — it guarantees cleanup — so the scan failed a shape it
    # does not forbid, under a message saying "handles an exception". A guard
    # whose message misdescribes what it caught sends the next reader to the
    # wrong place.
    handlers = [node for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler)]
    assert not handlers, (
        f"etl/transport.py catches "
        f"{[ast.unparse(h.type) if h.type else 'everything' for h in handlers]}. "
        f"Four callers want four different things on failure; a sender that "
        f"decides for them replaces four policies with a fifth. A bare "
        f"`try/finally` is fine — it decides nothing.")
