# Ontology validation

Some business meaning lives outside table metadata: what a metric means, which identifier is canonical for an entity, and how two tables relate. With `analysis-depth: detailed`, Lakehouse Review checks a pull request against **the definitions your workspace already has**, by asking **Genie One**. Genie One reads your Unity Catalog Pages, metric views, and certified datasets.

**Lakehouse Review does not ship or create an ontology.** It uses whatever your teams have documented. If there is nothing relevant, those questions are reported as not answered and the rest of the review runs as usual.

> **Why it matters:** these checks catch errors that pass every type check, such as joining customers to orders on the order key when both are `BIGINT`. Only a documented relationship reveals the error.

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
2. **An answer is authoritative only when it cites a governed source that is relevant to the question**: a Page whose title names the entity or the schema under review and that the same answer ties to that schema, or a metric view or certified dataset in the same schema. An uncited answer is a hypothesis and cannot create a high-confidence finding on its own.
3. **Genie Code gives a verdict** for every answered question: `conflict`, `no_conflict`, or `unclear`. A missing verdict is reported as unresolved, and a conflict always becomes a finding.

The **Analysis coverage** section of the comment shows how many questions were planned, answered by Unity Catalog, asked, answered authoritatively, and how the verdicts came out.

## What the reviewer needs

- `analysis-depth: detailed`, and Genie One allowed for service principals in the workspace ([setup](setup.md#3-allow-genie-code-genie-one-and-pages)).
- **Access to your Pages.** Genie One answers with the reviewer's permissions, so a Page the reviewer cannot open is not used, even if you can see it.

## Turning it off

- `analysis-depth: rapid` (the default) plans the questions but does not ask them. The comment then says that business relationships and definitions were not validated.
- `semantic: off` turns off Genie One completely.
- `ontology.enabled: false` in the config file turns off question planning.
