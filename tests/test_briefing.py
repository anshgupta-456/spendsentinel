from spendsentinel.briefing import check, check_largest_change, extract_figures

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


def test_digits_inside_names_are_not_counts():
    figures = extract_figures("Top supplier was BALFOUR BEATTY VINCI JV - HS2 (N2) on the A19, with 37 payments.")
    assert [f.text for f in figures] == ["37"]


CHANGE_FACTS = {
    "largest_change_department": "Department for Work and Pensions",
    "departments": [{"department": "Department for Transport"},
                    {"department": "Department for Work and Pensions"}],
}


def test_largest_change_claim_is_checked():
    right = "Spend was £4.09bn. The Department for Work and Pensions had the largest change, up 38.1%."
    wrong = "Spend was £4.09bn. The Department for Transport saw the largest change in spend, up 4.2%."
    silent = "Spend was £4.09bn."
    assert check_largest_change(right, CHANGE_FACTS) == {"named": "Department for Work and Pensions", "correct": True}
    assert check_largest_change(wrong, CHANGE_FACTS)["correct"] is False
    assert check_largest_change(silent, CHANGE_FACTS)["correct"] is None


def test_invented_figures_are_caught():
    results = check("Spend was £3.9bn and 41 duplicates were found.", FACTS)
    assert [r["figure"] for r in results if not r["verified"]] == ["£3.9bn", "41"]
