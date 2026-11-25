import pandas as pd

from spendsentinel.match import PUBLIC_BODY, _pick_exact, _pick_fuzzy


def candidates(rows):
    return pd.DataFrame(rows, columns=["company_number", "company_name", "company_postcode", "status", "source"])


def test_exact_prefers_postcode_then_active():
    c = candidates([
        ("001", "ACME LTD", "AB1 2CD", "Dissolved", "exact_name"),
        ("002", "ACME LIMITED", "ZZ9 9ZZ", "Active", "exact_name"),
    ])
    picked = _pick_exact(c, "AB1 2CD")
    assert picked["company_number"] == "001"
    assert picked["method"] == "exact_name+postcode"


def test_exact_refuses_to_guess_between_equal_candidates():
    c = candidates([
        ("001", "ACME LTD", "AB1 2CD", "Active", "exact_name"),
        ("002", "ACME PLC", "ZZ9 9ZZ", "Active", "exact_name"),
    ])
    assert _pick_exact(c, None) is None


def test_exact_prefers_standard_registration_over_branch_record():
    # Fujitsu Services Limited has both a company record and a Northern Ireland branch record.
    c = candidates([
        ("NF000668", "FUJITSU SERVICES LIMITED", None, "Active", "exact_name"),
        ("00096056", "FUJITSU SERVICES LIMITED", None, "Active", "exact_name"),
    ])
    assert _pick_exact(c, None)["company_number"] == "00096056"


def test_uk_variant_ranks_below_exact_name():
    c = candidates([
        ("001", "COGNIZANT TECHNOLOGY SOLUTIONS UK LIMITED", None, "Active", "uk_variant"),
    ])
    assert _pick_exact(c, None)["method"] == "uk_variant"


def test_exact_name_beats_previous_name():
    c = candidates([
        ("001", "NEW NAME LTD", "AB1 2CD", "Active", "previous_name"),
        ("002", "ACME LTD", "ZZ9 9ZZ", "Active", "exact_name"),
    ])
    assert _pick_exact(c, None)["company_number"] == "002"


def test_fuzzy_accepts_close_name_and_postcode_rescue():
    c = pd.DataFrame([
        ("100", "KYNDRYL UK LIMITED", "KYNDRYL UK", "PO6 3AU", "Active"),
        ("200", "KPMG LLP", "KPMG", "E14 5GL", "Active"),
    ], columns=["company_number", "company_name", "name_key", "company_postcode", "status"])
    assert _pick_fuzzy("KYNDRYL UK", None, c)["method"] == "fuzzy_name"
    picked = _pick_fuzzy("CSL KPMG", "E14 5GL", c)
    assert picked["company_number"] == "200" and picked["method"] == "fuzzy+postcode"
    assert _pick_fuzzy("CSL KPMG", None, c) is None  # without the postcode it is too weak


def test_public_bodies_are_recognised():
    assert PUBLIC_BODY.search("CITY OF WESTMINSTER POLAR")
    assert PUBLIC_BODY.search("GREATER LONDON AUTHORITY")
    assert PUBLIC_BODY.search("GMCA")
    assert PUBLIC_BODY.search("TORBAY BC")
    assert PUBLIC_BODY.search("LANCASHIRE COMBINED COUNTY AUTHORIT")
    assert not PUBLIC_BODY.search("CAPGEMINI")
    assert not PUBLIC_BODY.search("ABC CONSULTING")
