# SpendSentinel

Audit analytics for UK government spending. SpendSentinel downloads the monthly
"spending over £25,000" files that departments publish on GOV.UK, combines them
into one clean dataset, and runs procurement audit tests: possible duplicate
payments, supplier concentration and suppliers shared across departments.

Every number it produces can be traced back to a row in a published file, and
every cleaning step is logged as a data-quality rule.

**Results:** [audit findings](reports/audit_findings.md) ·
[data quality report](reports/data_quality.md)

## Why this data

Procurement teams need to know where money goes, whether it was paid correctly,
and how much depends on a few suppliers. Public spend files make a realistic test
bed because they are messy in all the ways real finance data is messy:

| Problem found in the source files | How it is handled |
| --- | --- |
| Four different column layouts for the same information (DWP's has nothing in common with the others) | Header synonyms map every layout to one schema |
| Amount column named just `£` (DfT) | Same mapping |
| Same date written five ways in one department (`02/09/2026`, `02-Sep-26`, `02 Sept 2026`, ...) | One parser tries each known format |
| Files in Windows-1252, so `£` breaks as UTF-8 | Encoding detected per file |
| A title line above the header row | Header row found automatically |
| `#`, `NOT SET` and blanks meaning "no value" | Treated as missing |
| DWP lists invoice lines, many under £25,000, under one payment number | Lines rolled up into payments |
| A totals row worth £221M inside a DWP file | Dropped as a non-data row, so it is not counted twice |

The build reconciles every department to the penny: raw total = kept + dropped,
and the payments table holds exactly the kept total.

## Pipeline

```
GOV.UK Content API ──fetch──▶ data/raw/spend/<dept>/<YYYY-MM>.csv  (+ manifest with SHA-256)
                      │
                    build      standardize headers, dates, amounts, postcodes, supplier names
                      │        17 data-quality rules, logged per file, line or payment
                      ▼
            data/spendsentinel.duckdb
              files · payment_lines · payments · quality_issues
                      │
                    audit      SQL tests in sql/audit/
                      ▼
            reports/*.md  and  exports/*.csv (for Tableau)
```

## Audit tests

| Test | What it looks for |
| --- | --- |
| [Possible duplicates](sql/audit/01_possible_duplicates.sql) | Same department, supplier and amount to the penny, within 7 days, under different payment references. Graded high, medium or low. Bulk orders (4+ identical payments, such as a vehicle fleet) and payments without a reference are set aside rather than counted. |
| [Supplier concentration](sql/audit/02_supplier_concentration.sql) | Top-10 share, number of suppliers making up 80% of spend, and the Herfindahl-Hirschman index |
| [Monthly trend](sql/audit/03_monthly_trend.sql) | Spend, payments and suppliers per month, with month-on-month change |
| [Cross-department suppliers](sql/audit/04_cross_department_suppliers.sql) | Suppliers paid by more than one department |

**Why there is no Benford's law test.** Benford's law expects amounts that span many
orders of magnitude with no cut-off. These files only contain payments over £25,000,
and payment sizes fall off steeply above any cut-off, so a first-digit test flags
every department as non-conforming for reasons that have nothing to do with fraud.
A test that always fires is not a useful control, so it is left out.

## Run it

Requires Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"

spendsentinel fetch   # download 12 months for each department in config/sources.toml
spendsentinel build   # clean, validate and load into DuckDB
spendsentinel audit   # run the audit tests, write reports/ and exports/
pytest                # unit tests for the parsing rules
```

Add a department by adding its GOV.UK collection to `config/sources.toml`.

## Project layout

```
config/sources.toml        departments and months to download
src/spendsentinel/
  fetch.py                 GOV.UK Content API client
  standardize.py           header, date, amount, postcode and supplier-name rules
  build.py                 combine files, run quality rules, load DuckDB, write the quality report
  audit.py                 run SQL tests, export CSVs, write the findings report
sql/build/                 roll lines up into payments, reconciliation
sql/audit/                 one file per audit test
tests/                     pytest unit tests
reports/                   generated reports (committed)
```

## Roadmap

- Match supplier names to Companies House records and measure match precision on a hand-checked sample
- Flag spend with suppliers that are in liquidation, being struck off or late filing accounts
- Tableau Public dashboard built from `exports/`
- A monthly briefing written by a local LLM, with every figure checked against SQL

## Data

Contains public sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Source files are downloaded on demand and are not stored in this repository.
