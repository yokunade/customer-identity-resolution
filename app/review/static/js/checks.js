// Engine checks (managers): signs of wrong links inside customers and missed links across them.

import { api, fmtDob, fmtNum, h, loading, plural, show } from "./core.js";
import { setNav } from "./shell.js";

const pct = (n, d) => (n && (100 * n) / d < 1 ? "<1%" : `${Math.round((100 * n) / Math.max(1, d))}%`);

function tile(label, value, sub) {
  return h("div", { class: "tile" },
    h("dt", { text: label }), h("dd", { class: "tile-value", text: value }),
    sub ? h("dd", { class: "tile-sub", text: sub }) : null);
}

function summaryTiles(s) {
  const linked = s.links.exact + s.links.strong + s.links.probable + s.links.possible;
  const merged = s.merges.automatic + s.merges.reviewer + s.merges.api;
  return h("dl", { class: "tiles" },
    tile("Source records", fmtNum(s.records), `${(s.records / Math.max(1, s.customers)).toFixed(2)} per customer`),
    tile("Customers", fmtNum(s.customers)),
    tile("Records linked on arrival", fmtNum(linked),
      `${fmtNum(s.links.exact)} exact · ${fmtNum(s.links.strong)} strong · ${fmtNum(s.links.probable)} probable · ${fmtNum(s.links.possible)} possible`),
    tile("Customers merged", fmtNum(merged),
      `${fmtNum(s.merges.automatic)} automatically · ${fmtNum(s.merges.reviewer)} by reviewers${s.merges.api ? ` · ${fmtNum(s.merges.api)} through the API` : ""}`),
    tile("Open review cases", fmtNum(s.open_cases), `${fmtNum(s.kept_apart)} pairs kept apart by reviewers`),
    tile("Moved out by hand", fmtNum(s.merges.moved_out), "Wrong links undone"));
}

function status(c) {
  if (!c.found) return h("span", { class: "tag tag-ok", text: "Nothing found" });
  if (!c.to_look_at) return h("span", { class: "tag tag-ok", text: "All explained" });
  return h("span", { class: "tag tag-warn", text: `${fmtNum(c.to_look_at)} to look at` });
}

const shownValue = (v) => (/^\d{4}-\d{2}-\d{2}$/.test(v) ? fmtDob(v) : v);

function examplesTable(c) {
  return h("div", { class: "table-scroll" }, h("table", { class: "check-table" },
    h("thead", {}, h("tr", {}, ["Found", "Explanation", c.group === "inside" ? "Customer" : "Customers"].map((t) =>
      h("th", { scope: "col", text: t })))),
    h("tbody", {}, c.examples.map((e) => h("tr", {},
      h("td", { class: "values" }, e.values.map((v) => h("div", { text: shownValue(v) }))),
      h("td", {}, h("span", { class: `tag ${e.expected ? "tag-muted" : "tag-warn"}`, text: e.label })),
      h("td", {}, h("div", { class: "people" },
        e.customers.map((p) => h("a", { href: `#/customer/${p.customer_id}`, target: "_blank", rel: "noopener" },
          p.name || "Unnamed", h("span", { class: "muted small", text: ` ${plural(p.record_count || 0, "record")}` }))),
        e.more_customers ? h("span", { class: "muted small", text: `and ${e.more_customers} more` }) : null)))))));
}

function checkPanel(c) {
  const id = `chk-${c.key}`;
  const list = h("div", { class: "check-examples", hidden: true });
  const toggle = c.examples.length
    ? h("button", { type: "button", class: "btn btn-sm", "aria-expanded": "false", "aria-controls": `${id}-ex`,
      text: "Show examples", onclick: () => {
        const open = list.hidden;
        if (open && !list.firstChild) list.append(examplesTable(c),
          c.found > c.examples.length ? h("p", { class: "hint", text: `The first ${c.examples.length} of ${fmtNum(c.found)}, those to look at first.` }) : null);
        list.hidden = !open;
        toggle.textContent = open ? "Hide examples" : "Show examples";
        toggle.setAttribute("aria-expanded", String(open));
      } })
    : null;
  list.id = `${id}-ex`;
  return h("section", { class: "panel check", "aria-labelledby": id },
    h("div", { class: "check-head" },
      h("div", {}, h("h3", { id, text: c.title }), h("p", { class: "hint", text: c.detects })),
      h("div", { class: "check-count" }, h("b", { text: fmtNum(c.found) }), status(c))),
    c.classes.length
      ? h("ul", { class: "check-classes", "aria-label": "What the check found" }, c.classes.map((k) =>
        h("li", { class: k.expected ? "" : "look" },
          h("span", { text: k.label }), h("b", { text: `${fmtNum(k.count)} · ${pct(k.count, c.found)}` }))))
      : null,
    toggle ? h("div", { class: "check-foot" }, toggle) : null,
    list);
}

export async function viewChecks() {
  setNav("checks");
  show(loading("Running the checks"));
  const res = await api("/checks");
  const group = (g) => res.checks.filter((c) => c.group === g).map(checkPanel);
  show(
    h("div", { class: "page-head" }, h("div", {},
      h("h1", { text: "Engine checks" }),
      h("p", { class: "sub", text: "Looks for signs of a wrong decision: records of different people inside one customer, and one person spread over several. Findings the matching rules account for count as explained; the rest are what to look at, and are listed first." }))),
    summaryTiles(res.summary),
    h("section", { class: "check-group", "aria-labelledby": "grp-inside" },
      h("h2", { id: "grp-inside", text: "Inside one customer" }),
      h("p", { class: "hint", text: "Signs that records of different people were linked together." }),
      ...group("inside")),
    h("section", { class: "check-group", "aria-labelledby": "grp-across" },
      h("h2", { id: "grp-across", text: "Across customers" }),
      h("p", { class: "hint", text: "Signs that one person was left in more than one customer, or that people share details." }),
      ...group("across")));
}
