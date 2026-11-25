"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  let data = null;
  try { data = JSON.parse($("data").textContent); } catch (err) { data = null; }
  if (!data) {
    $("lede").textContent = "No data in this build. Run `spendsentinel site` after build, audit and match.";
    return;
  }

  const REPO = "https://github.com/anshgupta-456/spendsentinel";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const deptName = Object.fromEntries(data.departments.map((d) => [d.code, d.name]));

  // ---------- formatting ----------
  function money(v) {
    const a = Math.abs(v), s = v < 0 ? "-" : "";
    if (a >= 1e9) return `${s}£${(a / 1e9).toFixed(2)}bn`;
    if (a >= 1e6) return `${s}£${(a / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${s}£${(a / 1e3).toFixed(1)}k`;
    return `${s}£${a.toFixed(0)}`;
  }
  function moneyAxis(v) {
    if (v === 0) return "£0";
    if (v >= 1e9) return `£${+(v / 1e9).toFixed(1)}bn`;
    if (v >= 1e6) return `£${+(v / 1e6).toFixed(0)}M`;
    return `£${+(v / 1e3).toFixed(0)}k`;
  }
  const int = (v) => Math.round(v).toLocaleString("en-GB");
  const pct = (v, d = 0) => `${(v * 100).toFixed(d)}%`;
  function monthLabel(m, withYear) {
    const [y, mm] = m.split("-");
    return MONTHS[+mm - 1] + (withYear ? ` ${y}` : "");
  }
  function dateLabel(iso) {
    const [y, m, d] = iso.split("-");
    return `${+d} ${MONTHS[+m - 1]} ${y}`;
  }
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- DOM helpers ----------
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(child));
    }
    return node;
  }
  function svg(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  }
  function table(columns, rows) {
    const head = el("tr", {}, columns.map((c) => el("th", { class: c.num ? "num" : null, scope: "col", text: c.label })));
    const body = rows.map((r) => el("tr", {}, columns.map((c) => {
      const value = c.render ? c.render(r) : r[c.key];
      return el("td", { class: c.num ? "num" : c.nowrap ? "nowrap" : null }, value instanceof Node ? value : String(value ?? ""));
    })));
    return el("div", { class: "table-scroll" }, el("table", {}, el("thead", {}, head), el("tbody", {}, body)));
  }
  function badge(level, label) {
    return el("span", { class: `badge ${level}` }, el("span", { class: "dot", "aria-hidden": "true" }), label);
  }
  function tile(label, value, sub) {
    return el("div", { class: "tile" }, el("p", { class: "label", text: label }),
      el("p", { class: "value", text: value }), sub ? el("p", { class: "sub", text: sub }) : null);
  }
  function chips(container, options, selected, onChange, groupLabel) {
    const group = el("div", { class: "controls", role: "group", "aria-label": groupLabel });
    const buttons = options.map((o) => el("button", {
      class: "chip", type: "button", "aria-pressed": String(o.value === selected),
      onclick: () => {
        buttons.forEach((b) => b.setAttribute("aria-pressed", "false"));
        buttons[options.indexOf(o)].setAttribute("aria-pressed", "true");
        onChange(o.value);
      },
    }, o.label));
    group.append(...buttons);
    container.append(group);
  }

  // ---------- tooltip ----------
  const tip = $("tooltip");
  function showTip(html, x, y) {
    tip.innerHTML = html;
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${Math.max(8, top)}px`;
  }
  const hideTip = () => { tip.hidden = true; };

  // ---------- derived figures ----------
  const scope = data.scope;
  const tiers = Object.fromEntries(data.duplicates.tiers.map((t) => [t.tier, t]));
  const flaggedTiers = ["high", "medium", "low"].map((t) => tiers[t]).filter(Boolean);
  const dupCount = flaggedTiers.reduce((s, t) => s + t.payments, 0);
  const dupAmount = flaggedTiers.reduce((s, t) => s + t.amount, 0);
  const balanced = data.quality.reconciliation.filter((r) => r.balanced).length;
  const deptsBySpend = [...data.concentration].sort((a, b) => b.spend_gbp - a.spend_gbp).map((c) => c.dept_code);

  // ---------- header and tiles ----------
  $("headline").textContent = `Where ${money(scope.spend)} of UK government spending went, and what to check`;
  $("lede").textContent = `${int(scope.payments)} payments from ${scope.departments} departments ` +
    `(${data.departments.map((d) => d.code).join(", ")}), ${monthLabel(scope.first_month.slice(0, 7), true)} to ` +
    `${monthLabel(scope.last_month.slice(0, 7), true)}, combined from ${int(scope.lines)} published lines in ${scope.files} monthly files.`;

  const tiles = $("tiles");
  tiles.append(
    tile("Spend analysed", money(scope.spend), `${int(scope.payments)} payments`),
    tile("Possible duplicate payments", money(dupAmount), `${int(dupCount)} payments to review`),
    tile("Reconciliation", `${balanced} of ${data.quality.reconciliation.length}`, "departments balance to the penny"),
  );
  if (data.risk) {
    const cov = data.risk.coverage;
    const unreg = cov.filter((c) => ["public_body", "joint_venture"].includes(c.method)).reduce((s, c) => s + c.spend, 0);
    const matched = cov.filter((c) => !["public_body", "joint_venture", "not_found"].includes(c.method));
    const matchedSpend = matched.reduce((s, c) => s + c.spend, 0);
    const insolvency = data.risk.flags.find((f) => f.flag === "insolvency");
    tiles.append(
      tile("Matched to Companies House", pct(matchedSpend / (scope.spend - unreg)), "of the spend that could be on the register"),
      tile("Paid to suppliers now insolvent", money(insolvency.spend), `${insolvency.suppliers} suppliers in liquidation or administration`),
    );
  }
  if (data.briefing) {
    const x = data.briefing.experiment;
    tiles.append(tile("AI briefing figures verified", `${x.final_ok} of ${x.final_all}`, `after one correction, across ${x.briefings} briefings`));
  }

  // ---------- monthly trend: small multiples ----------
  const allMonths = [...new Set(data.monthly.map((r) => r.month))].sort();
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  }
  function lineChart(dept) {
    const rows = data.monthly.filter((r) => r.dept === dept);
    const byMonth = Object.fromEntries(rows.map((r) => [r.month, r]));
    // Drawn close to its on-screen size so 11px labels stay 11px.
    const W = 380, H = 170, m = { t: 10, r: 12, b: 24, l: 52 };
    const step = niceStep(Math.max(...rows.map((r) => r.spend)) / 3);
    const yMax = Math.ceil(Math.max(...rows.map((r) => r.spend)) / step) * step;
    const x = (i) => m.l + (i * (W - m.l - m.r)) / Math.max(1, allMonths.length - 1);
    const y = (v) => m.t + (H - m.t - m.b) * (1 - v / yMax);
    const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": `Monthly spend for ${deptName[dept]}, ${money(Math.min(...rows.map((r) => r.spend)))} to ${money(Math.max(...rows.map((r) => r.spend)))}` });
    for (let v = 0; v <= yMax + 1e-6; v += step) {
      chart.append(svg("line", { class: v === 0 ? "baseline" : "gridline", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
      const label = svg("text", { class: "axis-label", x: m.l - 8, y: y(v) + 4, "text-anchor": "end" });
      label.textContent = moneyAxis(v);
      chart.append(label);
    }
    const last = allMonths.length - 1;
    allMonths.forEach((mo, i) => {
      const quarterly = i % 3 === 0 && last - i >= 2;  // skip one that would crowd the final label
      if (!quarterly && i !== last) return;
      const label = svg("text", { class: "axis-label", x: x(i), y: H - 6, "text-anchor": "middle" });
      label.textContent = monthLabel(mo, mo.endsWith("-01") || i === 0);
      chart.append(label);
    });
    let d = "", pen = false;
    allMonths.forEach((mo, i) => {
      const r = byMonth[mo];
      if (!r) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(r.spend).toFixed(1)}`;
      pen = true;
    });
    chart.append(svg("path", { class: "line", d }));
    const cross = svg("line", { class: "crosshair", y1: m.t, y2: H - m.b, visibility: "hidden" });
    const dot = svg("circle", { class: "hover-dot", r: 4.5, visibility: "hidden" });
    const hit = svg("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" });
    chart.append(cross, dot, hit);
    hit.addEventListener("pointermove", (ev) => {
      const pt = chart.createSVGPoint();
      pt.x = ev.clientX; pt.y = ev.clientY;
      const local = pt.matrixTransform(chart.getScreenCTM().inverse());
      let best = -1, bestDist = Infinity;
      allMonths.forEach((mo, i) => {
        if (!byMonth[mo]) return;
        const dist = Math.abs(x(i) - local.x);
        if (dist < bestDist) { best = i; bestDist = dist; }
      });
      if (best < 0) return;
      const r = byMonth[allMonths[best]];
      cross.setAttribute("x1", x(best)); cross.setAttribute("x2", x(best)); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x(best)); dot.setAttribute("cy", y(r.spend)); dot.setAttribute("visibility", "visible");
      showTip(`<strong>${esc(deptName[dept])} · ${monthLabel(r.month, true)}</strong>${money(r.spend)} · ${int(r.payments)} payments`, ev.clientX, ev.clientY);
    });
    hit.addEventListener("pointerleave", () => {
      cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); hideTip();
    });
    const latest = rows[rows.length - 1];
    return el("div", { class: "panel" }, el("h3", { text: deptName[dept] }),
      el("p", { class: "latest", text: `Latest: ${monthLabel(latest.month, true)}, ${money(latest.spend)}` }), chart);
  }
  $("trend-charts").append(...deptsBySpend.map(lineChart));
  $("trend-table").append(table(
    [{ label: "Month", render: (r) => monthLabel(r, true) },
      ...deptsBySpend.map((dpt) => ({ label: dpt, num: true, render: (mo) => {
        const r = data.monthly.find((x) => x.dept === dpt && x.month === mo);
        return r ? money(r.spend) : "";
      } }))],
    allMonths));

  // ---------- duplicates ----------
  const bulk = tiers.bulk, noref = tiers.no_reference;
  $("dup-note").textContent = `${int(dupCount)} payments worth ${money(dupAmount)} match an earlier payment from the same ` +
    "department to the same supplier, for the same amount to the penny, within 7 days, under a different reference. " +
    `Set aside and not counted: ${bulk ? `${int(bulk.payments)} bulk-order payments (${money(bulk.amount)}), such as vehicle fleets` : "no bulk orders"}` +
    `${noref ? `, and ${int(noref.payments)} payments with no reference number (${money(noref.amount)})` : ""}.`;
  const tierBadge = { high: ["serious", "High"], medium: ["warning", "Medium"], low: ["neutral", "Low"] };
  const dupState = { tier: "all", dept: "all", limit: 25 };
  function renderDuplicates() {
    const rows = data.duplicates.pairs.filter((p) =>
      (dupState.tier === "all" || p.tier === dupState.tier) && (dupState.dept === "all" || p.dept === dupState.dept));
    const shown = rows.slice(0, dupState.limit);
    const box = $("dup-table");
    box.replaceChildren(table([
      { label: "Supplier", key: "supplier" },
      { label: "Dept", key: "dept" },
      { label: "Amount", num: true, render: (r) => money(r.amount) },
      { label: "First paid", nowrap: true, render: (r) => dateLabel(r.first_date) },
      { label: "Second paid", nowrap: true, render: (r) => dateLabel(r.second_date) },
      { label: "References", render: (r) => `${r.first_ref} / ${r.second_ref}` },
      { label: "Tier", render: (r) => badge(...tierBadge[r.tier]) },
    ], shown));
    if (rows.length > shown.length) {
      box.append(el("button", { class: "chip more", type: "button",
        onclick: () => { dupState.limit = rows.length; renderDuplicates(); } }, `Show all ${rows.length}`));
    } else if (rows.length === 0) {
      box.append(el("p", { class: "note", text: "No pairs for this filter." }));
    }
  }
  const dupControls = $("dup-controls");
  chips(dupControls, [{ value: "all", label: "All tiers" }, { value: "high", label: "High" },
    { value: "medium", label: "Medium" }, { value: "low", label: "Low" }], "all",
  (v) => { dupState.tier = v; dupState.limit = 25; renderDuplicates(); }, "Confidence tier");
  chips(dupControls, [{ value: "all", label: "All departments" }, ...deptsBySpend.map((d) => ({ value: d, label: d }))], "all",
    (v) => { dupState.dept = v; dupState.limit = 25; renderDuplicates(); }, "Department");
  renderDuplicates();

  // ---------- suppliers ----------
  function renderSuppliers(dept) {
    const rows = data.top_suppliers.filter((s) => s.dept === dept);
    const max = Math.max(...rows.map((r) => r.spend));
    $("supplier-bars").replaceChildren(...rows.map((r) => {
      const row = el("div", { class: "bar-row" },
        el("span", { class: "bar-name", title: r.name, text: r.name }),
        el("div", { class: "bar-track" }, el("div", { class: "bar-fill", style: `width:${(100 * r.spend / max).toFixed(2)}%` })),
        el("span", { class: "bar-value", text: money(r.spend) }));
      row.addEventListener("pointermove", (ev) => showTip(
        `<strong>${esc(r.name)}</strong>${money(r.spend)} · ${pct(r.share, 1)} of ${esc(dept)} spend · ${int(r.payments)} payments`,
        ev.clientX, ev.clientY));
      row.addEventListener("pointerleave", hideTip);
      return row;
    }));
  }
  chips($("supplier-controls"), deptsBySpend.map((d) => ({ value: d, label: deptName[d] })), deptsBySpend[0],
    renderSuppliers, "Department");
  renderSuppliers(deptsBySpend[0]);
  $("concentration-table").append(table([
    { label: "Department", render: (r) => deptName[r.dept_code] },
    { label: "Suppliers", num: true, render: (r) => int(r.suppliers) },
    { label: "Spend", num: true, render: (r) => money(r.spend_gbp) },
    { label: "Top 10 share", num: true, render: (r) => pct(r.top10_share) },
    { label: "Suppliers for 80%", num: true, render: (r) => int(r.suppliers_for_80) },
    { label: "HHI", num: true, render: (r) => int(r.hhi) },
    { label: "Largest supplier", render: (r) => `${r.largest_supplier} (${pct(r.largest_supplier_share)})` },
  ], data.concentration));

  // ---------- supplier risk ----------
  if (!data.risk) {
    $("risk-note").textContent = "Supplier matching has not been run for this build.";
  } else {
    const cov = data.risk.coverage;
    const total = cov.reduce((s, c) => s + c.suppliers, 0);
    const unreg = cov.filter((c) => ["public_body", "joint_venture"].includes(c.method)).reduce((s, c) => s + c.spend, 0);
    const matched = cov.filter((c) => !["public_body", "joint_venture", "not_found"].includes(c.method));
    $("risk-note").textContent = `${int(matched.reduce((s, c) => s + c.suppliers, 0))} of ${int(total)} suppliers are matched ` +
      `to the Companies House register, covering ${pct(matched.reduce((s, c) => s + c.spend, 0) / (scope.spend - unreg))} ` +
      "of the spend that could be on it. Councils, government bodies and unincorporated joint ventures are not registered " +
      "companies, so they are left out of that figure. Status and filing dates are from the latest monthly snapshot, so they " +
      "describe each supplier today.";
    $("risk-tiles").append(...data.risk.flags.map((f) => tile(f.label, money(f.spend), `${f.suppliers} suppliers`)));
    const statusLevel = (r) => r.insolvency ? ["critical", r.status] : r.strike_off_proposed ? ["serious", r.status] : ["warning", r.status];
    const flagText = (r) => [r.insolvency && "insolvency", r.strike_off_proposed && "strike-off proposed",
      r.accounts_overdue && "accounts overdue", r.confirmation_overdue && "confirmation statement overdue"].filter(Boolean).join(", ");
    $("risk-table").append(table([
      { label: "Supplier", render: (r) => el("a", { href: `https://find-and-update.company-information.service.gov.uk/company/${r.company_number}` }, r.name) },
      { label: "Status", render: (r) => badge(...statusLevel(r)) },
      { label: "Flags", render: flagText },
      { label: "Paid by", key: "departments" },
      { label: "Spend", num: true, render: (r) => money(r.spend) },
    ], data.risk.suppliers));
  }

  // ---------- data quality ----------
  $("recon-table").append(table([
    { label: "Department", render: (r) => deptName[r.dept_code] || r.dept_code },
    { label: "Raw lines", num: true, render: (r) => int(r.raw_lines) },
    { label: "Dropped", num: true, render: (r) => int(r.dropped_lines) },
    { label: "Payments", num: true, render: (r) => int(r.payments) },
    { label: "Raw total", num: true, render: (r) => money(r.raw_pence / 100) },
    { label: "Dropped total", num: true, render: (r) => money(r.dropped_pence / 100) },
    { label: "Balances", render: (r) => r.balanced ? el("span", { class: "ok", text: "Yes, to the penny" }) : badge("critical", "No") },
  ], data.quality.reconciliation));
  $("rules-table").append(table([
    { label: "Rule", render: (r) => r.checks || r.rule },
    { label: "Applies to", key: "level" },
    { label: "Flagged", num: true, render: (r) => int(r.flagged) },
    { label: "£ affected", num: true, render: (r) => (r.level === "file" ? "" : money(r.amount)) },
    { label: "What happens", key: "action" },
  ], data.quality.rules));

  // ---------- briefing ----------
  const b = data.briefing;
  const card = $("briefing-card");
  if (!b) {
    card.append(el("p", { class: "note", text: "No briefing has been generated yet." }));
  } else {
    const claim = b.largest_change && b.largest_change.correct;
    const x = b.experiment;
    card.append(el("div", { class: "briefing" },
      el("blockquote", { text: b.text }),
      el("p", { class: "meta" },
        `Written by ${b.model} (running locally, temperature 0) for ${monthLabel(b.month, true)}, from figures computed in SQL. `,
        el("strong", { text: `${b.verified} of ${b.figures} figures verified` }),
        claim === true ? "; the department named as the largest change is the one SQL found." :
          claim === false ? "; the largest-change claim is wrong and flagged." : "."),
      el("p", { class: "meta" },
        `Across ${x.briefings} briefings from two models, first drafts had ${pct(x.first_ok / x.first_all)} of figures right, ` +
        `and ${x.final_ok} of ${x.final_all} after one correction. Every invented figure was flagged before anyone read it. `,
        el("a", { href: `${REPO}/blob/main/reports/briefings/README.md`, text: "See the experiment" }), ".")));
  }

  const generated = $("generated");
  generated.textContent = `Built ${data.generated_at} UTC by spendsentinel site. `;
  if (data.downloads && data.downloads.length) {
    generated.append("Download the data as CSV: ",
      ...data.downloads.flatMap((name, i) => [i ? ", " : "", el("a", { href: `data/${name}`, text: name })]), ".");
  }
})();
