# Ontology validation

Some business meaning lives outside table metadata: what a metric means, which identifier is canonical for an entity, and how two tables relate. With `analysis-depth: detailed`, Lakehouse Review checks a pull request against those definitions by asking **Genie One**, which reads your Unity Catalog Pages, metric views, and certified datasets.

> **Why it matters:** these checks catch errors that pass every type check, such as joining customers to orders on the order key (both `BIGINT`). In the [evaluation](ontology-evaluation.md), detailed analysis found 4 of 4 planted errors; without it, the review found none.

## Which questions are asked

Lakehouse Review decides which questions a pull request raises from what it touches. It does not ask open-ended questions.

| The pull request… | Question | Check |
| --- | --- | --- |
| Newly joins two tables, or joins them on different keys | What is the documented relationship, and which keys must be used? | `ontology.identifier-relationship` |
| Joins two identifiers of the same entity, such as `customer_id` and `cust_id` | What is the canonical identifier, and how do these relate? | `ontology.identifier-relationship` |
| Sums a measure after joining a finer-grained table | At what grain is the measure defined? Does this double count? | `correctness.aggregation-grain` |
| Filters on a status, flag, or type code to define an output | What does the code mean? Is it the right column? | `semantic.filter-meaning` |
| Writes a metric-like column, such as revenue or margin | What is the governed definition, and which asset is authoritative? | `semantic.metric-definition-drift` |
| Reads a metric-like table with no certified or deprecated signal | Is this the authoritative source? | `semantic.non-authoritative-source` |

Questions that Unity Catalog already answers, such as a metric view that defines the measure or a `certified` or `deprecated` tag, are **not sent anywhere**. The rest go to Genie One, riskiest first, up to `ontology.max_questions` (default 6).

## How answers are used

1. **Answers become evidence** with their citations: Pages, tables, metric views, and the queries Genie One ran.
2. **An answer is authoritative only when it cites a governed source** that is relevant to the question. An uncited answer is a hypothesis and cannot create a high-confidence finding on its own.
3. **Genie Code gives a verdict** for every answered question: `conflict`, `no_conflict`, or `unclear`. A missing verdict is reported as unresolved, and a conflict always becomes a finding.

The **Analysis coverage** section of the comment shows how many questions were planned, answered by Unity Catalog, asked, answered authoritatively, and how the verdicts came out.

## Writing Pages the reviewer can use

A Page counts as authoritative evidence only when Genie One cites it, its title matches the question, and the same answer ties it to the schema under review. These guidelines make that likely:

| Do this | Why |
| --- | --- |
| **Name the entity or the schema in the title**, for example "Order" or "Sales schema" | Relevance is decided by the title. A Page named "Notes" is never matched. |
| For metric definitions, **name the schema or domain in the title**, for example "Net revenue (sales)" | Definition titles such as "Net Revenue" exist in many domains, so for metrics the title must name the reviewed schema. |
| **Mention the tables by full name**, for example `main.sales.orders` | Genie One then cites the table next to the Page, which ties the Page to the schema. |
| State **keys and relationships** explicitly, for example "Orders relate to customers on `o_custkey = c_custkey`" | Join questions are answered from these sentences. Include composite keys. |
| State the **grain** of each fact table and measure, for example "`o_totalprice` is defined once per order" | Grain questions are answered from these sentences. |
| List the **meaning of codes**, for example "`l_linestatus`: `F` = line finalized; it is not the order status" | Filter questions are answered from these sentences. |
| **Share the Page with the reviewer service principal** | A Page the reviewer cannot open is not used, even if you can see it. |

See [`examples/tpch-ontology/pages/`](../examples/tpch-ontology/pages/) for Pages written this way.

## Turning it off

- `analysis-depth: rapid` (the default) plans the questions but does not ask them. The comment then says that business relationships and definitions were not validated.
- `semantic: off` turns off Genie One completely.
- `ontology.enabled: false` in the config file turns off question planning.
