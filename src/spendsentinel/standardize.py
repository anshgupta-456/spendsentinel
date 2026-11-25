"""Turn one department's raw monthly CSV into rows with a common schema.

Every department publishes the same kind of data, but no two files agree on
column names, date formats, encodings or how a blank value is written. This
module holds the small, testable rules that make them line up.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------------
# Column names
# ---------------------------------------------------------------------------

HEADER_SYNONYMS: dict[str, set[str]] = {
    "payment_date": {"date", "date of payment", "invoice paid date", "payment date",
                     "transaction date", "date paid"},
    "supplier": {"supplier", "supplier name", "vendor", "vendor name", "merchant name"},
    "amount": {"amount", "transparency invoice amount", "£", "amount £", "value", "value £",
               "net amount"},
    "expense_type": {"expense type", "invoice account description"},
    "expense_area": {"expense area", "expense area rev", "invoice cost centre description",
                     "cost centre"},
    "entity": {"entity"},
    "department": {"department", "department family"},
    "transaction_id": {"transaction number", "transaction no", "payment number", "transaction id"},
    "description": {"description", "item text"},
    "postcode": {"supplier postcode", "supplier post code", "postal code", "postcode"},
    "contract_number": {"contract number"},
}
_LOOKUP = {name: canonical for canonical, names in HEADER_SYNONYMS.items() for name in names}
REQUIRED = ("payment_date", "supplier", "amount")


def normalize_header(raw: str) -> str:
    text = raw.replace("﻿", "").strip().lower()
    text = re.sub(r"[()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def map_headers(headers: list[str]) -> tuple[dict[int, str], list[str]]:
    """Return {column index: canonical name} and the list of unmapped header names."""
    mapping: dict[int, str] = {}
    unmapped: list[str] = []
    for index, raw in enumerate(headers):
        name = normalize_header(raw)
        canonical = _LOOKUP.get(name)
        if canonical and canonical not in mapping.values():
            mapping[index] = canonical
        elif name:
            unmapped.append(raw.strip())
    return mapping, unmapped


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

NULL_TOKENS = {"", "#", "-", "n/a", "na", "null", "none", "not set", "not applicable"}


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
    return None if text.lower() in NULL_TOKENS else text


_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%d-%b-%y", "%d-%b-%Y", "%d %B %Y", "%d %b %Y",
                 "%Y-%m-%d", "%d.%m.%Y")


def parse_date(value: str | None) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    text = re.sub(r"\bSept\b", "Sep", text, flags=re.IGNORECASE)
    text = text.split(" 00:00")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount_pence(value: str | None) -> int | None:
    """Parse '£1,234.50', '(1,234.50)', '1234.5-' and similar into whole pence."""
    text = clean_text(value)
    if not text:
        return None
    negative = False
    text = text.replace("£", "").replace("GBP", "").replace(",", "").replace(" ", "")
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.endswith("-"):
        negative, text = True, text[:-1]
    if text.startswith("-"):
        negative, text = not negative, text[1:]
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    pence = int((amount * 100).quantize(Decimal("1")))
    return -pence if negative else pence


_LEGAL_SUFFIXES = ("LIMITED", "LTD", "PLC", "LLP", "LP", "INC", "INCORPORATED", "CO", "COMPANY")
_REDACTED = re.compile(r"redact|withheld|personal data|not disclosed|individual", re.IGNORECASE)


def supplier_key(name: str | None) -> str | None:
    """A grouping key for supplier names: upper case, no punctuation or legal suffix."""
    if not name:
        return None
    text = name.upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    text = re.sub(r"\bUNITED KINGDOM\b", "UK", text)
    words = text.split()
    if words and words[0] == "THE":
        words = words[1:]
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words = words[:-1]
    return " ".join(words) or None


def is_redacted(name: str | None) -> bool:
    return bool(name and _REDACTED.search(name))


_POSTCODE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$")


def clean_postcode(value: str | None) -> tuple[str | None, bool]:
    """Return (postcode in standard form, is_valid). Missing postcodes count as valid."""
    text = clean_text(value)
    if not text:
        return None, True
    compact = text.upper().replace(" ", "")
    if _POSTCODE.match(compact) or _POSTCODE.match(text.upper()):
        return f"{compact[:-3]} {compact[-3:]}", True
    return text.upper(), False


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

@dataclass
class ParsedFile:
    encoding: str
    header_row: int
    headers: list[str]
    mapping: dict[int, str]
    unmapped: list[str]
    rows: list[list[str]] = field(default_factory=list)  # data rows, header removed
    first_data_row: int = 0  # 1-based line number of the first data row


def decode(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def parse_file(data: bytes) -> ParsedFile:
    text, encoding = decode(data)
    rows = list(csv.reader(io.StringIO(text)))
    for index, row in enumerate(rows[:20]):
        mapping, unmapped = map_headers(row)
        if all(name in mapping.values() for name in REQUIRED):
            body = [r for r in rows[index + 1:]]
            return ParsedFile(encoding, index, [c.strip() for c in row], mapping, unmapped,
                              body, index + 2)
    raise ValueError("no header row with a date, supplier and amount column in the first 20 rows")
