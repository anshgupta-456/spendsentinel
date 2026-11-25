"""Generate a packaged Tableau workbook (.twbx) with the sheets and dashboards pre-built.

A .twbx is a zip holding the workbook XML and its data files, so it opens in
Tableau Public with no connection setup. The sheets mirror the web dashboard:
headline figures, monthly trend, flagged spend, top suppliers, supplier risk
and data quality.
"""

from __future__ import annotations

import csv
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
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
        ("Calculation_flagged", "Flagged amount", "real", "measure", "quantitative",
         'IF [possible_duplicate] = "True" THEN [amount_gbp] END'),
        ("Calculation_payments", "Payments", "integer", "measure", "quantitative", "COUNTD([payment_id])"),
        # Tableau ignores Excel's comma scaling in formats, so scale in the calculation instead.
        ("Calculation_spend_bn", "Total spend (£bn)", "real", "measure", "quantitative",
         "SUM([amount_gbp]) / 1000000000"),
        ("Calculation_spend_m", "Spend (£M)", "real", "measure", "quantitative", "SUM([amount_gbp]) / 1000000"),
        ("Calculation_flagged_m", "Flagged spend (£M)", "real", "measure", "quantitative",
         "SUM([Calculation_flagged]) / 1000000"),
    ],
    "suppliers": [
        ("Calculation_supplier_m", "Supplier spend (£M)", "real", "measure", "quantitative",
         "SUM([spend_gbp]) / 1000000"),
    ],
    "risk": [
        ("Calculation_risk_m", "Spend with supplier (£M)", "real", "measure", "quantitative",
         "SUM([spend_gbp]) / 1000000"),
    ],
}

# Number formats: Tableau stores a custom format as "*" followed by the pattern.
FORMATS = {
    ("payments", "Calculation_payments"): "*#,##0",
    ("payments", "Calculation_spend_bn"): '*"£"#,##0.00"bn"',
    ("payments", "Calculation_spend_m"): '*"£"#,##0"M"',
    ("payments", "Calculation_flagged_m"): '*"£"#,##0.0"M"',
    ("suppliers", "Calculation_supplier_m"): '*"£"#,##0.0"M"',
    ("risk", "Calculation_risk_m"): '*"£"#,##0.00"M"',
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
    sort: tuple[tuple[str, str, str], tuple[str, str, str]] | None = None  # sort this dimension by that measure
    independent_axis: tuple[str, str, str] | None = None                    # give each row its own scale


def _inst(source: str, inst: tuple[str, str, str]) -> str:
    prefix, name, suffix = inst
    return f"[{_ds(source)}].[{prefix}:{name}:{suffix}]"


def _shelf(source: str, items: list[tuple[str, str, str]]) -> str:
    refs = [_inst(source, i) for i in items]
    return refs[0] if len(refs) == 1 else "(" + " / ".join(refs) + ")"


def _field_column(source: str, name: str) -> str:
    """The <column> definition of a field (relation column or calculated field)."""
    fmt = FORMATS.get((source, name))
    fmt_attr = f" default-format={quoteattr(fmt)}" if fmt else ""
    for calc in CALCS.get(source, []):
        if calc[0] == name:
            internal, caption, datatype, role, typ, formula = calc
            return (f"<column caption={quoteattr(caption)} datatype='{datatype}'{fmt_attr} name='[{internal}]' "
                    f"role='{role}' type='{typ}'><calculation class='tableau' formula={quoteattr(formula)} />"
                    "</column>")
    datatype = dict(SOURCES[source][2])[name]
    measure = datatype in ("real", "integer")
    role, typ = ("measure", "quantitative") if measure else ("dimension", "ordinal" if datatype == "date" else "nominal")
    return f"<column datatype='{datatype}'{fmt_attr} name='[{name}]' role='{role}' type='{typ}' />"


def _extract(key: str) -> str:
    """Tableau Public only accepts extracts, so each source points at a .hyper file in the package."""
    stamp = datetime.now().strftime("%m/%d/%Y %I:%M:%S %p")
    return (
        "<extract count='-1' enabled='true' units='records'>"
        f"<connection class='hyper' dbname='Data/Extracts/{key}.hyper' default-settings='hyper' schema='Extract' "
        f"sslmode='' tablename='Extract' update-time='{stamp}' username='tableau_internal_user'>"
        "<relation name='Extract' table='[Extract].[Extract]' type='table' />"
        "</connection></extract>"
    )


def _datasource(key: str, embedded: bool, columns: list[tuple[str, str]] | None = None) -> str:
    caption, filename, default_columns = SOURCES[key]
    columns = columns or default_columns
    rel_cols = "".join(f"<column datatype='{dt}' name={quoteattr(n)} ordinal='{i}' />"
                       for i, (n, dt) in enumerate(columns))
    table = filename.replace(".csv", "#csv")
    calc_names = {c[0] for c in CALCS.get(key, [])}
    formatted = "".join(_field_column(key, name) for (src, name) in FORMATS if src == key and name not in calc_names)
    calcs = formatted + "".join(_field_column(key, c[0]) for c in CALCS.get(key, []))
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
        f"{_extract(key) if embedded else ''}"
        "</datasource>"
    )


def _worksheet(s: Sheet) -> str:
    # Only elements in the 18.1 workbook schema: no simple-id or computed-sort (Tableau rejects them).
    used = list(dict.fromkeys(s.rows + s.cols + [x for x in (s.text, s.color) if x]
                              + (list(s.sort) if s.sort else [])))
    fields = list(dict.fromkeys(name for _, name, _ in used))
    # A calculation's inputs must be declared too, including other calculations it uses.
    formulas = {c[0]: c[5] for c in CALCS.get(s.source, [])}
    pending, ordered = list(fields), []
    while pending:
        name = pending.pop(0)
        if name in ordered:
            continue
        refs = re.findall(r"\[([^\]]+)\]", formulas.get(name, ""))
        unresolved = [r for r in refs if r not in ordered]
        if unresolved:
            pending = unresolved + [name] + pending
            continue
        ordered.append(name)
    fields = ordered
    deps = "".join(_field_column(s.source, f) for f in fields)
    deps += "".join(
        f"<column-instance column='[{name}]' derivation='{DERIVATION[prefix]}' name='[{prefix}:{name}:{suffix}]' "
        f"pivot='key' type='{SUFFIX_TYPE[suffix]}' />" for prefix, name, suffix in used)
    encodings = ""
    if s.text or s.color:
        encodings = "<encodings>"
        if s.color:
            encodings += f"<color column='{_inst(s.source, s.color)}' />"
        if s.text:
            encodings += f"<text column='{_inst(s.source, s.text)}' />"
        encodings += "</encodings>"
    caption = SOURCES[s.source][0]
    sort = (f"<sort class='computed' column='{_inst(s.source, s.sort[0])}' direction='DESC' "
            f"using='{_inst(s.source, s.sort[1])}' />") if s.sort else ""
    style = "<style />"
    if s.independent_axis:
        style = ("<style><style-rule element='axis'>"
                 f"<encoding attr='space' class='0' field='{_inst(s.source, s.independent_axis)}' "
                 "field-type='quantitative' range-type='independent' type='space' />"
                 "</style-rule></style>")
    return (
        f"<worksheet name={quoteattr(s.name)}><table><view>"
        f"<datasources><datasource caption={quoteattr(caption)} name='{_ds(s.source)}' /></datasources>"
        f"<datasource-dependencies datasource='{_ds(s.source)}'>{deps}</datasource-dependencies>"
        f"{sort}<aggregation value='true' /></view>{style}"
        "<panes><pane>"
        f"<view><breakdown value='auto' /></view><mark class='{s.mark}' />{encodings}</pane></panes>"
        f"<rows>{_shelf(s.source, s.rows) if s.rows else ''}</rows>"
        f"<cols>{_shelf(s.source, s.cols) if s.cols else ''}</cols>"
        "</table></worksheet>"
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
        "<zones><zone h='100000' id='1' type='layout-basic' w='100000' x='0' y='0'>"
        f"{''.join(zones)}</zone></zones>"
        "</dashboard>"
    )


def sheets(include_risk: bool) -> list[Sheet]:
    spend = ("usr", "Calculation_spend_m", "qk")
    flagged = ("usr", "Calculation_flagged_m", "qk")
    code = ("none", "dept_code", "nk")
    supplier = ("none", "supplier_name", "nk")
    supplier_spend = ("usr", "Calculation_supplier_m", "qk")
    rule, flagged_count = ("none", "rule", "nk"), ("sum", "flagged", "qk")
    result = [
        Sheet("Total spend", "payments", "Text", [], [], text=("usr", "Calculation_spend_bn", "qk")),
        Sheet("Flagged spend", "payments", "Text", [], [], text=flagged),
        Sheet("Payments", "payments", "Text", [], [], text=("usr", "Calculation_payments", "qk")),
        Sheet("Monthly trend", "payments", "Line", [code, spend], [("tmn", "period_month", "qk")],
              independent_axis=spend),
        Sheet("Flagged spend by department", "payments", "Bar", [code], [flagged], sort=(code, flagged)),
        Sheet("Top suppliers", "suppliers", "Bar", [("none", "department", "nk"), supplier], [supplier_spend],
              sort=(supplier, supplier_spend)),
        Sheet("Quality rules", "quality", "Bar", [rule], [flagged_count], color=("none", "level", "nk"),
              sort=(rule, flagged_count)),
    ]
    if include_risk:
        risk_spend = ("usr", "Calculation_risk_m", "qk")
        result.append(Sheet("Supplier risk", "risk", "Bar", [supplier], [risk_spend],
                            color=("none", "status", "nk"), sort=(supplier, risk_spend)))
    return result


def workbook_xml(include_risk: bool, embedded: bool = True,
                 columns: dict[str, list[tuple[str, str]]] | None = None) -> str:
    all_sheets = sheets(include_risk)
    sources = ["payments", "suppliers", "quality"] + (["risk"] if include_risk else [])
    columns = columns or {}
    second = ["Top suppliers"] + (["Supplier risk"] if include_risk else [])
    dashboards = [
        _dashboard("Overview", [(18000, ["Total spend", "Payments", "Flagged spend"]),
                                (52000, ["Monthly trend"]), (30000, ["Flagged spend by department"])]),
        _dashboard("Suppliers", [(100000, second)]),
        _dashboard("Data quality", [(100000, ["Quality rules"])]),
    ]
    # Worksheet windows are left out (Tableau creates them). Dashboard windows are written so the
    # headline tiles fill their space; otherwise their titles wrap into a narrow column.
    fit = ("Total spend", "Payments", "Flagged spend", "Monthly trend")
    viewpoints = "".join(f"<viewpoint name={quoteattr(n)}><zoom type='entire-view' /></viewpoint>" for n in fit)
    windows = (f"<window class='dashboard' maximized='true' name='Overview'><viewpoints>{viewpoints}</viewpoints>"
               "<active id='-1' /></window>")
    return (
        "<?xml version='1.0' encoding='utf-8' ?>\n"
        "<workbook original-version='18.1' source-build='2024.1.0 (20241.24.0312.0830)' source-platform='win' "
        "version='18.1' xmlns:user='http://www.tableausoftware.com/xml/user'>"
        f"<datasources>{''.join(_datasource(k, embedded, columns.get(k)) for k in sources)}</datasources>"
        f"<worksheets>{''.join(_worksheet(s) for s in all_sheets)}</worksheets>"
        f"<dashboards>{''.join(dashboards)}</dashboards>"
        f"<windows source-height='30'>{windows}</windows>"
        "</workbook>\n"
    )


def _columns_from_csv(key: str, csv_path: Path) -> list[tuple[str, str]]:
    """The CSV's own header, typed from SOURCES, so the workbook always matches the file."""
    with csv_path.open(encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    types = dict(SOURCES[key][2])
    return [(name, types.get(name, "string")) for name in header]


def _write_hyper(csv_path: Path, hyper_path: Path, columns: list[tuple[str, str]]) -> None:
    from tableauhyperapi import (
        Connection,
        CreateMode,
        HyperProcess,
        SqlType,
        TableDefinition,
        TableName,
        Telemetry,
        escape_string_literal,
    )

    sql_types = {"string": SqlType.text(), "real": SqlType.double(), "integer": SqlType.big_int(),
                 "date": SqlType.date()}
    table = TableDefinition(TableName("Extract", "Extract"),
                            [TableDefinition.Column(name, sql_types[dt]) for name, dt in columns])
    with tempfile.TemporaryDirectory() as logs, \
            HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters={"log_dir": logs}) as hyper, \
            Connection(hyper.endpoint, str(hyper_path), CreateMode.CREATE_AND_REPLACE) as conn:
        conn.catalog.create_schema("Extract")
        conn.catalog.create_table(table)
        conn.execute_command(f"COPY {table.table_name} FROM {escape_string_literal(str(csv_path))} "
                             "WITH (format csv, header, NULL '')")


def build_workbook(data_dir: Path, include_risk: bool) -> Path:
    """Write <data_dir>/SpendSentinel.twbx: the workbook, its CSVs and a .hyper extract per source."""
    target = data_dir / f"{WORKBOOK_NAME}.twbx"
    keys = ["payments", "suppliers", "quality"] + (["risk"] if include_risk else [])
    columns = {k: _columns_from_csv(k, data_dir / SOURCES[k][1]) for k in keys}
    with tempfile.TemporaryDirectory() as tmp, \
            zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{WORKBOOK_NAME}.twb", workbook_xml(include_risk, columns=columns))
        for key in keys:
            csv_path = data_dir / SOURCES[key][1]
            hyper_path = Path(tmp) / f"{key}.hyper"
            _write_hyper(csv_path, hyper_path, columns[key])
            zf.write(csv_path, f"Data/{csv_path.name}")
            zf.write(hyper_path, f"Data/Extracts/{key}.hyper")
    return target
