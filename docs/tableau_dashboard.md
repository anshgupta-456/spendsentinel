# Building the Tableau Public dashboard

## Quick route: the ready-made workbook (about 10 minutes)

`spendsentinel tableau` also writes `exports/tableau/SpendSentinel.twbx`, a packaged
workbook with the data inside and these already built:

- **Sheets:** Total spend, Payments, Flagged spend, Monthly trend, Flagged spend by
  department, Top suppliers, Supplier risk, Quality rules
- **Dashboards:** Overview, Suppliers, Data quality

1. Install Tableau Public from public.tableau.com (free) and sign in.
2. **File > Open** and choose `SpendSentinel.twbx` (or download it from the live dashboard's footer).
3. Click through the three dashboard tabs. Two quick touch-ups make it look finished:
   - On **Monthly trend**, right-click the y-axis, choose **Edit Axis**, and set
     **Independent axis ranges for each row**, so each department gets its own scale.
   - Format money as currency: right-click `SUM(amount_gbp)` and `Flagged spend`, choose
     **Format**, then set **Numbers > Currency (Custom)**, display units **Millions (M)**, prefix £.
4. **File > Save to Tableau Public As...**, name it "SpendSentinel: UK government spend audit".
5. Copy the public link into the README under **Results**.

The workbook is generated without Tableau, so if anything looks off when it opens,
the manual route below builds the same sheets from scratch.

## Manual route (about an hour)

The data is already shaped for Tableau: `spendsentinel tableau` writes it to
`exports/tableau/`, and the live dashboard publishes the same files, so you can also
download them from its footer.

## 0. Get the data

Either run `spendsentinel tableau`, or download these from the live site's footer
(`https://anshgupta-456.github.io/spendsentinel/data/<file>`):

| File | Rows | What it is |
| --- | ---: | --- |
| `spend_payments.csv` | ~51,000 | One row per payment, with department, duplicate tier and the supplier's Companies House status already joined in. Most sheets use only this. |
| `duplicate_pairs.csv` | ~3,300 | Each matching pair: both dates, both references, tier (bulk-order pairs included, so filter them) |
| `supplier_ranking.csv` | ~3,200 | Suppliers ranked within each department, with share and cumulative share |
| `quality_issues_by_rule.csv` | ~25 | Data-quality rule counts per department |

## 1. Connect

1. Install Tableau Public from public.tableau.com and sign in (free account).
2. **Connect > To a File > Text file** and open `spend_payments.csv`.
3. On the data source page, check the field types: `payment_date` and `period_month` are
   **Date**; `amount_gbp` is **Number (decimal)**; `possible_duplicate`, `below_publication_threshold`
   and the `supplier_*` columns are **Boolean**.
4. Add the other three files as separate data sources (**Data > New Data Source**).

## 2. Calculated fields (in `spend_payments`)

```
Spend                SUM([amount_gbp])
Payments             COUNTD([payment_id])
Flagged spend        SUM(IF [possible_duplicate] THEN [amount_gbp] END)
Flagged share        [Flagged spend] / [Spend]
Risky supplier       [supplier_insolvent] OR [supplier_strike_off_proposed]
                     OR [supplier_accounts_overdue] OR [supplier_confirmation_overdue]
Spend with risky     SUM(IF [Risky supplier] THEN [amount_gbp] END)
```

Format `Spend`, `Flagged spend` and `Spend with risky` as currency (£, units: millions).

## 3. Sheets

| Sheet | Build |
| --- | --- |
| **KPI: Spend** | `Spend` on Text. Big font, no axes. Repeat for `Payments`, `Flagged spend`, `Spend with risky`. |
| **Monthly trend** | `period_month` (continuous, MONTH) on Columns, `Spend` on Rows, `department` on Rows too, so each department gets its own panel and scale. Set **Edit Axis > Independent axis ranges**. |
| **Top suppliers** | `supplier_name` on Rows, `Spend` on Columns, sort descending, filter Top 15 by `Spend`. `department` as a filter shown as a single-value list. |
| **Duplicates** | From `duplicate_pairs`: text table of `supplier_name`, `department`, `amount_gbp`, `first_date`, `second_date`, `first_ref`, `second_ref`, `confidence`. Filter `confidence` to high, medium, low; show the filter so bulk and no_reference can be switched on. |
| **80/20 curve** | From `supplier_ranking`: `rank` on Columns, `cumulative_share` on Rows, `department` on Color; add a constant reference line at 0.8. |
| **Supplier risk** | `supplier_name` on Rows, `Spend` on Columns, filter `Risky supplier` = True, `company_status` on Color. |
| **Data quality** | From `quality_issues_by_rule`: `rule` on Rows, `SUM(flagged)` on Columns, `level` on Color. |

## 4. Dashboards

1. **Overview**: the four KPI tiles across the top, the monthly trend below, top suppliers on the right.
   Add the `department` filter and set it to **Apply to worksheets > All using this data source**.
2. **Exceptions**: the duplicates table with its confidence filter, plus the supplier risk chart.
3. **Data quality**: the rules chart and a text box with the reconciliation line from
   `reports/data_quality.md` ("every department balances to the penny").

Size: **Fixed 1200 × 800** (or Automatic). Add a title and the caption
"Source: GOV.UK spending over £25,000 files; Companies House. Every finding is a lead for review."

## 5. Publish

**File > Save to Tableau Public As...**, name it "SpendSentinel: UK government spend audit".
Copy the public link and add it to the README under **Results**, next to the live dashboard link.
