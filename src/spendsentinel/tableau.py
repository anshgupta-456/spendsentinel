"""Write analysis-ready CSVs for Tableau Public to exports/tableau/.

Tableau works best from one wide table, so `spend_payments.csv` carries one row
per payment with the department name, duplicate tier and the supplier's Companies
House status already joined in. The other files are small lookup tables.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from .config import DB_PATH, EXPORTS_DIR, load_settings

TABLEAU_DIR = EXPORTS_DIR / "tableau"

BEST_TIER = """
    SELECT second_payment_id AS payment_id,
           CASE max(CASE confidence WHEN 'high' THEN 5 WHEN 'medium' THEN 4 WHEN 'low' THEN 3
                                    WHEN 'no_reference' THEN 2 ELSE 1 END)
                WHEN 5 THEN 'high' WHEN 4 THEN 'medium' WHEN 3 THEN 'low'
                WHEN 2 THEN 'no_reference' ELSE 'bulk' END AS duplicate_tier
    FROM audit_possible_duplicates GROUP BY second_payment_id
"""


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0])


def run() -> None:
    TABLEAU_DIR.mkdir(parents=True, exist_ok=True)
    departments = pd.DataFrame([{"dept_code": d.code, "department": d.name} for d in load_settings().departments])
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        con.register("departments", departments)
        matched = _has_table(con, "supplier_risk")
        supplier_cols = """,
            m.company_number, m.method AS match_method, r.status AS company_status,
            coalesce(r.insolvency, false) AS supplier_insolvent,
            coalesce(r.strike_off_proposed, false) AS supplier_strike_off_proposed,
            coalesce(r.accounts_overdue, false) AS supplier_accounts_overdue,
            coalesce(r.confirmation_overdue, false) AS supplier_confirmation_overdue""" if matched else ""
        supplier_joins = """
            LEFT JOIN supplier_matches m ON m.supplier_key = p.supplier_key
            LEFT JOIN supplier_risk r ON r.supplier_key = p.supplier_key""" if matched else ""
        tables = {
            "spend_payments": f"""
                SELECT p.payment_id, p.dept_code, d.department, p.payment_date, p.period_month,
                       p.supplier_name, p.expense_type, p.expense_area, p.description,
                       p.amount_gbp, p.n_lines,
                       t.duplicate_tier,
                       coalesce(t.duplicate_tier IN ('high', 'medium', 'low'), false) AS possible_duplicate,
                       abs(p.amount_pence) < 2500000 AS below_publication_threshold{supplier_cols}
                FROM payments p
                JOIN departments d USING (dept_code)
                LEFT JOIN ({BEST_TIER}) t ON t.payment_id = p.payment_id{supplier_joins}
                ORDER BY p.payment_date
            """,
            "duplicate_pairs": """
                SELECT a.*, d.department FROM audit_possible_duplicates a JOIN departments d USING (dept_code)
                ORDER BY amount_gbp DESC
            """,
            "supplier_ranking": """
                SELECT r.*, d.department FROM supplier_ranked r JOIN departments d USING (dept_code)
                ORDER BY dept_code, rank
            """,
            "quality_issues_by_rule": """
                SELECT dept_code, level, rule, count(*) AS flagged,
                       coalesce(sum(abs(amount_pence)), 0) / 100.0 AS amount_gbp
                FROM quality_issues GROUP BY ALL ORDER BY flagged DESC
            """,
        }
        for name, query in tables.items():
            frame = con.execute(query).df()
            frame.to_csv(TABLEAU_DIR / f"{name}.csv", index=False)
            print(f"{name}.csv: {len(frame):,} rows")
