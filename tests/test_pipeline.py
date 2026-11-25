"""End-to-end test: two small, deliberately messy files through build and audit."""

import csv

import duckdb
import pytest

from spendsentinel import audit, build
from spendsentinel.config import Department, Settings

# Windows-1252, a title line above the header, a duplicate pair (T1/T2), a payment
# split over two lines (T3), a small payment (T4) and a totals row.
JANUARY = (
    "Spending over £25,000 - January 2026\r\n"
    "Date,Supplier,Amount,Expense Type,Expense Area,Transaction number,Supplier Postcode\r\n"
    '05/01/2026,Acme Ltd,"£30,000.00",IT,Digital,T1,AB1 2CD\r\n'
    '05/01/2026,Acme Limited,"£30,000.00",IT,Digital,T2,AB1 2CD\r\n'
    '10/01/2026,Widget Co,"£12,000.00",Parts,Ops,T3,ZZ9 9ZZ\r\n'
    '10/01/2026,Widget Co,"£14,000.00",Parts,Ops,T3,ZZ9 9ZZ\r\n'
    '15/01/2026,Small Supplier,"£5,000.00",Misc,Ops,T4,\r\n'
    'TOTAL,,"£91,000.00",,,,\r\n'
)
# UTF-8 with DWP-style headers, one line republished from January, a refund and a payment.
FEBRUARY = (
    "Invoice Paid Date,Supplier Name,Payment Number,Transparency Invoice Amount,Invoice Account Description\n"
    '10/01/2026,Widget Co,T3,"14,000.00",Parts\n'
    '03/02/2026,Gadget PLC,P9,"(2,500.00)",Refunds\n'
    '03/02/2026,Gadget PLC,P10,"40,000.00",Hardware\n'
)


@pytest.fixture()
def pipeline(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    (raw / "TST").mkdir(parents=True)
    (raw / "TST" / "2026-01.csv").write_bytes(JANUARY.encode("cp1252"))
    (raw / "TST" / "2026-02.csv").write_bytes(FEBRUARY.encode("utf-8"))
    with (raw / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["dept_code", "year", "month", "local_path", "file_url", "sha256"])
        writer.writeheader()
        for month in (1, 2):
            writer.writerow({"dept_code": "TST", "year": 2026, "month": month,
                             "local_path": f"TST/2026-0{month}.csv", "file_url": "", "sha256": ""})

    db = tmp_path / "test.duckdb"
    for module in (build, audit):
        monkeypatch.setattr(module, "DB_PATH", db)
        monkeypatch.setattr(module, "REPORTS_DIR", tmp_path / "reports")
        monkeypatch.setattr(module, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(build, "RAW_SPEND_DIR", raw)
    monkeypatch.setattr(build, "load_settings", lambda: Settings(12, (Department("TST", "Test", "x"),)))

    build.run()
    audit.run()
    with duckdb.connect(str(db), read_only=True) as con:
        yield con


def test_lines_are_kept_or_dropped_for_a_reason(pipeline):
    rules = dict(pipeline.execute("SELECT rule, count(*) FROM quality_issues GROUP BY rule").fetchall())
    assert rules["header_not_on_first_row"] == 1
    assert rules["encoding_cp1252"] == 1
    assert rules["non_data_row"] == 1          # the TOTAL row
    assert rules["repeated_across_files"] == 1  # T3's line republished in February
    assert rules["negative_amount"] == 1
    assert rules["below_publication_threshold"] == 2  # T4 and the refund


def test_reconciliation_balances_to_the_penny(pipeline):
    raw, kept, dropped, payments = pipeline.execute("""
        SELECT sum(amount_pence), sum(amount_pence) FILTER (WHERE keep),
               sum(amount_pence) FILTER (WHERE NOT keep), (SELECT sum(amount_pence) FROM payments)
        FROM payment_lines
    """).fetchone()
    assert raw == kept + dropped
    assert kept == payments
    assert dropped == 91_000_00 + 14_000_00


def test_invoice_lines_roll_up_into_one_payment(pipeline):
    total, lines = pipeline.execute(
        "SELECT amount_pence, n_lines FROM payments WHERE payment_ref = 'T3'").fetchone()
    assert (total, lines) == (26_000_00, 2)
    assert pipeline.execute("SELECT count(*) FROM payments").fetchone()[0] == 6


def test_duplicate_pair_is_found_and_graded_high(pipeline):
    rows = pipeline.execute("SELECT first_ref, second_ref, confidence FROM audit_possible_duplicates").fetchall()
    assert rows == [("T1", "T2", "high")]
