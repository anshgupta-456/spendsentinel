from datetime import date

import pytest

from spendsentinel import standardize as st


@pytest.mark.parametrize("raw, expected", [
    ("02/09/2026", date(2026, 9, 2)),
    ("02-Sep-26", date(2026, 9, 2)),
    ("02 September 2026", date(2026, 9, 2)),
    ("02 Sept 2026", date(2026, 9, 2)),
    ("02 Sep 2026", date(2026, 9, 2)),
    ("2026-09-02", date(2026, 9, 2)),
    ("TOTAL", None),
    ("", None),
    ("#", None),
])
def test_parse_date(raw, expected):
    assert st.parse_date(raw) == expected


@pytest.mark.parametrize("raw, pence", [
    ("£77,287.02", 7728702),
    ("6,000,000.00", 600000000),
    ("25000", 2500000),
    ("99999.9", 9999990),
    ("(1,234.50)", -123450),
    ("1234.50-", -123450),
    ("-£12.00", -1200),
    (" £426,091,000.00 ", 42609100000),
    ("NOT SET", None),
    ("abc", None),
])
def test_parse_amount_pence(raw, pence):
    assert st.parse_amount_pence(raw) == pence


def test_map_headers_handles_each_department_layout():
    dwp = ["Invoice Paid Date", "Invoice Cost Centre Description", "Invoice Account Description",
           "Supplier Name", "Payment Number", "Transparency Invoice Amount"]
    dft = ["Department Family", "Entity", "Date", "Expense Type", "Expense Area rev", "Supplier",
           "Transaction No", "Item Text", " £ ", "Postal Code"]
    for headers in (dwp, dft):
        mapping, _ = st.map_headers(headers)
        assert {"payment_date", "supplier", "amount", "transaction_id"} <= set(mapping.values())


def test_parse_file_finds_header_below_a_title_and_decodes_cp1252():
    raw = "Spending over £25,000\r\nDate,Supplier,Amount,Expense Type,Expense Area\r\n02/09/2026,ACME LTD,£30000,IT,Digital\r\n"
    parsed = st.parse_file(raw.encode("cp1252"))
    assert parsed.encoding == "cp1252"
    assert parsed.header_row == 1
    assert parsed.rows[0][1] == "ACME LTD"


@pytest.mark.parametrize("name, key", [
    ("IBM United Kingdom Ltd", "IBM UK"),
    ("IBM UNITED KINGDOM LIMITED", "IBM UK"),
    ("The Crown Hosting Data Centres Ltd.", "CROWN HOSTING DATA CENTRES"),
    ("Morris & Spottiswood Limited", "MORRIS AND SPOTTISWOOD"),
    ("Capita Business Services Ltd", "CAPITA BUSINESS SERVICES"),
])
def test_supplier_key(name, key):
    assert st.supplier_key(name) == key


def test_postcodes():
    assert st.clean_postcode("sn139gb") == ("SN13 9GB", True)
    assert st.clean_postcode("E20 1HZ") == ("E20 1HZ", True)
    assert st.clean_postcode("#") == (None, True)
    assert st.clean_postcode("12345")[1] is False


def test_redacted_supplier():
    assert st.is_redacted("REDACTED")
    assert st.is_redacted("Redacted - personal data")
    assert not st.is_redacted("Capita Business Services Ltd")
