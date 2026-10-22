# Customer Identity

**Domain:** Customer · **Owner:** Customer Data Platform · **Related:** Net Revenue

## Customer

A customer is a person or business that places orders. Every customer has exactly one canonical customer identifier and at most one CRM customer number.

## Identifiers

| Identifier | System | Type | Where it appears |
|---|---|---|---|
| Canonical customer ID | Order platform | Number (`BIGINT`) | All order and revenue data. In `silver_orders` it is the column `cust_id` (being renamed to `customer_id`). |
| CRM customer number | CRM | Text (`STRING`) | Customer master data from the CRM, for example `bronze_customers.cust_id`. |

The two are **different identifier systems**. They share a column name in some tables, but a CRM customer number is not a canonical customer ID.

- Customers created in the CRM before the 2026 CRM migration have a CRM number equal to their canonical ID written as text (for example canonical `123` and CRM `"123"`).
- Customers created after the migration get CRM numbers with a `C-` prefix (for example `"C-00042"`). They have no numeric equivalent.

## Relationship and join rule

To match order data to CRM customer data, **convert the canonical ID to text** and compare it with the CRM number:

```sql
CAST(orders.customer_id AS STRING) = crm.cust_id
```

**Never convert the CRM number to a number** (explicitly, or implicitly by comparing a `BIGINT` column with a `STRING` column). `C-` CRM numbers become NULL, and those customers silently drop out of joins, customer counts, and revenue by customer.

## Ownership

Changes to how customers are identified or joined need review by the Customer Data Platform team.
