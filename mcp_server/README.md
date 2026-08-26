# MCP server

Eight tools over the loaded graph. Every one runs a traversal that
`demo/demo.py` already proves works on real FDA data.

```bash
docker run --rm -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
python -m etl.download_openfda && python -m etl.load_openfda
python -m mcp_server.server
```

## The tools

| Tool | Answers |
|---|---|
| `clearances_under_regulation` | **a rule changes — which clearances are affected** |
| `count_clearances_under_regulation` | how many, uncapped |
| `regulations_for_product` | which rules a product code must comply with |
| `regulation_for_clearance` | which rule governs one clearance |
| `busiest_regulations` | where a change would hurt most |
| `clearances_by_advisory_committee` | clearances by reviewing authority |
| `product_codes_by_class` | device categories at one risk class |
| `graph_provenance` | what is in the graph and where it came from |

The first is the question this graph exists for. It is two hops, **through the
product code**:

    Regulation <-[:GOVERNED_BY]- ProductCode <-[:CLASSIFIED_AS]- Submission

This text used to say the join ran on the clearance's own `regulation_number`.
It does not — the loader writes that property and no tool reads it. Both are
exact joins on government-issued codes, but they are different paths, and a
clearance whose product code carries no regulation edge is invisible to this
one.

Measured on the loaded graph: all **19,127** clearances are reachable this way,
and the regulation the traversal reaches equals the `regulation_number` on the
record for every one of them — **0 disagreements**. 902 product codes carry no
regulation edge, but no clearance classifies as one of them. A live test
asserts that equivalence, so the day it stops holding the suite says so rather
than the answer quietly shrinking.

## Two tools are separate on purpose

`clearances_under_regulation` is `LIMIT`ed and `count_clearances_under_regulation`
is not. Reading the length of a capped list as the total would understate the
blast radius of a rule change, which is the one number this graph exists to get
right.

## An empty answer is not a failed one

Every tool returns an `error` field alongside its rows. An agent cannot act on
`[]` if it means both *"no clearances"* and *"the engine is down"* — and a 500
from a running engine must not be reported as "no engine", which sends someone
to restart a container that is already up.

## What is deliberately absent

**`pending_by_authority`.** A previous version declared it and returned `[]`.
It is not answerable: openFDA publishes *decided* clearances — every record
carries `decision_date` and `decision_code` — and there is no pending queue in
the public data, no `SUBMITTED_TO` edge in the schema and no `status` property
on `Submission`. `clearances_by_advisory_committee` is the nearest honest
thing, since the advisory committee **is** the FDA's reviewing authority.

**Predicate chains.** `PredicateClaim` and `CITES_PREDICATE` are in the schema,
but the loader does not build them — they need the 510(k) Summary PDF
extractor, which does not exist yet. A tool for them would be a second stub.

## Layout

Three modules, and **none of them has an MCP dependency except `server.py`** —
so every traversal is testable without `fastmcp` installed.

| | |
|---|---|
| `engine.py` | the transport and the literal encoder: where the graph is, how a statement is sent, what a failure comes back as, and how a Python value becomes Cypher an engine with no escape sequences can parse |
| `queries.py` | the eight traversals, and nothing else |
| `server.py` | wiring only — it registers the tools by name |

`queries.py` was one file until it passed the 500-line review limit; review
skips a file over it, so an oversized module is an unread one.

A query added to `queries.py` and forgotten in `server.py` fails a test. That
claim used to be false: the guarding test iterated its own hardcoded copy of
the tool list, so a query missing from both passed. The expected set is
derived from `queries.py` now — every public function that calls `run` — and
compared against the tuple `server.py` declares.

## Tests

```bash
pytest tests/test_mcp_engine.py tests/test_mcp_queries.py   # no engine needed

docker run --rm -p 8111:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
SAMYAMA_TEST_URL=http://localhost:8111 pytest tests/test_mcp_live.py
```

Note the second file: `tests/test_mcp_live.py` is where the engine-backed
tests live. Setting `SAMYAMA_TEST_URL` and running `tests/test_mcp_queries.py`
sets a variable that file ignores and runs no engine test at all.

**`SAMYAMA_TEST_URL`, not `SAMYAMA_URL`, and never port 8080.** The
engine-backed tests write and `DETACH DELETE` fixture nodes, and doing that
against a loaded graph breaks MERGE-key equality permanently while leaving the
node count untouched (`DATASET-CARD.md`, known issue 10). The fixture refuses to
run against an engine that already holds nodes.
