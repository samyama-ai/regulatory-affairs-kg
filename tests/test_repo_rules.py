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

CLIENTS = ["benchmarks/measure.py", "demo/demo.py", "etl/load_openfda.py",
           "mcp_server/engine.py"]


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


def calls_named(tree: ast.AST, name: str) -> bool:
    """Is `name` actually CALLED anywhere — not merely mentioned."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = getattr(func, "id", None) or getattr(func, "attr", None)
            if called == name:
                return True
    return False


def test_no_client_builds_the_request_itself():
    """Four modules constructed an identical `/api/query` POST, and a fix has
    already landed in one of them and not another — `benchmarks/measure.py`
    carries a comment saying so about a sibling it cannot enforce.

    What is asserted is that nobody builds the request themselves. A fifth
    module that hand-rolls one is the failure, whatever it does afterwards.
    """
    offenders, senders = [], []
    for name in CLIENTS:
        tree = ast.parse((repo() / name).read_text(encoding="utf-8"))
        if any("/api/query" in text for text in code_strings(tree)):
            offenders.append(name)
        if calls_named(tree, "post_query"):
            senders.append(name)

    assert not offenders, (
        f"{offenders} build the /api/query request themselves. The request is "
        f"`etl.transport.post_query`; what a FAILURE means stays with each "
        f"caller, which is the part that genuinely differs.")
    assert senders == CLIENTS, (
        f"only {senders} call post_query. The check above passes for a module "
        f"that stopped talking to the engine altogether, so it is not evidence "
        f"on its own.")


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
    handlers = [node for node in ast.walk(tree)
                if isinstance(node, (ast.ExceptHandler, ast.Try))]
    assert not handlers, (
        "etl/transport.py handles an exception. Four callers want four "
        "different things on failure; a sender that decides for them replaces "
        "four policies with a fifth.")
