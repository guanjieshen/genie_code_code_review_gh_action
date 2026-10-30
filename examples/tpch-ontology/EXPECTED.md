# TPC-H ontology demo: expected findings

The pull request adds two dashboard tables. Every key is `BIGINT`, so schema and type checks pass. The errors contradict the rules in the **TPC-H Sample Schema**, **Order**, and **Line Item** Pages (see [`pages/`](pages/)).

| # | Issue | Where | Business rule |
|---|---|---|---|
| 1 | `customer` joined to `orders` on `c_custkey = o_orderkey` | `src/segment_order_revenue.sql` | Customers relate to orders on `c_custkey = o_custkey` |
| 2 | `lineitem` joined to `partsupp` on the part key only | `src/supplier_margin.sql` | `partsupp` joins on part **and** supplier keys; the part key alone multiplies rows |
| 3 | `o_totalprice` summed after joining line items | `src/segment_order_revenue.sql` | An order total is defined once per order |
| 4 | `l_linestatus = 'F'` used to count fulfilled orders | `src/segment_order_revenue.sql` | Line status is not order status; fulfilled orders have `o_orderstatus = 'F'` |

With `analysis-depth: detailed` and the Pages shared with the reviewer, each issue should appear as a high-confidence finding that cites a Page, with a `conflict` verdict. With `analysis-depth: rapid`, the comment notes that business relationships and definitions were not validated.
