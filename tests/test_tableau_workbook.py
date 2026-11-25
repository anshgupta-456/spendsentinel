import re
import xml.etree.ElementTree as ET

import pytest

from spendsentinel.tableau_workbook import SOURCES, workbook_xml


@pytest.mark.parametrize("include_risk", [True, False])
def test_workbook_is_well_formed_with_unique_sheet_names(include_risk):
    root = ET.fromstring(workbook_xml(include_risk).encode("utf-8"))
    worksheets = [w.get("name") for w in root.iter("worksheet")]
    dashboards = [d.get("name") for d in root.iter("dashboard")]
    names = worksheets + dashboards
    assert len(names) == len(set(names)), "Tableau needs every sheet and dashboard name to be unique"
    assert ("Supplier risk" in worksheets) is include_risk


def test_every_dashboard_zone_points_at_a_real_sheet():
    root = ET.fromstring(workbook_xml(True).encode("utf-8"))
    worksheets = {w.get("name") for w in root.iter("worksheet")}
    for zone in root.iter("zone"):
        if zone.get("name"):
            assert zone.get("name") in worksheets


def test_every_field_on_a_shelf_is_declared():
    root = ET.fromstring(workbook_xml(True).encode("utf-8"))
    for sheet in root.iter("worksheet"):
        declared = {c.get("name") for c in sheet.iter("column-instance")}
        shelves = " ".join(filter(None, [sheet.findtext(".//rows"), sheet.findtext(".//cols")]))
        for ref in re.findall(r"\]\.(\[[^\]]+\])", shelves):
            assert ref in declared, f"{sheet.get('name')}: {ref} used but not declared"


def test_relation_columns_match_the_exported_files():
    # The column lists are hand-written, so keep them in step with tableau.py's exports.
    assert [c for c, _ in SOURCES["suppliers"][2]] == ["department", "supplier_name", "rank", "spend_gbp", "share", "payments"]
