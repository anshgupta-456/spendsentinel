"""Generate a packaged Tableau workbook (.twbx) with the sheets and dashboards pre-built.

A .twbx is a zip holding the workbook XML and its data files, so it opens in
Tableau Public with no connection setup. The sheets mirror the web dashboard:
headline figures, monthly trend, flagged spend, top suppliers, supplier risk
and data quality.
"""

from __future__ import annotations

import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import quoteattr

WORKBOOK_NAME = "SpendSentinel"

# Relation columns per data file: (name, Tableau datatype). Text columns that hold
# True/False are kept as strings and compared to "True" in calculations.
SOURCES = {
    "payments": ("Spend payments", "spend_payments.csv", [
        ("payment_id", "string"), ("dept_code", "string"), ("department", "string"),
        ("payment_date", "date"), ("period_month", "date"), ("supplier_name", "string"),
        ("expense_type", "string"), ("expense_area", "string"), ("description", "string"),
        ("amount_gbp", "real"), ("n_lines", "integer"), ("duplicate_tier", "string"),
        ("possible_duplicate", "string"), ("below_publication_threshold", "string"),
        ("company_number", "string"), ("match_method", "string"), ("company_status", "string"),
        ("supplier_insolvent", "string"), ("supplier_strike_off_proposed", "string"),
        ("supplier_accounts_overdue", "string"), ("supplier_confirmation_overdue", "string"),
    ]),
    "suppliers": ("Top suppliers", "top_suppliers.csv", [
        ("department", "string"), ("supplier_name", "string"), ("rank", "integer"),
        ("spend_gbp", "real"), ("share", "real"), ("payments", "integer"),
    ]),
    "risk": ("Risky suppliers", "risky_suppliers.csv", [
        ("supplier_name", "string"), ("company_number", "string"), ("status", "string"),
        ("departments", "string"), ("spend_gbp", "real"), ("flags", "string"),
    ]),
    "quality": ("Data quality", "quality_issues_by_rule.csv", [
        ("dept_code", "string"), ("level", "string"), ("rule", "string"),
        ("flagged", "integer"), ("amount_gbp", "real"),
    ]),
}

# Calculated fields: (internal name, caption, datatype, role, type, formula).
CALCS = {
    "payments": [
        ("Calculation_flagged", "Flagged spend", "real", "measure", "quantitative",
         'IF [possible_duplicate] = "True" THEN [amount_gbp] END'),
        ("Calculation_payments", "Payments", "integer", "measure", "quantitative", "COUNTD([payment_id])"),
    ],
}

DERIVATION = {"sum": "Sum", "none": "None", "tmn": "Month-Trunc", "usr": "User"}
SUFFIX_TYPE = {"qk": "quantitative", "nk": "nominal", "ok": "ordinal"}


def _ds(key: str) -> str:
    return f"federated.spendsentinel_{key}"


@dataclass
class Sheet:
    name: str
    source: str
    mark: str
    rows: list[tuple[str, str, str]]          # (derivation prefix, field, suffix)
    cols: list[tuple[str, str, str]]
    text: tuple[str, str, str] | None = None
    color: tuple[str, str, str] | None = None
    sort: tuple[tuple[str, str, str], tuple[str, str, str]] | None = None  # (dimension, by measure)
    uid: str = field(default_factory=lambda: str(uuid.uuid4()).upper())


def _inst(source: str, inst: tuple[str, str, str]) -> str:
    prefix, name, suffix = inst
    return f"[{_ds(source)}].[{prefix}:{name}:{suffix}]"


def _shelf(source: str, items: list[tuple[str, str, str]]) -> str:
    refs = [_inst(source, i) for i in items]
    return refs[0] if len(refs) == 1 else "(" + " / ".join(refs) + ")"


def _field_column(source: str, name: str) -> str:
    """The <column> definition of a field (relation column or calculated field)."""
    for calc in CALCS.get(source, []):
        if calc[0] == name:
            internal, caption, datatype, role, typ, formula = calc
            return (f"<column caption={quoteattr(caption)} datatype='{datatype}' name='[{internal}]' "
                    f"role='{role}' type='{typ}'><calculation class='tableau' formula={quoteattr(formula)} />"
                    "</column>")
    datatype = dict(SOURCES[source][2])[name]
    measure = datatype in ("real", "integer")
    role, typ = ("measure", "quantitative") if measure else ("dimension", "ordinal" if datatype == "date" else "nominal")
    return f"<column datatype='{datatype}' name='[{name}]' role='{role}' type='{typ}' />"


def _datasource(key: str, embedded: bool) -> str:
    caption, filename, columns = SOURCES[key]
    rel_cols = "".join(f"<column datatype='{dt}' name={quoteattr(n)} ordinal='{i}' />"
                       for i, (n, dt) in enumerate(columns))
    table = filename.replace(".csv", "#csv")
    calcs = "".join(_field_column(key, c[0]) for c in CALCS.get(key, []))
    directory = "Data" if embedded else "."
    return (
        f"<datasource caption={quoteattr(caption)} inline='true' name='{_ds(key)}' version='18.1'>"
        "<connection class='federated'><named-connections>"
        f"<named-connection caption={quoteattr(caption)} name='textscan.{key}'>"
        f"<connection class='textscan' directory='{directory}' filename='{filename}' password='' server='' />"
        "</named-connection></named-connections>"
        f"<relation connection='textscan.{key}' name='{filename}' table='[{table}]' type='table'>"
        f"<columns character-set='UTF-8' header='yes' locale='en_GB' separator=','>{rel_cols}</columns>"
        "</relation></connection>"
        "<aliases enabled='yes' />"
        f"{calcs}"
        "</datasource>"
    )


def _worksheet(s: Sheet) -> str:
    used = list(dict.fromkeys(s.rows + s.cols + [x for x in (s.text, s.color) if x]
                              + (list(s.sort) if s.sort else [])))
    fields = list(dict.fromkeys(name for _, name, _ in used))
    deps = "".join(_field_column(s.source, f) for f in fields)
    deps += "".join(
        f"<column-instance column='[{name}]' derivation='{DERIVATION[prefix]}' name='[{prefix}:{name}:{suffix}]' "
        f"pivot='key' type='{SUFFIX_TYPE[suffix]}' />" for prefix, name, suffix in used)
    sort = (f"<computed-sort column='{_inst(s.source, s.sort[0])}' direction='DESC' "
            f"using='{_inst(s.source, s.sort[1])}' />") if s.sort else ""
    encodings = ""
    if s.text or s.color:
        encodings = "<encodings>"
        if s.color:
            encodings += f"<color column='{_inst(s.source, s.color)}' />"
        if s.text:
            encodings += f"<text column='{_inst(s.source, s.text)}' />"
        encodings += "</encodings>"
    caption = SOURCES[s.source][0]
    return (
        f"<worksheet name={quoteattr(s.name)}><table><view>"
        f"<datasources><datasource caption={quoteattr(caption)} name='{_ds(s.source)}' /></datasources>"
        f"<datasource-dependencies datasource='{_ds(s.source)}'>{deps}</datasource-dependencies>"
        f"{sort}<aggregation value='true' /></view><style />"
        "<panes><pane selection-relaxation-option='selection-relaxation-allow'>"
        f"<view><breakdown value='auto' /></view><mark class='{s.mark}' />{encodings}</pane></panes>"
        f"<rows>{_shelf(s.source, s.rows) if s.rows else ''}</rows>"
        f"<cols>{_shelf(s.source, s.cols) if s.cols else ''}</cols>"
        f"</table><simple-id uuid='{{{s.uid}}}' /></worksheet>"
    )


def _zone(zid: int, name: str, x: int, y: int, w: int, h: int) -> str:
    return f"<zone h='{h}' id='{zid}' name={quoteattr(name)} w='{w}' x='{x}' y='{y}' />"


def _dashboard(name: str, rows: list[tuple[int, list[str]]]) -> str:
    """rows: (height share out of 100000, sheet names laid side by side)."""
    zones, zid, y = [], 3, 0
    for height, sheets in rows:
        width = 100000 // len(sheets)
        for i, sheet in enumerate(sheets):
            zones.append(_zone(zid, sheet, i * width, y, width, height))
            zid += 1
        y += height
    return (
        f"<dashboard name={quoteattr(name)}><style />"
        "<size maxheight='800' maxwidth='1200' minheight='800' minwidth='1200' />"
        "<zones><zone h='100000' id='1' type-v2='layout-basic' w='100000' x='0' y='0'>"
        f"{''.join(zones)}</zone></zones>"
        f"<simple-id uuid='{{{str(uuid.uuid4()).upper()}}}' /></dashboard>"
    )


def sheets(include_risk: bool) -> list[Sheet]:
    spend = ("sum", "amount_gbp", "qk")
    flagged = ("sum", "Calculation_flagged", "qk")
    dept = ("none", "department", "nk")
    result = [
        Sheet("Total spend", "payments", "Text", [], [], text=spend),
        Sheet("Flagged spend", "payments", "Text", [], [], text=flagged),
        Sheet("Payments", "payments", "Text", [], [], text=("usr", "Calculation_payments", "qk")),
        Sheet("Monthly trend", "payments", "Line", [dept, spend], [("tmn", "period_month", "qk")]),
        Sheet("Flagged spend by department", "payments", "Bar", [dept], [flagged], sort=(dept, flagged)),
        Sheet("Top suppliers", "suppliers", "Bar", [dept, ("none", "supplier_name", "nk")],
              [("sum", "spend_gbp", "qk")], sort=(("none", "supplier_name", "nk"), ("sum", "spend_gbp", "qk"))),
        Sheet("Quality rules", "quality", "Bar", [("none", "rule", "nk")], [("sum", "flagged", "qk")],
              color=("none", "level", "nk"), sort=(("none", "rule", "nk"), ("sum", "flagged", "qk"))),
    ]
    if include_risk:
        result.append(Sheet("Supplier risk", "risk", "Bar", [("none", "supplier_name", "nk")],
                            [("sum", "spend_gbp", "qk")], color=("none", "status", "nk"),
                            sort=(("none", "supplier_name", "nk"), ("sum", "spend_gbp", "qk"))))
    return result


def workbook_xml(include_risk: bool, embedded: bool = True) -> str:
    all_sheets = sheets(include_risk)
    sources = ["payments", "suppliers", "quality"] + (["risk"] if include_risk else [])
    second = ["Top suppliers"] + (["Supplier risk"] if include_risk else [])
    dashboards = [
        _dashboard("Overview", [(18000, ["Total spend", "Payments", "Flagged spend"]),
                                (52000, ["Monthly trend"]), (30000, ["Flagged spend by department"])]),
        _dashboard("Suppliers", [(100000, second)]),
        _dashboard("Data quality", [(100000, ["Quality rules"])]),
    ]
    windows = "".join(f"<window class='worksheet' name={quoteattr(s.name)} />" for s in all_sheets)
    windows += "".join(f"<window class='dashboard' name={quoteattr(n)} />" for n in ("Overview", "Suppliers", "Data quality"))
    return (
        "<?xml version='1.0' encoding='utf-8' ?>\n"
        "<workbook original-version='18.1' source-build='2024.1.0 (20241.24.0312.0830)' source-platform='win' "
        "version='18.1' xmlns:user='http://www.tableausoftware.com/xml/user'>"
        f"<datasources>{''.join(_datasource(k, embedded) for k in sources)}</datasources>"
        f"<worksheets>{''.join(_worksheet(s) for s in all_sheets)}</worksheets>"
        f"<dashboards>{''.join(dashboards)}</dashboards>"
        f"<windows source-height='30'>{windows}</windows>"
        "</workbook>\n"
    )


def build_workbook(data_dir: Path, include_risk: bool) -> Path:
    """Write <data_dir>/SpendSentinel.twbx with the workbook and its CSVs inside."""
    target = data_dir / f"{WORKBOOK_NAME}.twbx"
    files = [SOURCES[k][1] for k in ("payments", "suppliers", "quality") + (("risk",) if include_risk else ())]
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{WORKBOOK_NAME}.twb", workbook_xml(include_risk))
        for name in files:
            zf.write(data_dir / name, f"Data/{name}")
    return target
