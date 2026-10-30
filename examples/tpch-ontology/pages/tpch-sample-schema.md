# TPC-H Sample Schema

**Domain:** Sales and supply · **Tables:** `samples.tpch` · **Related:** Order, Line Item

The `samples.tpch` schema is the TPC-H benchmark data set: customers place orders, each order has line items, and each line item is a part bought from one supplier.

## Tables and keys

| Table | Entity | Primary key | Grain |
|---|---|---|---|
| `samples.tpch.region` | Region | `r_regionkey` | One row per region |
| `samples.tpch.nation` | Nation | `n_nationkey` | One row per nation |
| `samples.tpch.customer` | Customer | `c_custkey` | One row per customer |
| `samples.tpch.orders` | Order | `o_orderkey` | One row per order |
| `samples.tpch.lineitem` | Line item | `l_orderkey`, `l_linenumber` | One row per line of an order |
| `samples.tpch.part` | Part | `p_partkey` | One row per part |
| `samples.tpch.supplier` | Supplier | `s_suppkey` | One row per supplier |
| `samples.tpch.partsupp` | Part supplier | `ps_partkey`, `ps_suppkey` | One row per part **and** supplier |

## Relationships and join rules

| From | To | Join on |
|---|---|---|
| `orders` | `customer` | `o_custkey = c_custkey` |
| `lineitem` | `orders` | `l_orderkey = o_orderkey` |
| `lineitem` | `partsupp` | `l_partkey = ps_partkey AND l_suppkey = ps_suppkey` |
| `lineitem` | `part` | `l_partkey = p_partkey` |
| `lineitem` | `supplier` | `l_suppkey = s_suppkey` |
| `customer` | `nation` | `c_nationkey = n_nationkey` |
| `supplier` | `nation` | `s_nationkey = n_nationkey` |
| `nation` | `region` | `n_regionkey = r_regionkey` |

- **Customers relate to orders only through `o_custkey`.** `o_orderkey` identifies an order, not a customer; joining `c_custkey` to `o_orderkey` matches unrelated rows even though both are `BIGINT`.
- **`partsupp` has a composite key.** Each part is offered by several suppliers, so joining `lineitem` to `partsupp` on the part key alone returns one row per supplier of the part and multiplies the line item. Always join on both the part key and the supplier key.

## Metrics

| Metric | Definition | Grain |
|---|---|---|
| Net revenue | `SUM(l_extendedprice * (1 - l_discount))` | Summed from line items |
| Order total | `o_totalprice`, already the total of the order's line items including tax and discount | Once per order |
| Supply cost | `SUM(ps_supplycost * l_quantity)`, joining `partsupp` on part and supplier keys | Summed from line items |
