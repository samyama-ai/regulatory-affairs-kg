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

The first is the question this graph exists for. It is two hops, because
`regulation_number` is on the clearance record itself — an exact
government-issued join, not a name match.

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

`queries.py` holds the traversals and has **no MCP dependency**, so they are
testable without `fastmcp` installed. `server.py` is wiring only, and registers
the tools by name — a query added to `queries.py` and forgotten there fails a
test rather than being silently unavailable to an agent.

## Tests

```bash
pytest tests/test_mcp_queries.py                       # no engine needed
docker run --rm -p 8111:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
SAMYAMA_TEST_URL=http://localhost:8111 pytest tests/test_mcp_queries.py
```

**`SAMYAMA_TEST_URL`, not `SAMYAMA_URL`, and never port 8080.** The
engine-backed tests write and `DETACH DELETE` fixture nodes, and doing that
against a loaded graph breaks MERGE-key equality permanently while leaving the
node count untouched (`DATASET-CARD.md`, known issue 10). The fixture refuses to
run against an engine that already holds nodes.
