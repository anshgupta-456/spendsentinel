# Building the Tableau Public dashboard

The dashboard is built in Tableau Public (free) from the CSVs that
`spendsentinel audit` and `spendsentinel match` write to `exports/`.

## 1. Connect the data

In Tableau Public, **Connect > Text file** and open `exports/payments.csv`. Then add
these as separate data sources the same way:

| File | Used for |
| --- | --- |
| `payments.csv` | Overview, trends, exceptions list |
| `possible_duplicates.csv` | Duplicate review table |
| `supplier_spend.csv` | Top suppliers and the 80/20 curve |
| `supplier_risk.csv` | Supplier risk view |
| `data_quality_rules.csv` | Data quality panel |

Check the field types: `payment_date` and `period_month` are **Date**, `amount_gbp`
and `spend_gbp` are **Number (decimal)**, and the true/false columns are **Boolean**.

## 2. Calculated fields (in `payments`)

```
Spend (£)              SUM([amount_gbp])
Flagged spend (£)      SUM(IF [possible_duplicate] THEN [amount_gbp] END)
Payments               COUNTD([payment_id])
Average payment (£)    [Spend (£)] / [Payments]
```

## 3. Sheets

1. **KPI tiles**: Spend, Payments, distinct suppliers, Flagged spend. One sheet per tile, big numbers, no axes.
2. **Monthly trend**: `period_month` (continuous month) on Columns, `Spend (£)` on Rows, `dept_code` on Color.
3. **Top suppliers**: from `supplier_spend`, filter `rank <= 15`, `supplier_name` on Rows sorted by `spend_gbp`, `dept_code` as a filter.
4. **80/20 curve**: from `supplier_spend`, `rank` on Columns, `cumulative_share` on Rows, one line per `dept_code`, with a reference line at 0.8.
5. **Duplicate review**: from `possible_duplicates`, a text table of supplier, amount, first and second date, refs and `confidence`. Filter out `bulk` and `no_reference` by default, but keep them selectable.
6. **Supplier risk**: from `supplier_risk`, a bar of `spend_gbp` by supplier, filtered to rows where any flag is true, colored by `status`.
7. **Data quality**: from `data_quality_rules`, `rule` on Rows and `flagged` as bars, with `action` as a label.

## 4. Dashboards

- **Overview**: KPI tiles across the top, monthly trend below, top suppliers on the right. Add a `dept_code` filter that applies to every sheet that uses the payments data.
- **Exceptions**: the duplicate review table with a confidence filter, plus the supplier risk chart.
- **Data quality**: the rules chart plus a text box with the reconciliation line from `reports/data_quality.md`.

Set the size to **Automatic** or 1200 × 800, and add a title and a "Source: GOV.UK spending over £25,000 files, Companies House" caption.

## 5. Publish

**File > Save to Tableau Public**. Copy the public link into the README under "Results".
