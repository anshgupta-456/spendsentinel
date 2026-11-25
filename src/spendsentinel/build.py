"""Combine every downloaded file into one DuckDB database and log each data-quality fix.

Outputs
-------
data/spendsentinel.duckdb
    files           one row per source file (encoding, header row, columns)
    payment_lines   every data row, in one schema, with a keep flag
    payments        lines rolled up to one row per payment
    quality_issues  every rule that fired, at file, line or payment level
reports/data_quality.md
    the human-readable data-quality and reconciliation report
"""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from . import standardize as st
from .config import DB_PATH, EXPORTS_DIR, RAW_SPEND_DIR, REPORTS_DIR, SQL_DIR, load_settings

PUBLICATION_THRESHOLD_PENCE = 25_000 * 100

RULES = {
    # rule: (level, what it checks, what we do)
    "header_not_on_first_row": ("file", "Header row is below a title or blank line", "skip the extra rows"),
    "encoding_cp1252": ("file", "File is Windows-1252, not UTF-8 (the £ sign breaks otherwise)", "decode as Windows-1252"),
    "unmapped_columns": ("file", "Columns that are not part of the common schema", "ignore them"),
    "non_data_row": ("line", "No date and no supplier, such as a totals or footer row", "drop"),
    "missing_or_bad_amount": ("line", "Amount is blank or cannot be read as money", "drop"),
    "unparseable_date": ("line", "Date is filled in but in no known format", "keep, date left empty"),
    "missing_date": ("line", "Date is blank", "keep, date left empty"),
    "date_outside_file_month": ("line", "Payment date is not in the month the file covers", "keep, flag"),
    "missing_supplier": ("line", "Supplier name is blank", "keep, flag"),
    "redacted_supplier": ("line", "Supplier name withheld (for example a person's name)", "keep, flag"),
    "missing_transaction_id": ("line", "No transaction or payment number", "keep, each line is its own payment"),
    "invalid_postcode": ("line", "Supplier postcode is not a valid UK postcode", "keep, flag"),
    "zero_amount": ("line", "Amount is exactly £0.00", "keep, flag"),
    "negative_amount": ("line", "Negative amount (credit note or refund)", "keep, flag"),
    "exact_duplicate_line": ("line", "Line is identical to another line in the same file", "keep, flag"),
    "repeated_across_files": ("line", "Same transaction, supplier, date and amount already published in an earlier file", "drop the later copy"),
    "below_publication_threshold": ("payment", "Payment total is under £25,000 after adding up its lines", "keep, flag"),
}


def _line_records(manifest_row: dict, dept_code: str):
    path = RAW_SPEND_DIR / manifest_row["local_path"]
    parsed = st.parse_file(path.read_bytes())
    year, month = int(manifest_row["year"]), int(manifest_row["month"])
    file_month = date(year, month, 1)
    source_file = manifest_row["local_path"]

    file_record = {
        "source_file": source_file, "dept_code": dept_code, "file_month": file_month,
        "file_url": manifest_row["file_url"], "sha256": manifest_row["sha256"],
        "encoding": parsed.encoding, "header_row": parsed.header_row + 1,
        "columns": " | ".join(h for h in parsed.headers if h),
        "unmapped_columns": " | ".join(parsed.unmapped),
    }
    issues = []
    if parsed.header_row > 0:
        issues.append(("file", source_file, dept_code, "header_not_on_first_row", f"header on line {parsed.header_row + 1}", None))
    if parsed.encoding == "cp1252":
        issues.append(("file", source_file, dept_code, "encoding_cp1252", "", None))
    if parsed.unmapped:
        issues.append(("file", source_file, dept_code, "unmapped_columns", ", ".join(parsed.unmapped), None))

    lines = []
    for offset, row in enumerate(parsed.rows):
        if not any(cell.strip() for cell in row):
            continue
        source_row = parsed.first_data_row + offset
        line_id = f"{dept_code}-{year}{month:02d}-{source_row}"
        get = {canonical: (row[i] if i < len(row) else "") for i, canonical in parsed.mapping.items()}

        raw_date = st.clean_text(get.get("payment_date"))
        payment_date = st.parse_date(raw_date)
        supplier = st.clean_text(get.get("supplier"))
        amount_pence = st.parse_amount_pence(get.get("amount"))
        transaction_id = st.clean_text(get.get("transaction_id"))
        postcode, postcode_ok = st.clean_postcode(get.get("postcode"))

        flags: list[tuple[str, str]] = []
        keep = True
        if payment_date is None and supplier is None:
            flags.append(("non_data_row", ",".join(c.strip() for c in row if c.strip())[:120]))
            keep = False
        elif amount_pence is None:
            flags.append(("missing_or_bad_amount", st.clean_text(get.get("amount")) or ""))
            keep = False
        if keep:
            if raw_date and payment_date is None:
                flags.append(("unparseable_date", raw_date))
            elif raw_date is None:
                flags.append(("missing_date", ""))
            elif (payment_date.year, payment_date.month) != (year, month):
                flags.append(("date_outside_file_month", payment_date.isoformat()))
            if supplier is None:
                flags.append(("missing_supplier", ""))
            elif st.is_redacted(supplier):
                flags.append(("redacted_supplier", supplier))
            if transaction_id is None:
                flags.append(("missing_transaction_id", ""))
            if not postcode_ok:
                flags.append(("invalid_postcode", postcode or ""))
            if amount_pence == 0:
                flags.append(("zero_amount", ""))
            elif amount_pence < 0:
                flags.append(("negative_amount", f"{amount_pence / 100:.2f}"))

        for rule, detail in flags:
            issues.append(("line", line_id, dept_code, rule, detail, amount_pence))

        lines.append({
            "line_id": line_id, "dept_code": dept_code, "source_file": source_file,
            "source_row": source_row, "file_month": file_month, "payment_date": payment_date,
            "entity": st.clean_text(get.get("entity")),
            "expense_type": st.clean_text(get.get("expense_type")),
            "expense_area": st.clean_text(get.get("expense_area")),
            "supplier_raw": get.get("supplier", "").strip() or None,
            "supplier_name": supplier,
            "supplier_key": None if st.is_redacted(supplier) else st.supplier_key(supplier),
            "is_redacted": st.is_redacted(supplier),
            "transaction_id": transaction_id,
            "description": st.clean_text(get.get("description")),
            "postcode": postcode,
            "contract_number": st.clean_text(get.get("contract_number")),
            "amount_pence": amount_pence,
            "keep": keep,
        })
    return file_record, lines, issues


def _load(con: duckdb.DuckDBPyConnection, files, lines, issues) -> None:
    files_df = pd.DataFrame(files)
    lines_df = pd.DataFrame(lines)
    lines_df["amount_pence"] = lines_df["amount_pence"].astype("Int64")
    issues_df = pd.DataFrame(issues, columns=["level", "ref", "dept_code", "rule", "detail", "amount_pence"])
    issues_df["amount_pence"] = issues_df["amount_pence"].astype("Int64")
    con.register("files_df", files_df)
    con.register("lines_df", lines_df)
    con.register("issues_df", issues_df)
    con.execute("CREATE TABLE files AS SELECT * FROM files_df")
    con.execute("""
        CREATE TABLE payment_lines AS
        SELECT * EXCLUDE (payment_date, file_month),
               CAST(file_month AS DATE) AS file_month,
               CAST(payment_date AS DATE) AS payment_date
        FROM lines_df
    """)
    con.execute("CREATE TABLE quality_issues AS SELECT * FROM issues_df")
    con.execute((SQL_DIR / "build" / "rollup.sql").read_text(encoding="utf-8"))


def _rule_summary(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    counts = con.execute("""
        SELECT rule, count(*) AS flagged,
               coalesce(sum(abs(amount_pence)), 0) / 100.0 AS amount_gbp
        FROM quality_issues GROUP BY rule
    """).df().set_index("rule")
    rows = []
    for rule, (level, meaning, action) in RULES.items():
        flagged = int(counts.loc[rule, "flagged"]) if rule in counts.index else 0
        amount = float(counts.loc[rule, "amount_gbp"]) if rule in counts.index else 0.0
        rows.append({"rule": rule, "level": level, "checks": meaning, "flagged": flagged,
                     "amount_gbp": round(amount, 2), "action": action})
    return pd.DataFrame(rows)


def _write_report(con: duckdb.DuckDBPyConnection, rules: pd.DataFrame) -> Path:
    recon = con.execute((SQL_DIR / "build" / "reconciliation.sql").read_text(encoding="utf-8")).df()
    files = con.execute("""
        SELECT dept_code, count(*) AS files, count(DISTINCT columns) AS header_layouts,
               min(file_month) AS first_month, max(file_month) AS last_month,
               string_agg(DISTINCT encoding, ', ') AS encodings
        FROM files GROUP BY dept_code ORDER BY dept_code
    """).df()

    def gbp(x: float) -> str:
        return f"£{x:,.2f}"

    out = ["# Data quality report", "",
           "Generated by `spendsentinel build`. Every rule below runs on every file; "
           "nothing is fixed by hand.", "",
           "## Source files", "",
           "| Department | Files | Months | Header layouts | Encodings |",
           "| --- | ---: | --- | ---: | --- |"]
    for r in files.itertuples():
        out.append(f"| {r.dept_code} | {r.files} | {r.first_month:%b %Y} to {r.last_month:%b %Y} | "
                   f"{r.header_layouts} | {r.encodings} |")
    out += ["", "## Reconciliation", "",
            "Every pound in the raw files is either kept or dropped with a reason, "
            "so `raw = kept + dropped` must hold to the penny.", "",
            "| Department | Raw lines | Kept lines | Dropped lines | Payments | Raw total | Kept total | Dropped total | Balances |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |"]
    for r in recon.itertuples():
        balanced = r.raw_pence == r.kept_pence + r.dropped_pence and r.kept_pence == r.payments_pence
        ok = "yes" if balanced else "NO"
        out.append(f"| {r.dept_code} | {r.raw_lines:,} | {r.kept_lines:,} | {r.dropped_lines:,} | "
                   f"{r.payments:,} | {gbp(r.raw_pence / 100)} | {gbp(r.kept_pence / 100)} | "
                   f"{gbp(r.dropped_pence / 100)} | {ok} |")
    out += ["", "## Rules", "",
            "| Rule | Level | Checks | Flagged | £ affected | Action |",
            "| --- | --- | --- | ---: | ---: | --- |"]
    for r in rules.itertuples():
        out.append(f"| `{r.rule}` | {r.level} | {r.checks} | {r.flagged:,} | "
                   f"{gbp(r.amount_gbp) if r.level != 'file' else '-'} | {r.action} |")
    out.append("")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "data_quality.md"
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def run() -> None:
    settings = load_settings()
    codes = {d.code for d in settings.departments}
    with (RAW_SPEND_DIR / "manifest.csv").open(encoding="utf-8") as fh:
        manifest = [row for row in csv.DictReader(fh) if row["local_path"] and row["dept_code"] in codes]

    files, lines, issues = [], [], []
    for row in manifest:
        file_record, file_lines, file_issues = _line_records(row, row["dept_code"])
        files.append(file_record)
        lines.extend(file_lines)
        issues.extend(file_issues)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    with duckdb.connect(str(DB_PATH)) as con:
        _load(con, files, lines, issues)
        rules = _rule_summary(con)
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        rules.to_csv(EXPORTS_DIR / "data_quality_rules.csv", index=False)
        report = _write_report(con, rules)
        kept, payments = con.execute(
            "SELECT (SELECT count(*) FROM payment_lines WHERE keep), (SELECT count(*) FROM payments)"
        ).fetchone()

    flagged = Counter(i[3] for i in issues)
    print(f"files: {len(files)}  lines: {len(lines):,}  kept: {kept:,}  payments: {payments:,}")
    print("top issues:", ", ".join(f"{k}={v:,}" for k, v in flagged.most_common(5)))
    print(f"report: {report.relative_to(REPORTS_DIR.parent)}")
