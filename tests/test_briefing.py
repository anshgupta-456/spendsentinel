from spendsentinel.briefing import check, extract_figures

FACTS = {
    "total_spend": 3_456_789_012.0,
    "departments": [
        {"spend": 1_234_567.0, "payments": 1_520, "change_vs_previous_month_pct": -12.4},
    ],
    "possible_duplicate_payments": {"payments": 37, "amount": 2_048_000.0},
}


def test_extracts_money_percent_and_counts_but_not_dates():
    figures = extract_figures("In July 2026 spend was £3.46bn across 1,520 payments, down 12.4% on 3 July.")
    kinds = [(f.text, f.kind) for f in figures]
    assert ("£3.46bn", "money") in kinds
    assert ("12.4%", "percent") in kinds
    assert ("1,520", "count") in kinds
    assert all(f.text not in ("2026", "3") for f in figures)


def test_rounded_figures_match_their_facts():
    results = check("Total spend was £3.46bn. HMRC spent £1.2M over 1,520 payments, down 12.4%. "
                    "37 possible duplicates worth £2.0M were flagged.", FACTS)
    assert all(r["verified"] for r in results), results


def test_invented_figures_are_caught():
    results = check("Spend was £3.9bn and 41 duplicates were found.", FACTS)
    assert [r["figure"] for r in results if not r["verified"]] == ["£3.9bn", "41"]
