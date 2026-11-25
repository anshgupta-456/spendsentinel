# Data dictionary

Tables in `data/spendsentinel.duckdb` (rebuilt by `spendsentinel build`, `audit` and `match`)
and `data/companies_house.duckdb` (built by `spendsentinel companies`). Money is stored in
whole pence (`amount_pence`) so totals reconcile exactly; `amount_gbp` is for display.

## files

One row per downloaded CSV.

| Column | Meaning |
| --- | --- |
| `source_file` | Path under `data/raw/spend/`, e.g. `DWP/2026-03.csv` |
| `dept_code` | Department code from `config/sources.toml` |
| `file_month` | First day of the month the file covers |
| `file_url`, `sha256` | Where it came from and its checksum |
| `encoding` | `utf-8-sig` or `cp1252` |
| `header_row` | Line number of the header (1 = first line) |
| `columns`, `unmapped_columns` | Original headers, and those not used |

## payment_lines

Every data row from every file, in one schema.

| Column | Meaning |
| --- | --- |
| `line_id` | `<dept>-<YYYYMM>-<row>`, unique |
| `payment_date` | Parsed date (empty if it could not be read) |
| `supplier_raw`, `supplier_name` | As published, and with whitespace and placeholders cleaned |
| `supplier_key` | Grouping key: upper case, no punctuation, no legal suffix |
| `is_redacted` | Supplier name withheld |
| `transaction_id` | Transaction or payment number, if any |
| `expense_type`, `expense_area`, `entity`, `description`, `postcode`, `contract_number` | As published, cleaned |
| `amount_pence` | Amount in pence; negative for credits |
| `keep` | False for dropped rows (see `quality_issues`) |

## payments

Kept lines rolled up: one row per department, transaction number and supplier.
Lines with no transaction number are payments on their own.

| Column | Meaning |
| --- | --- |
| `payment_id` | `<dept>:<ref>:<supplier_key>` |
| `payment_ref` | Transaction number, or the line id when there is none |
| `period_month` | Month of the payment (falls back to the file month) |
| `n_lines`, `amount_pence`, `amount_gbp` | Number of lines and total |
| `expense_type`, `expense_area`, `description` | Taken from the largest line |

## quality_issues

| Column | Meaning |
| --- | --- |
| `level` | `file`, `line` or `payment` |
| `ref` | The file path, line id or payment id |
| `rule` | Rule name; see `RULES` in `src/spendsentinel/build.py` |
| `detail` | The offending value or a short note |

## Audit tables

| Table | Built by |
| --- | --- |
| `audit_possible_duplicates` | `sql/audit/01_possible_duplicates.sql`: one row per matching pair, with `confidence` (`high`, `medium`, `low`, `bulk`, `no_reference`) |
| `supplier_spend`, `supplier_ranked`, `audit_supplier_concentration` | `sql/audit/02_supplier_concentration.sql` |
| `monthly_trend` | `sql/audit/03_monthly_trend.sql` |
| `audit_cross_department_suppliers` | `sql/audit/04_cross_department_suppliers.sql` |

## Supplier tables

| Table | Meaning |
| --- | --- |
| `suppliers` | One row per `supplier_key`: spend, payments, departments, most common postcode |
| `supplier_matches` | Matched company number and the `method` that matched it, or `public_body`, `joint_venture`, `not_found` |
| `supplier_risk` | Matched suppliers with Companies House status and the flags `insolvency`, `strike_off_proposed`, `accounts_overdue`, `confirmation_overdue`, `new_company` |

## companies_house.duckdb

| Table | Meaning |
| --- | --- |
| `companies` | 5.7M companies: number, name, `name_key`, registered postcode, category, status, incorporation and filing dates, first SIC code |
| `company_previous_names` | Earlier names, with their own `name_key` |
| `company_tokens`, `token_frequency` | Word index used to find fuzzy-match candidates |
