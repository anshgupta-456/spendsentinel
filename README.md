# SpendSentinel

[![tests](https://github.com/anshgupta-456/spendsentinel/actions/workflows/tests.yml/badge.svg)](https://github.com/anshgupta-456/spendsentinel/actions/workflows/tests.yml)

Audit analytics for UK government spending. SpendSentinel downloads the monthly
"spending over £25,000" files that departments publish on GOV.UK, combines them
into one clean dataset, and runs the checks a procurement or audit team would:
possible duplicate payments, supplier concentration, and suppliers that are now
insolvent or behind on their filings. A local LLM writes a monthly briefing, and
every figure in it is checked against SQL before anyone reads it.

Every number can be traced back to a row in a published file, and every cleaning
step is logged as a data-quality rule.

## Results

Four departments (HMRC, DWP, DBT, DfT), 12 months each, 48 files:

| | |
| --- | --- |
| Payments analysed | 51,417 worth £49.65bn, built from 167,758 published lines |
| Reconciliation | Every department balances to the penny: raw = kept + dropped |
| Possible duplicate payments | 457 payments worth £49.2M flagged for review, after setting aside bulk orders |
| Suppliers matched to Companies House | 90% of the spend that could be on the register |
| Spend with suppliers now in liquidation or administration | £3.1M across 7 suppliers |
| LLM briefings (2 local models × 10 months) | 96% of figures right in first drafts, 209 of 210 after one correction; invented figures and a wrong comparison caught and logged ([experiment](reports/briefings/README.md)) |

Reports: [audit findings](reports/audit_findings.md) ·
[supplier matching and risk](reports/supplier_risk.md) ·
[data quality](reports/data_quality.md) ·
[monthly briefings](reports/briefings/README.md)

Every finding is a lead for review, not proof of an error.

## Why this data

Procurement teams need to know where money goes, whether it was paid correctly,
and how much depends on a few suppliers. Public spend files make a realistic test
bed because they are messy in the same ways internal finance data is:

| Problem found in the source files | How it is handled |
| --- | --- |
| Four column layouts for the same information (DWP's has nothing in common with the others) | Header synonyms map every layout to one schema |
| Amount column named just `£` (DfT) | Same mapping |
| Same date written five ways in one department (`02/09/2026`, `02-Sep-26`, `02 Sept 2026`, ...) | One parser tries each known format |
| Files in Windows-1252, so `£` breaks as UTF-8 | Encoding detected per file |
| A title line above the header row | Header row found automatically |
| `#`, `NOT SET` and blanks meaning "no value" | Treated as missing |
| DWP lists invoice lines, many under £25,000, under one payment number | Lines rolled up into payments |
| A totals row worth £221M inside a DWP file | Dropped as a non-data row, so it is not counted twice |
| Supplier names cut at 35 characters, with codes after the legal suffix (`MITIE FM LTD - 2WM`) | Normalised supplier keys |

## Pipeline

```
GOV.UK Content API ──fetch──▶ data/raw/spend/<dept>/<YYYY-MM>.csv  (+ manifest with SHA-256)
                      │
                    build      standardise headers, dates, amounts, postcodes, supplier names
                      │        17 data-quality rules, logged per file, line or payment
                      ▼
            data/spendsentinel.duckdb  ──audit──▶  SQL tests, reports/, exports/*.csv (Tableau)
                      │
Companies House ──companies──▶ data/companies_house.duckdb (5.7M companies)
                      │
                    match      rule cascade: exact name, postcode, previous name, fuzzy
                      │        risk flags: insolvency, strike-off, overdue filings
                      ▼
                    brief      local LLM writes a briefing from SQL facts; every figure is checked
```

## Audit tests

| Test | What it looks for |
| --- | --- |
| [Possible duplicates](sql/audit/01_possible_duplicates.sql) | Same department, supplier and amount to the penny, within 7 days, under different payment references. Graded high, medium or low. Bulk orders (4+ identical payments, such as a vehicle fleet) and payments with no reference are set aside rather than counted. |
| [Supplier concentration](sql/audit/02_supplier_concentration.sql) | Top-10 share, number of suppliers making up 80% of spend, Herfindahl-Hirschman index |
| [Monthly trend](sql/audit/03_monthly_trend.sql) | Spend, payments and suppliers per month, with month-on-month change |
| [Cross-department suppliers](sql/audit/04_cross_department_suppliers.sql) | Suppliers paid by more than one department |

**Why there is no Benford's law test.** Benford's law expects amounts that span many
orders of magnitude with no cut-off. These files only contain payments over £25,000,
and payment sizes fall off steeply above any cut-off, so a first-digit test flags
every department as non-conforming for reasons that have nothing to do with fraud.
A test that always fires is not a useful control, so it is left out.

## Supplier matching and risk

Supplier names are matched to the Companies House register with a cascade, strictest
rule first, and each match records the rule that made it:

1. exact name and same postcode
2. exact name, one clear candidate (standard registrations preferred over branch records)
3. a previous name of the company
4. same name once a leading or trailing "UK" is ignored
5. similar name at the same postcode (token set ratio of 85 or more)
6. very similar name (token sort ratio of 92 or more)

A supplier with two equally good candidates is left unmatched rather than guessed.
Names that sound like public bodies ("Home Office", "GMCA", "Torbay BC") only match
when the postcode agrees too, because private companies can register those names.
Councils, combined authorities and unincorporated joint ventures (most HS2 civil
engineering) are labelled rather than forced onto a company.

Match precision is measured, not assumed: `reports/match_review_sample.csv` holds a
stratified sample of matches to check by hand, and `spendsentinel evaluate` reports
precision for each rule.

Risk flags use the snapshot's status and filing dates: in liquidation, administration
or receivership; proposal to strike off; accounts or confirmation statement more than
30 days overdue (only for company types that must file); incorporated less than a year
before the first payment.

## LLM briefing with checked figures

`spendsentinel brief` asks a local model (through Ollama, temperature 0) to write a
short monthly briefing. The experiment compares `llama3.2:3b` and `llama3.1:8b` over
10 months, run on a GPU server by pointing `OLLAMA_URL` at it. Guardrails:

- The model sees only aggregated facts computed in SQL, never payment rows, and runs
  on the local machine, so no data leaves it.
- Every money figure, percentage and count in its text is extracted and matched to a
  fact, allowing only for the rounding shown (`£3.49bn` must round from the fact).
- If any figure fails, the model is told which ones and gets one rewrite.
- Rankings (which department changed most) are computed in SQL and passed in as facts,
  and the briefing's largest-change claim is checked against them.
- Every run is logged in `reports/briefings/runs.jsonl` and summarised in
  [the experiment table](reports/briefings/README.md).

What the experiment found: first drafts got 96% of figures right; the misses were
invented numbers (one rewrite claimed £437.5bn for a month of about £5bn), all caught.
An earlier version let the model rank departments itself, and a human read showed it
naming the smallest change as the largest while every number still checked out, which
is why rankings now come from SQL. The figure check cannot confirm a figure is attached
to the right department or supplier, so a person still reads the briefing.

## Run it

Requires Python 3.11+, and [Ollama](https://ollama.com) for the briefing step.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"

spendsentinel fetch       # download 12 months for each department in config/sources.toml
spendsentinel build       # clean, validate and load into DuckDB
spendsentinel audit       # audit tests, reports/ and exports/
spendsentinel companies   # load a Companies House snapshot (download it first, see below)
spendsentinel match       # match suppliers and flag risk
spendsentinel evaluate    # precision from the hand-checked review sample
ollama pull llama3.2:3b
spendsentinel brief --all-months
pytest                    # unit and end-to-end tests
```

The Companies House snapshot is the free "BasicCompanyDataAsOneFile" zip (about 470 MB)
from [download.companieshouse.gov.uk](https://download.companieshouse.gov.uk/en_output.html);
put it in `data/raw/companies_house/`. Add a department by adding its GOV.UK collection
to `config/sources.toml`.

## Project layout

```
config/sources.toml        departments and months to download
src/spendsentinel/
  fetch.py                 GOV.UK Content API client
  standardize.py           header, date, amount, postcode and supplier-name rules
  build.py                 combine files, quality rules, DuckDB load, quality report
  audit.py                 SQL tests, CSV exports, findings report
  companies.py             Companies House snapshot loader
  match.py                 supplier matching cascade and risk flags
  briefing.py              LLM briefing and figure checker
sql/build/                 roll lines up into payments, reconciliation
sql/audit/                 one file per audit test
tests/                     unit tests and an end-to-end pipeline test
reports/                   generated reports (committed)
docs/                      data dictionary, Tableau dashboard guide
```

## Data

Contains public sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/),
from GOV.UK and Companies House. Source files are downloaded on demand and are not
stored in this repository.
