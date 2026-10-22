# Net Revenue

**Domain:** Finance · **Owner:** Finance · **Also called:** net sales, recognized revenue

## Definition

Net revenue is the money the business recognizes from customer orders after discounts. It is the sum of each order's net amount (order amount minus discount) for orders that count as real sales, reported by order date.

## What counts

- Orders in status `PLACED` or `SHIPPED`.
- The order's net amount (amount minus discount), not the gross amount.

## What does not count

- **Cancelled orders** (`status = CANCELLED`). A cancelled order is not revenue, even though it was placed.
- **Test orders** (`is_test = true`). Synthetic orders created by engineering and QA must never reach any revenue figure.

## Governed sources

- **Definition:** the `net_revenue` measure of the metric view `gold_revenue_metrics` is the governed definition. Any calculation of net revenue must produce the same result.
- **Certified dataset:** `gold_revenue_daily` is the certified daily net revenue table and the source for executive reporting and month-end close.
- **Not net revenue:** `gold_revenue_daily_v1` is gross revenue (it includes cancelled and test orders). It was deprecated in June 2026 and must not be used for revenue reporting.

## Business rules

- Any breakdown of net revenue (by segment, region, product, or customer) must apply the same exclusions, so its total reconciles to `gold_revenue_daily` for the same dates.
- Executive dashboards show net revenue only. A number labelled "revenue" in an executive report is expected to be net revenue.
