# Ontology evaluation

Lakehouse Review was evaluated on a pull request against `samples.tpch` with four planted errors. Every key involved is `BIGINT`, so schema and type checks pass. The errors contradict relationships documented in existing Unity Catalog Pages (TPC-H Sample Schema, Order, Line Item).

| # | Planted error | Rule it breaks |
|---|---|---|
| 1 | `customer` joined to `orders` on `c_custkey = o_orderkey` | Customers relate to orders on `c_custkey = o_custkey` |
| 2 | `lineitem` joined to `partsupp` on part key only | `partsupp` joins on part **and** supplier keys |
| 3 | `o_totalprice` summed after joining line items | An order total is defined once per order |
| 4 | `l_linestatus = 'F'` used to count fulfilled orders | Line status is not order status |

### Method

Double-blind A/B: `analysis-depth: detailed`, ontology on vs off, two runs each, same commit and same Unity Catalog evidence snapshot. Reports were shuffled under random labels and redacted (no tool, model, or coverage text). The label mapping was sealed until an independent judge had scored each report against the answer key.

### Results (final round)

| Variant | Runs | Issues caught (of 4) | False positives | Judge quality (1-5) | Time per review |
|---|---|---|---|---|---|
| Ontology on | 2 | 4, 4 | 0 | 5, 5 | ~100-120 s |
| Ontology off | 2 | 0, 0 | 0 | 1, 1 | ~15 s |

With the ontology on, both runs reported the same four high-confidence findings, each backed by a cited TPC-H Page and Genie One's own query results, with a `conflict` verdict for each. Without it, the review found nothing: the code is type-correct, so only the documented business relationships reveal the errors.

### What changed across rounds

| Round | Ontology on | Fix that followed |
|---|---|---|
| 1 | 2 of 4 | Questions for aggregation grain and filter meaning; budget 6; ranking by risk |
| 2 | 2 of 4 | A page is tied to the reviewed schema by Genie One's queries too, not only by links |
| 3 | 3 of 4 | Fixed a Genie One client parsing bug; verdicts required for every answered question |
| 4 | 3-4 of 4 | Findings linked to the answers they rely on; definition pages must name the reviewed schema |
| 5 | **4 of 4** | The PR diff is passed to the reviewer, so it never judges without reading the code |

### Limitations

Two runs per variant on one pull request. Genie One latency (about 35-60 s per question, asked in parallel) dominates review time in `detailed` mode. Genie One does not report token usage, so cost was not measured.
