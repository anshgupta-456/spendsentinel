"""A monthly spend briefing written by a local LLM, with every figure checked against SQL.

Guardrails:
- The model only sees aggregated facts computed in SQL, never payment rows.
- It runs locally through Ollama, so no data leaves the machine.
- Every number in its text is extracted and matched to a fact. Anything that
  does not match is reported, and the model gets one chance to correct it.
- Each run is logged, so accuracy is measured rather than assumed.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime

import duckdb
import requests

from .audit import money
from .config import DB_PATH, REPORTS_DIR, load_settings

# Point this at another machine (for example a GPU server) with the OLLAMA_URL environment variable.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/api/chat"
DEFAULT_MODEL = "llama3.2:3b"
BRIEFINGS_DIR = REPORTS_DIR / "briefings"
RUN_LOG = BRIEFINGS_DIR / "runs.jsonl"

SYSTEM_PROMPT = """You write short monthly spending briefings for a procurement manager.
Rules:
- Use ONLY the facts in the JSON you are given. Do not estimate, round differently or invent figures.
- Copy every money figure exactly as written in the *_text fields (for example "£1.23bn" or "£45.6M").
- Write percentages exactly as given in the *_pct_text fields.
- At most 150 words, plain sentences, no headings, no bullet points.
- Cover: total spend, which department changed most from the previous month, the largest suppliers,
  and the possible duplicate payments flagged for review."""


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

def _pct_text(value: float | None) -> str | None:
    return None if value is None else f"{value:+.1f}%"


def month_facts(con: duckdb.DuckDBPyConnection, month: date) -> dict:
    names = {d.code: d.name for d in load_settings().departments}
    depts = con.execute("""
        SELECT dept_code, spend_gbp, payments, suppliers, change_gbp,
               spend_gbp - change_gbp AS previous_gbp
        FROM monthly_trend WHERE period_month = ? ORDER BY spend_gbp DESC
    """, [month]).fetchall()
    departments = []
    for code, spend, payments, suppliers, change, previous in depts:
        change_pct = (change / previous * 100) if change is not None and previous else None
        top = con.execute("""
            SELECT supplier_name, sum(amount_gbp) AS spend FROM payments
            WHERE dept_code = ? AND period_month = ? AND supplier_name IS NOT NULL
            GROUP BY supplier_name ORDER BY spend DESC LIMIT 3
        """, [code, month]).fetchall()
        departments.append({
            "department": names.get(code, code),
            "spend": round(spend, 2), "spend_text": money(spend),
            "payments": int(payments), "suppliers": int(suppliers),
            "change_vs_previous_month_pct": None if change_pct is None else round(change_pct, 1),
            "change_vs_previous_month_pct_text": _pct_text(change_pct),
            "top_suppliers": [{"name": n, "spend": round(s, 2), "spend_text": money(s)} for n, s in top],
        })
    duplicates = con.execute("""
        SELECT count(DISTINCT second_payment_id), coalesce(sum(amount_gbp), 0)
        FROM audit_possible_duplicates
        WHERE confidence IN ('high', 'medium', 'low')
          AND date_trunc('month', second_date) = ?
    """, [month]).fetchone()
    total = sum(d["spend"] for d in departments)
    return {
        "month": f"{month:%B %Y}",
        "total_spend": round(total, 2), "total_spend_text": money(total),
        "departments": departments,
        "possible_duplicate_payments": {"payments": int(duplicates[0]), "amount": round(duplicates[1], 2),
                                        "amount_text": money(duplicates[1])},
    }


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------

MONEY = re.compile(r"(?P<neg>-)?£\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s?(?P<unit>bn|billion|m|million|k|thousand)?\b",
                   re.IGNORECASE)
PERCENT = re.compile(r"(?P<sign>[+-])?(?P<num>\d+(?:\.\d+)?)\s?%")
# Digits that are part of a name ("HS2 (N2)", "A19", "S1") are not counts, so a count may not touch a letter.
COUNT = re.compile(r"(?<![\w.,£])\d{1,3}(?:,\d{3})+(?![\w.,])|(?<![\w.,£])\d+(?![\w.,%])")
UNITS = {"bn": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3}


@dataclass
class Figure:
    text: str
    kind: str      # money, percent or count
    value: float
    tolerance: float


def _decimals(number: str) -> int:
    return len(number.split(".")[1]) if "." in number else 0


def extract_figures(text: str) -> list[Figure]:
    figures, taken = [], []
    for m in MONEY.finditer(text):
        scale = UNITS.get((m.group("unit") or "").lower(), 1)
        number = m.group("num").replace(",", "")
        value = float(number) * scale * (-1 if m.group("neg") else 1)
        # A figure written to 1 decimal place is right if the fact rounds to it.
        figures.append(Figure(m.group(0).strip(), "money", value, 0.5 * 10 ** -_decimals(number) * scale + 0.005))
        taken.append(m.span())
    for m in PERCENT.finditer(text):
        value = float(m.group("num")) * (-1 if m.group("sign") == "-" else 1)
        figures.append(Figure(m.group(0).strip(), "percent", value, 0.5 * 10 ** -_decimals(m.group("num")) + 1e-9))
        taken.append(m.span())
    for m in COUNT.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        value = float(m.group(0).replace(",", ""))
        if 1900 <= value <= 2100 or value <= 31 and re.search(
                r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b",
                text[max(0, m.start() - 12):m.end() + 12]):
            continue  # years and day-of-month numbers are not claims about spend
        figures.append(Figure(m.group(0), "count", value, 0.0))
    return figures


def _fact_values(facts) -> list[float]:
    values = []
    if isinstance(facts, dict):
        for value in facts.values():
            values += _fact_values(value)
    elif isinstance(facts, list):
        for value in facts:
            values += _fact_values(value)
    elif isinstance(facts, (int, float)) and not isinstance(facts, bool):
        values.append(float(facts))
    return values


def check(text: str, facts: dict) -> list[dict]:
    known = _fact_values(facts)
    results = []
    for f in extract_figures(text):
        candidates = known if f.kind != "percent" else [v for v in known] + [abs(v) for v in known]
        match = next((v for v in candidates if abs(v - f.value) <= f.tolerance), None)
        if match is None and f.kind == "percent":
            match = next((v for v in known if abs(abs(v) - abs(f.value)) <= f.tolerance), None)
        results.append({"figure": f.text, "kind": f.kind, "verified": match is not None, "fact": match})
    return results


# ---------------------------------------------------------------------------
# Generating
# ---------------------------------------------------------------------------

def _chat(model: str, messages: list[dict]) -> str:
    response = requests.post(OLLAMA_URL, json={
        "model": model, "messages": messages, "stream": False,
        "options": {"temperature": 0, "seed": 42},
    }, timeout=600)
    response.raise_for_status()
    return response.json()["message"]["content"].strip()


def brief_month(con: duckdb.DuckDBPyConnection, month: date, model: str = DEFAULT_MODEL) -> dict:
    facts = month_facts(con, month)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Facts:\n" + json.dumps(facts, indent=1)}]
    attempts = []
    text = _chat(model, messages)
    results = check(text, facts)
    attempts.append({"text": text, "results": results})
    wrong = [r["figure"] for r in results if not r["verified"]]
    if wrong:
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "These figures are not in the facts: " + ", ".join(wrong)
                      + ". Rewrite the briefing using only figures copied from the facts."}]
        text = _chat(model, messages)
        results = check(text, facts)
        attempts.append({"text": text, "results": results})

    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    verified = sum(r["verified"] for r in results)
    lines = [f"# Spend briefing: {facts['month']}", "",
             f"Written by `{model}` (local, temperature 0) from SQL facts only. "
             f"Figures checked: **{verified} of {len(results)} verified**"
             + (" after one correction." if len(attempts) > 1 else "."), "",
             text, "", "## Figure check", "", "| Figure | Type | Verified | Matching fact |",
             "| --- | --- | :---: | ---: |"]
    for r in results:
        fact = "" if r["fact"] is None else f"{r['fact']:,.2f}"
        status = "yes" if r["verified"] else "NO"
        lines.append(f"| {r['figure']} | {r['kind']} | {status} | {fact} |")
    if len(attempts) > 1:
        first = attempts[0]["results"]
        lines += ["", f"First draft: {sum(r['verified'] for r in first)} of {len(first)} figures verified. "
                  "Unverified: " + ", ".join(r["figure"] for r in first if not r["verified"]) + "."]
    path = BRIEFINGS_DIR / f"{month:%Y-%m}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    record = {"run_at": datetime.now().isoformat(timespec="seconds"), "model": model,
              "month": f"{month:%Y-%m}", "attempts": len(attempts),
              "first_draft": {"figures": len(attempts[0]["results"]),
                              "verified": sum(r["verified"] for r in attempts[0]["results"])},
              "final": {"figures": len(results), "verified": verified}}
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def summarize_runs() -> None:
    """Write reports/briefings/README.md: the latest run for each month and model."""
    if not RUN_LOG.exists():
        return
    latest: dict[tuple[str, str], dict] = {}
    for line in RUN_LOG.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        latest[(record["model"], record["month"])] = record
    runs = sorted(latest.values(), key=lambda r: (r["model"], r["month"]))
    first_ok = sum(r["first_draft"]["verified"] for r in runs)
    first_all = sum(r["first_draft"]["figures"] for r in runs)
    final_ok = sum(r["final"]["verified"] for r in runs)
    final_all = sum(r["final"]["figures"] for r in runs)
    corrected = sum(r["attempts"] > 1 for r in runs)
    lines = ["# Briefing experiment", "",
             "Each month's briefing is written by a local model from SQL facts, then every figure in it is "
             "checked against those facts. If any figure fails, the model is told which ones and gets one "
             "rewrite. This table keeps the latest run per month.", "",
             f"**First drafts: {first_ok} of {first_all} figures verified "
             f"({first_ok / first_all:.0%}). After correction: {final_ok} of {final_all} "
             f"({final_ok / final_all:.0%}).** {corrected} of {len(runs)} briefings needed a rewrite.", "",
             "| Month | Model | First draft verified | Final verified | Attempts |",
             "| --- | --- | ---: | ---: | ---: |"]
    for r in runs:
        lines.append(f"| [{r['month']}]({r['month']}.md) | {r['model']} | "
                     f"{r['first_draft']['verified']} / {r['first_draft']['figures']} | "
                     f"{r['final']['verified']} / {r['final']['figures']} | {r['attempts']} |")
    lines += ["", "Limits: the check confirms each figure appears in the facts. It does not confirm the "
              "figure is attached to the right department or supplier, so a human still reads the text.", ""]
    (BRIEFINGS_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")


def run(month: str | None = None, all_months: bool = False, model: str = DEFAULT_MODEL) -> None:
    with duckdb.connect(str(DB_PATH)) as con:
        months = [r[0] for r in con.execute("""
            SELECT period_month FROM monthly_trend GROUP BY period_month
            HAVING count(DISTINCT dept_code) = (SELECT count(DISTINCT dept_code) FROM monthly_trend)
            ORDER BY period_month
        """).fetchall()]
        if month:
            months = [datetime.strptime(month, "%Y-%m").date()]
        elif not all_months:
            months = months[-1:]
        for m in months:
            r = brief_month(con, m, model)
            print(f"{r['month']}: first draft {r['first_draft']['verified']}/{r['first_draft']['figures']} verified, "
                  f"final {r['final']['verified']}/{r['final']['figures']} after {r['attempts']} attempt(s)")
    summarize_runs()
