"""The nine queries this suite measures, and why each one is here.

Split out of `benchmarks/run_queries.py` when it passed the 500-line review
limit — review skips a file over it and blocks the PR, so an oversized file is
an unread one.

Split by SUBJECT rather than by length: this is the catalogue, which is data —
a question, the Cypher that answers it, and the argument for measuring it.
`run_queries.py` is the runner: talking to the engine, timing, the index
experiment, and rendering the page. The two change for different reasons and on
different days.

Each entry carries a `why` as well as a `cypher`, because a timing with no
argument for the shape being timed is a number nobody can act on.
"""

QUERIES: list[dict] = [
    {
        "name": "Change impact — every clearance under one rule",
        "question": "A rule changes. Which clearances are affected?",
        "why": ("The question this graph exists for. Two hops, THROUGH THE "
                "PRODUCT CODE — this text used to say the join runs on the "
                "clearance's own `regulation_number`, and the Cypher below has "
                "never touched that property. Both are exact joins on "
                "government-issued codes, but they are different paths. "
                "Measured on the loaded graph: all {submissions:,} clearances are "
                "reachable this way and the regulation reached matches the one "
                "on the record every time. In a relational schema "
                "this is the query that needs the join written by hand each time "
                "the shape of the question changes."),
        "cypher": ("MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)"
                   "<-[:CLASSIFIED_AS]-(s:Submission) "
                   "WHERE r.cfr_section = '870.5150' "
                   "RETURN count(s) AS clearances"),
    },
    {
        "name": "Change impact — the affected clearances themselves",
        "question": "Which specific devices are affected, and whose are they?",
        "why": "The same traversal, returning rows rather than a count.",
        "cypher": ("MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)"
                   "<-[:CLASSIFIED_AS]-(s:Submission) "
                   "WHERE r.cfr_section = '870.5150' "
                   "RETURN s.id AS clearance, s.applicant AS applicant, "
                   "s.decision_date AS decided "
                   "ORDER BY s.decision_date DESC LIMIT 5"),
    },
    {
        "name": "Busiest regulations",
        "question": "Where would a rule change hurt most?",
        "why": ("An aggregation across the whole graph. Answers which rules carry "
                "the most clearances, which is where regulatory attention goes."),
        "cypher": ("MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)"
                   "<-[:CLASSIFIED_AS]-(s:Submission) "
                   "RETURN r.cfr_section AS cfr_section, count(s) AS clearances "
                   "ORDER BY clearances DESC LIMIT 10"),
    },
    {
        "name": "Device to law",
        "question": "Which rules must this product code comply with?",
        # Most product codes carry no `definition` — `shape()` counts how many
        # and the page states the figure, so it is never typed here. A blank
        # cell in the rendered table is missing source data, not a parse
        # failure, and the page says so rather than leaving a reader to guess.
        "why": ("One hop from the join hub. `product_code` is on every openFDA "
                "endpoint; device *names* are not consistent between them, so a "
                "name is never the key."),
        "cypher": ("MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) "
                   "WHERE p.product_code = 'DXY' "
                   "RETURN p.definition AS device_category, p.device_class AS class, "
                   "r.cfr_section AS cfr_section"),
    },
    {
        "name": "Law to device categories",
        "question": "Which device categories does one rule govern?",
        "why": "The reverse of the above, and the first half of a change-impact answer.",
        "cypher": ("MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) "
                   "WHERE r.cfr_section = '870.5150' "
                   "RETURN p.product_code AS product_code, p.definition AS category "
                   "LIMIT 10"),
    },
    {
        "name": "Clearance to rule",
        "question": "Which rule governs this one clearance?",
        "why": "A point lookup through two hops — the query an inspector runs.",
        "cypher": ("MATCH (s:Submission)-[:CLASSIFIED_AS]->(p:ProductCode)"
                   "-[:GOVERNED_BY]->(r:Regulation) "
                   "WHERE s.id = 'K233820' "
                   "RETURN s.device_name AS device, p.product_code AS product_code, "
                   "r.cfr_section AS cfr_section"),
    },
    {
        "name": "Clearances by reviewing authority",
        "question": "Which FDA advisory committees review the most clearances?",
        "why": ("A grouping over {submissions:,} submissions. **The distribution is an "
                "artifact of the slice, not a finding about the FDA**: this "
                "graph holds 21 CFR part 870 clearances, so Cardiovascular "
                "leads by construction. What the query demonstrates is the "
                "grouping, not the ranking. Note also this is *decided* "
                "clearances — openFDA publishes no pending queue, so 'pending "
                "by authority' has no answer in this data."),
        "cypher": ("MATCH (s:Submission) WHERE s.advisory_committee IS NOT NULL "
                   "RETURN s.advisory_committee AS committee, count(s) AS clearances "
                   "ORDER BY clearances DESC LIMIT 10"),
    },
    {
        "name": "High-risk device categories with a rule attached",
        "question": "How many Class III categories carry a regulation in this slice?",
        "why": ("A filtered scan with a join — the population a reviewer starts "
                "from. It is NOT the count of Class III categories: this slice "
                "holds {class_three:,}, and only the ones with a GOVERNED_BY edge are "
                "joinable here."),
        # `count(DISTINCT p)`, not `count(p)`. The bare form counts PATTERN
        # MATCHES, so a product code governed by two regulations would be
        # counted twice. Measured: at most one regulation per product code, so
        # both forms agree today — the distinct form is correct by construction
        # rather than by a property of the current data.
        #
        # The column name says what the JOIN counts, not what "Class III" means.
        # It read `class_three_categories`, which a reader takes as the number
        # of Class III device categories. `shape()` measures both the population
        # and the joinable subset, and the commentary names them rather than
        # typing them — reporting the subset under the population's name
        # understated it by nearly four times.
        "cypher": ("MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) "
                   "WHERE p.device_class = '3' "
                   "RETURN count(DISTINCT p) AS class_three_with_a_regulation"),
    },
    {
        "name": "Provenance",
        "question": "What is in this graph, and where did it come from?",
        "why": ("The question a reviewer asks before trusting any answer above. "
                "Every node carries the openFDA endpoint it was built from."),
        "cypher": ("MATCH (n) WHERE n.source IS NOT NULL "
                   "RETURN n.source AS source, count(n) AS nodes "
                   "ORDER BY nodes DESC"),
    },
]
