"""Load the Companies House "Free Company Data Product" snapshot into DuckDB.

The snapshot is a monthly CSV of every company on the UK register (about 5.7
million rows). We keep the columns needed for matching and risk checks, and add
a name key built with the same rules used for supplier names so the two sides
can be joined.
"""

from __future__ import annotations

import re
import zipfile
from datetime import date
from pathlib import Path

import duckdb

from . import standardize as st
from .config import COMPANIES_DB_PATH, RAW_COMPANIES_DIR

COLUMNS = {
    # our name: column in the Companies House file (after trimming spaces)
    "company_name": "CompanyName",
    "company_number": "CompanyNumber",
    "postcode": "RegAddress.PostCode",
    "category": "CompanyCategory",
    "status": "CompanyStatus",
    "incorporation_date": "IncorporationDate",
    "accounts_next_due": "Accounts.NextDueDate",
    "accounts_last_made_up": "Accounts.LastMadeUpDate",
    "confstmt_next_due": "ConfStmtNextDueDate",
    "sic_1": "SICCode.SicText_1",
}
PREVIOUS_NAME_COLUMNS = [f"PreviousName_{i}.CompanyName" for i in range(1, 11)]
DATE_COLUMNS = ("incorporation_date", "accounts_next_due", "accounts_last_made_up", "confstmt_next_due")


def latest_snapshot() -> tuple[Path, date]:
    zips = sorted(RAW_COMPANIES_DIR.glob("BasicCompanyDataAsOneFile-*.zip"))
    if not zips:
        raise FileNotFoundError(
            f"No BasicCompanyDataAsOneFile-*.zip in {RAW_COMPANIES_DIR}. Download it from "
            "https://download.companieshouse.gov.uk/en_output.html")
    path = zips[-1]
    found = re.search(r"(\d{4})-(\d{2})-(\d{2})", path.name)
    return path, date(*map(int, found.groups()))


def _extract(zip_path: Path) -> Path:
    with zipfile.ZipFile(zip_path) as archive:
        member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
        target = RAW_COMPANIES_DIR / Path(member).name
        if not target.exists():
            archive.extract(member, RAW_COMPANIES_DIR)
    return target


def run() -> None:
    zip_path, snapshot = latest_snapshot()
    csv_path = _extract(zip_path)
    with duckdb.connect(str(COMPANIES_DB_PATH)) as con:
        con.execute(f"CREATE OR REPLACE TEMP VIEW ch_raw AS SELECT * FROM read_csv('{csv_path.as_posix()}', "
                    "header = true, all_varchar = true, quote = '\"', escape = '\"')")
        raw_names = [row[0] for row in con.execute("DESCRIBE ch_raw").fetchall()]
        by_trimmed = {name.strip(): name for name in raw_names}

        def col(name: str) -> str:
            return '"' + by_trimmed[name].replace('"', '""') + '"'

        selects = []
        for ours, theirs in COLUMNS.items():
            expr = f"nullif(trim({col(theirs)}), '')"
            if ours in DATE_COLUMNS:
                expr = f"try_strptime({expr}, '%d/%m/%Y')::DATE"
            selects.append(f"{expr} AS {ours}")

        con.create_function("name_key", st.supplier_key, ["VARCHAR"], "VARCHAR",
                            null_handling="special", side_effects=False)
        con.execute(f"""
            CREATE OR REPLACE TABLE companies AS
            SELECT {', '.join(selects)}, DATE '{snapshot.isoformat()}' AS snapshot_date
            FROM ch_raw
        """)
        con.execute("ALTER TABLE companies ADD COLUMN name_key VARCHAR")
        con.execute("UPDATE companies SET name_key = name_key(company_name)")

        previous = " UNION ALL ".join(
            f"SELECT nullif(trim({col(c)}), '') AS alias_name, nullif(trim({col('CompanyNumber')}), '') "
            f"AS company_number FROM ch_raw" for c in PREVIOUS_NAME_COLUMNS if c in by_trimmed)
        con.execute(f"""
            CREATE OR REPLACE TABLE company_previous_names AS
            SELECT alias_name, company_number, name_key(alias_name) AS name_key
            FROM ({previous}) WHERE alias_name IS NOT NULL
        """)
        # Token index for fuzzy-match candidate lookup: each word of each company name.
        con.execute("""
            CREATE OR REPLACE TABLE company_tokens AS
            SELECT company_number, unnest(string_split(name_key, ' ')) AS token
            FROM companies WHERE name_key IS NOT NULL
        """)
        con.execute("""
            CREATE OR REPLACE TABLE token_frequency AS
            SELECT token, count(*) AS companies FROM company_tokens GROUP BY token
        """)
        companies, previous_names = con.execute(
            "SELECT (SELECT count(*) FROM companies), (SELECT count(*) FROM company_previous_names)"
        ).fetchone()
    print(f"companies: {companies:,}  previous names: {previous_names:,}  snapshot: {snapshot}")
