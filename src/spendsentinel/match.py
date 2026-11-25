"""Match supplier names to Companies House records, then flag supplier risk.

Matching runs as a cascade, strictest rule first, and every match records the
rule that made it so precision can be measured per rule:

1. exact_name+postcode  same normalised name and same postcode
2. exact_name           same normalised name (one clear candidate)
3. previous_name        same as a name the company used to trade under
4. uk_variant           same name once a leading or trailing "UK" is ignored
5. fuzzy+postcode       similar name (token set ratio >= 85) at the same postcode
6. fuzzy_name           very similar name (token sort ratio >= 92)

A supplier with two equally good candidates is left unmatched rather than guessed.
A supplier whose name sounds like a public body ("Home Office") is only matched
when the postcode agrees too, because private companies can register such names.
Unmatched suppliers are labelled public_body, joint_venture or not_found.
"""

from __future__ import annotations

import re

import duckdb
import pandas as pd
from rapidfuzz import fuzz

from . import standardize as st
from .audit import money
from .config import COMPANIES_DB_PATH, DB_PATH, EXPORTS_DIR, REPORTS_DIR

REVIEW_SAMPLE = REPORTS_DIR / "match_review_sample.csv"
SAMPLE_SIZE = 200
COMPANY_URL = "https://find-and-update.company-information.service.gov.uk/company/"

PUBLIC_BODY = re.compile(
    r"\b(COUNCIL|BOROUGH|CITY OF|AUTHORITY|NHS|HM|HMRC|DEPARTMENT|MINISTRY|AGENCY|GOVERNMENT|"
    r"CABINET OFFICE|POLICE|FIRE AND RESCUE|TRANSPORT FOR|UNIVERSITY|COLLEGE|SCHOOL|COMMISSION|"
    r"OFFICE FOR|OFFICE OF|SECRETARY OF STATE|EMBASSY|HIGH COMMISSION|HOME OFFICE|TREASURY|COURTS?|"
    r"TRIBUNAL|MAYOR|DVLA|DVSA|NETWORK RAIL|CITY REGION|PASSENGER TRANSPORT|COMBINED (COUNTY|AUTH\w*)|"
    # common abbreviations: borough, metropolitan and district councils, combined authorities, transport bodies
    r"BC|MBC|DC|GMCA|WMCA|SYMCA|NECA|TFL|TFGM|TRANSPORT NORTH( EAST)?|TRANSPORT EAST)\b")
JOINT_VENTURE = re.compile(r"\b(JV|JOINT VENTURE|JOINT|UNINCORPORATED)\b")
# Ordinary UK registrations, preferred over branch, overseas or Royal Charter records of the same name.
STANDARD_NUMBER = re.compile(r"^(\d{8}|(SC|NI|OC|SO|NC)\d{6})$")
POSTCODE_METHODS = {"exact_name+postcode", "fuzzy+postcode"}
SOURCE_RANK = {"exact_name": 3, "previous_name": 2, "uk_variant": 1}

SUPPLIERS_SQL = """
CREATE OR REPLACE TABLE suppliers AS
SELECT
    supplier_key,
    arg_max(supplier_name, amount_pence)               AS supplier_name,
    mode(postcode)                                     AS postcode,
    count(*)                                           AS payments,
    sum(amount_gbp)                                    AS spend_gbp,
    string_agg(DISTINCT dept_code, ', ' ORDER BY dept_code) AS departments,
    min(payment_date)                                  AS first_paid,
    max(payment_date)                                  AS last_paid
FROM payments
WHERE supplier_key IS NOT NULL
GROUP BY supplier_key
"""

EXACT_CANDIDATES_SQL = """
SELECT s.supplier_key, c.company_number, c.company_name, c.postcode AS company_postcode,
       c.status, 'exact_name' AS source
FROM suppliers s JOIN ch.companies c ON c.name_key = s.supplier_key
UNION ALL
SELECT s.supplier_key, c.company_number, c.company_name, c.postcode, c.status, 'previous_name'
FROM suppliers s
JOIN ch.company_previous_names p ON p.name_key = s.supplier_key
JOIN ch.companies c ON c.company_number = p.company_number
UNION ALL
SELECT s.supplier_key, c.company_number, c.company_name, c.postcode, c.status, 'uk_variant'
FROM suppliers s
JOIN ch.companies c
  ON regexp_replace(c.name_key, '^UK | UK$', '') = regexp_replace(s.supplier_key, '^UK | UK$', '')
 AND c.name_key <> s.supplier_key
"""

# Fuzzy candidates: companies that share the supplier's rarest word, plus companies
# registered at the supplier's postcode. Rare words keep each candidate list short.
FUZZY_CANDIDATES_SQL = """
WITH words AS (
    SELECT supplier_key, unnest(string_split(supplier_key, ' ')) AS token FROM unmatched
),
rarest AS (
    -- the two rarest words, so a spelling difference in one word does not lose the candidate
    SELECT supplier_key, token, companies FROM (
        SELECT w.supplier_key, w.token, f.companies,
               row_number() OVER (PARTITION BY w.supplier_key ORDER BY f.companies) AS rn
        FROM (SELECT DISTINCT * FROM words) w JOIN ch.token_frequency f USING (token)
        WHERE length(w.token) >= 3
    ) WHERE rn <= 2
),
by_word AS (
    SELECT r.supplier_key, t.company_number
    FROM rarest r JOIN ch.company_tokens t USING (token)
    WHERE r.companies <= 5000
),
by_postcode AS (
    SELECT u.supplier_key, c.company_number
    FROM unmatched u JOIN ch.companies c ON c.postcode = u.postcode
    WHERE u.postcode IS NOT NULL
)
SELECT DISTINCT k.supplier_key, c.company_number, c.company_name, c.name_key,
       c.postcode AS company_postcode, c.status
FROM (SELECT * FROM by_word UNION SELECT * FROM by_postcode) k
JOIN ch.companies c USING (company_number)
JOIN unmatched u USING (supplier_key)
-- cheap pre-filter in SQL so Python only scores plausible names
WHERE jaro_winkler_similarity(u.supplier_key, c.name_key) >= 0.80
   OR c.postcode = u.postcode
"""

RISK_SQL = """
CREATE OR REPLACE TABLE supplier_risk AS
SELECT
    s.*,
    m.company_number, m.company_name, m.method, m.score,
    c.status, c.incorporation_date, c.accounts_next_due, c.confstmt_next_due, c.sic_1, c.snapshot_date,
    coalesce(regexp_matches(lower(c.status), 'liquidation|administration|receiver|insolvency'), false) AS insolvency,
    coalesce(lower(c.status) LIKE '%strike off%', false)        AS strike_off_proposed,
    -- Filing deadlines apply to companies and LLPs, not to Royal Charter bodies or overseas
    -- registrations, and a few days late is routine, so only flag more than 30 days overdue.
    coalesce(regexp_matches(lower(c.category),
             'limited company|limited liability partnership|pri/ltd by guar|pri/lbg/nsc|community interest|unlimited company')
             AND c.accounts_next_due < c.snapshot_date - INTERVAL 30 DAY, false)  AS accounts_overdue,
    coalesce(regexp_matches(lower(c.category),
             'limited company|limited liability partnership|pri/ltd by guar|pri/lbg/nsc|community interest|unlimited company')
             AND c.confstmt_next_due < c.snapshot_date - INTERVAL 30 DAY, false)  AS confirmation_overdue,
    coalesce(c.incorporation_date > s.first_paid - INTERVAL 1 YEAR, false) AS new_company
FROM suppliers s
JOIN supplier_matches m USING (supplier_key)
JOIN ch.companies c USING (company_number)
"""

RISK_FLAGS = {
    "insolvency": "In liquidation, administration or receivership",
    "strike_off_proposed": "Proposal to strike off the register",
    "accounts_overdue": "Annual accounts more than 30 days overdue",
    "confirmation_overdue": "Confirmation statement more than 30 days overdue",
    "new_company": "Incorporated less than a year before its first payment here",
}


def _postcode(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    clean, ok = st.clean_postcode(str(value))
    return clean if ok else None


def _pick_exact(candidates: pd.DataFrame, supplier_postcode: str | None) -> dict | None:
    c = candidates.copy()
    c["postcode_match"] = c.company_postcode.map(_postcode) == supplier_postcode
    c["active"] = c.status.eq("Active")
    c["rank"] = c.source.map(SOURCE_RANK)
    c["standard"] = c.company_number.str.match(STANDARD_NUMBER)
    keys = ["rank", "postcode_match", "active", "standard"]
    c = c.sort_values(keys, ascending=False).drop_duplicates("company_number")
    best = c.iloc[0]
    if len(c) > 1 and tuple(c.iloc[1][keys]) == tuple(best[keys]):
        return None  # two equally good companies: do not guess
    method = best.source
    if method == "exact_name" and best.postcode_match:
        method = "exact_name+postcode"
    return {"company_number": best.company_number, "company_name": best.company_name,
            "method": method, "score": 100.0}


def _pick_fuzzy(supplier_key: str, supplier_postcode: str | None, candidates: pd.DataFrame) -> dict | None:
    scored = []
    for row in candidates.itertuples():
        postcode_match = supplier_postcode is not None and _postcode(row.company_postcode) == supplier_postcode
        sort_ratio = fuzz.token_sort_ratio(supplier_key, row.name_key or "")
        set_ratio = fuzz.token_set_ratio(supplier_key, row.name_key or "")
        if postcode_match and set_ratio >= 85:
            scored.append((1, set_ratio, row.status == "Active", row, "fuzzy+postcode"))
        elif sort_ratio >= 92:
            scored.append((0, sort_ratio, row.status == "Active", row, "fuzzy_name"))
    if not scored:
        return None
    scored.sort(key=lambda s: s[:3], reverse=True)
    best = scored[0]
    if len(scored) > 1 and scored[1][:3] == best[:3] and scored[1][3].company_number != best[3].company_number:
        return None
    return {"company_number": best[3].company_number, "company_name": best[3].company_name,
            "method": best[4], "score": float(best[1])}


def _match_all(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    suppliers = con.execute("SELECT supplier_key, supplier_name, postcode FROM suppliers").df()
    suppliers["postcode"] = suppliers.postcode.map(_postcode)
    postcode_of = dict(zip(suppliers.supplier_key, suppliers.postcode, strict=True))

    exact = con.execute(EXACT_CANDIDATES_SQL).df()
    matches: dict[str, dict] = {}
    for key, group in exact.groupby("supplier_key"):
        picked = _pick_exact(group, postcode_of.get(key))
        if picked:
            matches[key] = picked

    unmatched = suppliers[~suppliers.supplier_key.isin(matches)]
    con.register("unmatched", unmatched)
    fuzzy = con.execute(FUZZY_CANDIDATES_SQL).df()
    for key, group in fuzzy.groupby("supplier_key"):
        picked = _pick_fuzzy(key, postcode_of.get(key), group)
        if picked:
            matches[key] = picked

    rows = []
    for s in suppliers.itertuples():
        found = matches.get(s.supplier_key)
        sounds_public = bool(PUBLIC_BODY.search(s.supplier_key))
        if found and (not sounds_public or found["method"] in POSTCODE_METHODS):
            rows.append({"supplier_key": s.supplier_key, **found})
        else:
            if sounds_public:
                kind = "public_body"
            elif JOINT_VENTURE.search(s.supplier_key):
                kind = "joint_venture"
            else:
                kind = "not_found"
            rows.append({"supplier_key": s.supplier_key, "company_number": None, "company_name": None,
                         "method": kind, "score": None})
    return pd.DataFrame(rows)


def _review_sample(con: duckdb.DuckDBPyConnection) -> str:
    if REVIEW_SAMPLE.exists():
        return f"kept existing {REVIEW_SAMPLE.name} (it may already hold your labels)"
    matched = con.execute("""
        SELECT r.supplier_key, r.supplier_name, r.postcode AS supplier_postcode, r.company_number,
               r.company_name, c.postcode AS company_postcode, r.status AS company_status,
               r.method, r.score, r.spend_gbp
        FROM supplier_risk r JOIN ch.companies c USING (company_number)
    """).df()
    # Stratified: every method gets at least 20 rows (or all it has), the rest in proportion.
    per_method = matched.method.value_counts()
    quota = {m: min(n, max(20, round(SAMPLE_SIZE * n / len(matched)))) for m, n in per_method.items()}
    sample = pd.concat([matched[matched.method == m].sample(q, random_state=42) for m, q in quota.items()])
    sample = sample.sort_values(["method", "supplier_name"])
    sample["company_url"] = COMPANY_URL + sample.company_number
    sample["correct"] = ""
    sample["note"] = ""
    sample.to_csv(REVIEW_SAMPLE, index=False)
    return f"wrote {len(sample)} matches to {REVIEW_SAMPLE.name}: mark 'correct' as y or n, then run `spendsentinel evaluate`"


def _write_report(con: duckdb.DuckDBPyConnection) -> None:
    coverage = con.execute("""
        SELECT m.method, count(*) AS suppliers, sum(s.spend_gbp) AS spend_gbp
        FROM supplier_matches m JOIN suppliers s USING (supplier_key)
        GROUP BY m.method ORDER BY spend_gbp DESC
    """).df()
    totals = con.execute("SELECT count(*), sum(spend_gbp) FROM suppliers").fetchone()
    flags = con.execute(" UNION ALL ".join(
        f"SELECT '{flag}' AS flag, count(*) AS suppliers, coalesce(sum(spend_gbp), 0) AS spend_gbp "
        f"FROM supplier_risk WHERE {flag}" for flag in RISK_FLAGS)).df().set_index("flag")
    flagged = con.execute(f"""
        SELECT supplier_name, company_number, status, departments, spend_gbp,
               {', '.join(RISK_FLAGS)}
        FROM supplier_risk
        WHERE insolvency OR strike_off_proposed OR accounts_overdue OR confirmation_overdue
        ORDER BY spend_gbp DESC LIMIT 15
    """).df()
    snapshot = con.execute("SELECT max(snapshot_date) FROM ch.companies").fetchone()[0]

    unregistrable = coverage.method.isin(["public_body", "joint_venture"])
    matched = coverage[~unregistrable & ~coverage.method.eq("not_found")]
    registrable_spend = totals[1] - coverage[unregistrable].spend_gbp.sum()
    out = ["# Supplier matching and risk", "",
           f"Suppliers matched to the Companies House register ({snapshot:%d %B %Y} snapshot). "
           "Generated by `spendsentinel match`.", "",
           f"**{int(matched.suppliers.sum()):,} of {totals[0]:,} suppliers matched. They cover "
           f"{matched.spend_gbp.sum() / registrable_spend:.0%} of the spend that could be on the register** "
           f"({money(matched.spend_gbp.sum())} of {money(registrable_spend)}), and "
           f"{matched.spend_gbp.sum() / totals[1]:.0%} of all spend once public bodies and joint ventures, "
           "which are not registered companies, are included.", "",
           "| Method | Suppliers | Spend |", "| --- | ---: | ---: |"]
    for r in coverage.itertuples():
        out.append(f"| {r.method} | {r.suppliers:,} | {money(r.spend_gbp)} |")
    out += ["", "`public_body` covers councils, combined authorities, NHS bodies, government departments "
            "and similar, which are not on the company register. `joint_venture` covers unincorporated "
            "joint ventures (most HS2 civil-engineering spend), which are not registered either. "
            "`not_found` had no confident match.", "",
            "## Risk flags", "",
            "Status and filing dates are as of the snapshot date, so they describe the suppliers today, "
            "not on the day they were paid.", "",
            "| Flag | Suppliers | Spend with them |", "| --- | ---: | ---: |"]
    for flag, label in RISK_FLAGS.items():
        r = flags.loc[flag]
        out.append(f"| {label} | {int(r.suppliers):,} | {money(r.spend_gbp)} |")
    if not flagged.empty:
        out += ["", "Largest flagged suppliers (insolvency, strike-off or overdue filings):", "",
                "| Supplier | Company | Status | Flags | Departments | Spend |",
                "| --- | --- | --- | --- | --- | ---: |"]
        for r in flagged.itertuples():
            names = [k.replace("_", " ") for k in RISK_FLAGS if getattr(r, k) and k != "new_company"]
            out.append(f"| {r.supplier_name} | [{r.company_number}]({COMPANY_URL}{r.company_number}) | "
                       f"{r.status} | {', '.join(names)} | {r.departments} | {money(r.spend_gbp)} |")
    out += ["", "## Match precision", ""]
    evaluation = REPORTS_DIR / "match_evaluation.md"
    if evaluation.exists():
        out.append(evaluation.read_text(encoding="utf-8").split("\n", 2)[2].strip())
    else:
        out.append(f"Not measured yet. Label `{REVIEW_SAMPLE.name}` and run `spendsentinel evaluate`.")
    out.append("")
    (REPORTS_DIR / "supplier_risk.md").write_text("\n".join(out), encoding="utf-8")


def run() -> None:
    if not COMPANIES_DB_PATH.exists():
        raise SystemExit("Companies House data not loaded. Run `spendsentinel companies` first.")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DB_PATH)) as con:
        con.execute(f"ATTACH '{COMPANIES_DB_PATH.as_posix()}' AS ch (READ_ONLY)")
        con.execute(SUPPLIERS_SQL)
        matches = _match_all(con)
        con.register("matches_df", matches)
        con.execute("CREATE OR REPLACE TABLE supplier_matches AS SELECT * FROM matches_df")
        con.execute(RISK_SQL)
        con.execute("SELECT * FROM supplier_matches").df().to_csv(EXPORTS_DIR / "supplier_matches.csv", index=False)
        con.execute("SELECT * FROM supplier_risk").df().to_csv(EXPORTS_DIR / "supplier_risk.csv", index=False)
        sample_note = _review_sample(con)
        _write_report(con)
        summary = con.execute("""
            SELECT count(*) FILTER (WHERE company_number IS NOT NULL), count(*) FROM supplier_matches
        """).fetchone()
    print(f"matched {summary[0]:,} of {summary[1]:,} suppliers")
    print(sample_note)
    print("report: reports/supplier_risk.md")


def evaluate() -> None:
    if not REVIEW_SAMPLE.exists():
        raise SystemExit("No review sample yet. Run `spendsentinel match` first.")
    sample = pd.read_csv(REVIEW_SAMPLE, dtype=str).fillna("")
    answer = sample.correct.str.strip().str.lower()
    labelled = sample[answer.isin(["y", "yes", "1", "true", "n", "no", "0", "false"])].copy()
    if labelled.empty:
        raise SystemExit(f"No labels in {REVIEW_SAMPLE.name} yet: put y or n in the 'correct' column.")
    labelled["ok"] = answer[labelled.index].isin(["y", "yes", "1", "true"])
    by_method = labelled.groupby("method").ok.agg(["count", "sum"])
    lines = ["# Match evaluation", "",
             f"Hand-checked {len(labelled)} of {len(sample)} sampled matches: "
             f"**{labelled.ok.mean():.1%} precision** ({int(labelled.ok.sum())} correct).", "",
             "| Method | Checked | Correct | Precision |", "| --- | ---: | ---: | ---: |"]
    for method, r in by_method.iterrows():
        lines.append(f"| {method} | {int(r['count'])} | {int(r['sum'])} | {r['sum'] / r['count']:.0%} |")
    lines.append("")
    (REPORTS_DIR / "match_evaluation.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
