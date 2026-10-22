# Ontology pages for the demo

These two Unity Catalog Pages give Genie One business context that is not in table metadata:

| Page | Adds |
|---|---|
| [Net Revenue](net-revenue.md) | Business meaning of net revenue, what counts and what does not, and which assets are governed, certified, or deprecated |
| [Customer Identity](customer-identity.md) | The canonical customer ID, the separate CRM customer number, and the rule for joining them |

Technical facts (schemas, types, the metric view SQL, tags) stay in Unity Catalog. The Pages add meaning and rules.

### Import

Create one Page per file in the demo schema (Catalog Explorer > your schema > Pages), and paste the file content. Or ask Genie Code to create the Pages from these files.

Then run Lakehouse Review with `analysis-depth: detailed`. The **Analysis coverage** section shows the ontology questions, and whether Genie One answered them from these Pages.
