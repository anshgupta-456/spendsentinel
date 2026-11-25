"""Build the static dashboard in _site/: the web template plus the data, embedded as JSON.

Everything the page shows comes from DuckDB tables built by `build`, `audit` and
`match`, and from the committed briefing reports. The page has no server and no
external libraries, so it can be hosted on GitHub Pages or opened from disk.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from .config import DB_PATH, REPORTS_DIR, ROOT, load_settings

SITE_DIR = ROOT / "_site"
WEB_DIR = Path(__file__).parent / "web"
PREFERRED_BRIEFING_MODEL = "llama3.1:8b"
DATA_PLACEHOLDER = "/*__SPENDSENTINEL_DATA__*/null"


def _clean(value):
    if isinstance(value, float):
        return None if math.isnan(value) else round(value, 2)
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if hasattr(value, "item"):  # numpy scalars
        return _clean(value.item())
    return value


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict]:
    cursor = con.execute(sql, params or [])
    names = [d[0] for d in cursor.description]
    return [{n: _clean(v) for n, v in zip(names, row, strict=True)} for row in cursor.fetchall()]


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> dict:
    return _rows(con, sql)[0]


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0])


def _briefing() -> dict | None:
    """The latest briefing from the preferred model, with its check results."""
    log = REPORTS_DIR / "briefings" / "runs.jsonl"
    if not log.exists():
        return None
    runs = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    preferred = [r for r in runs if r["model"] == PREFERRED_BRIEFING_MODEL] or runs
    if not preferred:
        return None
    run = max(preferred, key=lambda r: (r["month"], r["run_at"]))
    folder = re.sub(r"[^A-Za-z0-9._-]+", "-", run["model"])
    page = REPORTS_DIR / "briefings" / folder / f"{run['month']}.md"
    if not page.exists():
        return None
    body = page.read_text(encoding="utf-8").split("## Figure check")[0].splitlines()[3:]
    text = "\n".join(body).strip()
    stats = {"briefings": 0, "first_ok": 0, "first_all": 0, "final_ok": 0, "final_all": 0, "claims_ok": 0, "claims": 0}
    latest: dict[tuple[str, str], dict] = {}
    for r in runs:
        latest[(r["model"], r["month"])] = r
    for r in latest.values():
        stats["briefings"] += 1
        stats["first_ok"] += r["first_draft"]["verified"]
        stats["first_all"] += r["first_draft"]["figures"]
        stats["final_ok"] += r["final"]["verified"]
        stats["final_all"] += r["final"]["figures"]
        correct = (r.get("largest_change") or {}).get("correct")
        if correct is not None:
            stats["claims"] += 1
            stats["claims_ok"] += int(correct)
    return {"model": run["model"], "month": run["month"], "text": text,
            "figures": run["final"]["figures"], "verified": run["final"]["verified"],
            "largest_change": run.get("largest_change"), "experiment": stats}


def _rules(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Every data-quality rule with its description, including rules that never fired."""
    from .build import RULES

    counts = {r["rule"]: r for r in _rows(con, """
        SELECT rule, count(*) AS flagged, coalesce(sum(abs(amount_pence)), 0) / 100.0 AS amount
        FROM quality_issues GROUP BY rule
    """)}
    rules = []
    for rule, (level, checks, action) in RULES.items():
        found = counts.get(rule, {})
        rules.append({"rule": rule, "level": level, "checks": checks, "action": action,
                      "flagged": found.get("flagged", 0), "amount": found.get("amount", 0.0)})
    return sorted(rules, key=lambda r: -r["flagged"])


def build_data(con: duckdb.DuckDBPyConnection) -> dict:
    names = {d.code: d.name for d in load_settings().departments}
    scope = _one(con, """
        SELECT count(*) AS payments, sum(amount_gbp) AS spend, count(DISTINCT dept_code) AS departments,
               min(period_month) AS first_month, max(period_month) AS last_month,
               (SELECT count(*) FROM files) AS files, (SELECT count(*) FROM payment_lines) AS lines
        FROM payments
    """)
    recon = _rows(con, (Path(__file__).parents[2] / "sql" / "build" / "reconciliation.sql").read_text(encoding="utf-8"))
    for r in recon:
        r["balanced"] = r["raw_pence"] == r["kept_pence"] + r["dropped_pence"] and r["kept_pence"] == r["payments_pence"]

    tier_rank = ("CASE confidence WHEN 'high' THEN 5 WHEN 'medium' THEN 4 WHEN 'low' THEN 3 "
                 "WHEN 'no_reference' THEN 2 ELSE 1 END")
    tiers = _rows(con, f"""
        WITH best AS (
            SELECT second_payment_id, max(amount_gbp) AS amount, max({tier_rank}) AS level
            FROM audit_possible_duplicates GROUP BY second_payment_id
        )
        SELECT CASE level WHEN 5 THEN 'high' WHEN 4 THEN 'medium' WHEN 3 THEN 'low'
                          WHEN 2 THEN 'no_reference' ELSE 'bulk' END AS tier,
               count(*) AS payments, sum(amount) AS amount
        FROM best GROUP BY level ORDER BY level DESC
    """)
    pairs = _rows(con, """
        SELECT dept_code AS dept, supplier_name AS supplier, amount_gbp AS amount, first_date, second_date,
               first_ref, second_ref, confidence AS tier, first_expense_type AS expense_type
        FROM audit_possible_duplicates
        WHERE confidence IN ('high', 'medium', 'low')
        ORDER BY amount_gbp DESC LIMIT 150
    """)

    data = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        "departments": [{"code": c, "name": names.get(c, c)} for c in sorted(names)],
        "scope": scope,
        "monthly": _rows(con, """
            SELECT dept_code AS dept, strftime(period_month, '%Y-%m') AS month, spend_gbp AS spend, payments
            FROM monthly_trend ORDER BY dept_code, period_month
        """),
        "top_suppliers": _rows(con, """
            SELECT dept_code AS dept, supplier_name AS name, spend_gbp AS spend, payments, share
            FROM supplier_ranked WHERE rank <= 10 ORDER BY dept_code, rank
        """),
        "concentration": _rows(con, "SELECT * FROM audit_supplier_concentration ORDER BY spend_gbp DESC"),
        "duplicates": {"tiers": tiers, "pairs": pairs},
        "quality": {"reconciliation": recon, "rules": _rules(con)},
        "briefing": _briefing(),
        "risk": None,
        "downloads": sorted(p.name for p in (ROOT / "exports" / "tableau").glob("*.*") if p.suffix in (".csv", ".twbx")),
    }

    if _has_table(con, "supplier_risk"):
        coverage = _rows(con, """
            SELECT m.method, count(*) AS suppliers, sum(s.spend_gbp) AS spend
            FROM supplier_matches m JOIN suppliers s USING (supplier_key)
            GROUP BY m.method ORDER BY spend DESC
        """)
        flags = {
            "insolvency": "In liquidation, administration or receivership",
            "strike_off_proposed": "Proposal to strike off",
            "accounts_overdue": "Accounts 30+ days overdue",
            "confirmation_overdue": "Confirmation statement 30+ days overdue",
        }
        data["risk"] = {
            "coverage": coverage,
            "flags": [{"flag": k, "label": label, **_one(con, f"""
                          SELECT count(*) AS suppliers, coalesce(sum(spend_gbp), 0) AS spend
                          FROM supplier_risk WHERE {k}""")} for k, label in flags.items()],
            "suppliers": _rows(con, f"""
                SELECT supplier_name AS name, company_number, status, departments, spend_gbp AS spend,
                       {', '.join(flags)}
                FROM supplier_risk
                WHERE {' OR '.join(flags)}
                ORDER BY spend_gbp DESC LIMIT 40
            """),
        }
    return data


def run() -> Path:
    # Empty the folder rather than delete it: it may be a mounted volume (Docker) or a served directory.
    SITE_DIR.mkdir(exist_ok=True)
    for child in SITE_DIR.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(WEB_DIR, SITE_DIR, dirs_exist_ok=True)
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        data = build_data(con)
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    index = SITE_DIR / "index.html"
    html = index.read_text(encoding="utf-8")
    if DATA_PLACEHOLDER not in html:
        raise RuntimeError("index.html is missing the data placeholder")
    index.write_text(html.replace(DATA_PLACEHOLDER, payload), encoding="utf-8")
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    # Publish the Tableau extracts next to the page, so the data behind it can be downloaded.
    tableau_dir = ROOT / "exports" / "tableau"
    if tableau_dir.exists():
        (SITE_DIR / "data").mkdir(exist_ok=True)
        for data_file in [*tableau_dir.glob("*.csv"), *tableau_dir.glob("*.twbx")]:
            shutil.copy2(data_file, SITE_DIR / "data" / data_file.name)
    size = sum(p.stat().st_size for p in SITE_DIR.rglob("*") if p.is_file())
    print(f"site: {SITE_DIR.relative_to(ROOT)}/index.html ({size / 1024:.0f} KB)")
    return index
