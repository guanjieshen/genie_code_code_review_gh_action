# Order

**Domain:** Sales · **Table:** `samples.tpch.orders` · **Related:** TPC-H Sample Schema, Line Item

An order is one purchase by one customer. It has one or more line items in `samples.tpch.lineitem`.

## Identifiers

| Column | Meaning |
|---|---|
| `o_orderkey` | The order identifier. Primary key. Referenced by `lineitem.l_orderkey`. |
| `o_custkey` | The customer who placed the order. Joins to `customer.c_custkey`. |

`o_orderkey` and `o_custkey` are different identifiers. Do not join customers on `o_orderkey`.

## Order total and grain

`o_totalprice` is the total price of the whole order: the sum of its line items' extended price, with tax and discount applied. **It is defined once per order.**

When a query joins `orders` to `lineitem`, each order appears once per line item. Summing `o_totalprice` after that join counts each order total as many times as the order has lines. To report order totals next to line-item data, aggregate `o_totalprice` at the order grain first, or use `COUNT(DISTINCT o_orderkey)` style logic for counts only.

## Order status

| `o_orderstatus` | Meaning |
|---|---|
| `F` | Fulfilled: every line item of the order has line status `F` |
| `O` | Open: every line item has line status `O` |
| `P` | Partially fulfilled: the line items have a mix of statuses |

**To select fulfilled orders, filter `o_orderstatus = 'F'`.** A single line item with `l_linestatus = 'F'` does not make the order fulfilled.
