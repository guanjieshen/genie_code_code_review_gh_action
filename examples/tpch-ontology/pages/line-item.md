# Line Item

**Domain:** Sales and supply · **Table:** `samples.tpch.lineitem` · **Related:** TPC-H Sample Schema, Order

A line item is one line of an order: a quantity of one part bought from one supplier.

## Keys

| Column | Meaning |
|---|---|
| `l_orderkey`, `l_linenumber` | Primary key. `l_orderkey` joins to `orders.o_orderkey`. |
| `l_partkey`, `l_suppkey` | The part and the supplier. Together they join to `partsupp` (`ps_partkey`, `ps_suppkey`). |

**Join `partsupp` on both `l_partkey` and `l_suppkey`.** A part has several suppliers, so the part key alone multiplies each line item by the number of suppliers of its part, and inflates supply cost and margin.

## Measures

| Measure | Definition |
|---|---|
| Net revenue | `l_extendedprice * (1 - l_discount)` per line item, summed |
| Supply cost | `ps_supplycost * l_quantity`, with `partsupp` joined on part and supplier keys |

## Line status

| `l_linestatus` | Meaning |
|---|---|
| `F` | The line item has shipped (finalized) |
| `O` | The line item has not shipped yet (open) |

**Line status describes one line, not the order.** An order with one shipped line and one open line is partially fulfilled (`o_orderstatus = 'P'`). To count fulfilled orders, use `orders.o_orderstatus = 'F'`, not `lineitem.l_linestatus = 'F'`.
