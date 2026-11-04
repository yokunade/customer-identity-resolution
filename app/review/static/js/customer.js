// Customer 360 (managers): everything held about one customer.

import { ICONS, api, fmtDate, fmtDateTime, fmtDob, h, icon, loading, plural, shortId, show, state, toast } from "./core.js";
import { setNav } from "./shell.js";

const tag = (text, cls = "") => h("span", { class: "tag " + cls, text });
const sources = (list) => h("span", { class: "sources", text: list.join(", ") });

function outcomeText(e) {
  const rec = e.record_id ? `record ${e.record_id}` : "";
  switch (e.outcome) {
    case "new_customer": return `Customer created from ${rec}`;
    case "matched": return `${rec[0].toUpperCase() + rec.slice(1)} linked by ${e.rule}`;
    case "existing_source_record": return `${rec[0].toUpperCase() + rec.slice(1)} re-sent by its source; link kept`;
    case "relinked": return `${rec[0].toUpperCase() + rec.slice(1)} re-sent and moved here${e.relinked_from ? ` from ${shortId(e.relinked_from)}` : ""} by ${e.rule}`;
    case "merge": return `Customer ${shortId(e.merged_customer_id)} merged in${e.reason ? `: ${e.reason}` : ""}`;
    case "split": {
      const by = e.done_by ? ` by ${e.done_by}` : "";
      if (e.record_id) return `${rec[0].toUpperCase() + rec.slice(1)} moved here from ${shortId(e.split_from)}${by}: ${e.reason}`;
      return `${plural(e.records_moved, "record")} moved out to ${e.restored ? "restored" : "new"} customer ${shortId(e.moved_to)}${by}: ${e.reason}`;
    }
    default: return e.outcome.replace(/_/g, " ");
  }
}

function linkedBy(l) {
  if (!l) return h("span", { class: "muted", text: "Unknown" });
  if (l.outcome === "new_customer") return h("span", {}, "First record ", h("span", { class: "muted small", text: "created the customer" }));
  if (l.outcome === "split") return h("span", {}, `Moved here by ${l.by || "hand"} `,
    h("span", { class: "muted small", text: `from ${shortId(l.split_from)}` }));
  if (l.outcome === "merge" || !l.rule) return h("span", { text: l.outcome.replace(/_/g, " ") });
  return h("span", { class: "linked" }, l.tier ? tag(l.tier, "tag-tier tag-" + l.tier) : null, h("span", { class: "mono small", text: l.rule }));
}

export function cleansedLines(c) {
  const n = c.name || {};
  const a = c.address || {};
  return [
    ["Name", [n.title, n.first_name, n.middle_names, n.last_name, n.suffix].filter(Boolean).join(" ")],
    ["Born", c.date_of_birth ? `${fmtDob(c.date_of_birth)}${c.date_of_birth_usable ? "" : " (not used: placeholder)"}` : ""],
    ["Gender", c.gender || ""],
    ...(c.emails || []).map((e) => ["Email", `${e.canonical}${e.usable_for_matching ? "" : " (not used for matching)"}`]),
    ...(c.phones || []).map((p) => ["Phone", `${p.national || p.e164} · ${p.type}${p.usable_for_matching ? "" : " (not used for matching)"}`]),
    ["Address", (a.lines || []).join(", ")],
    ...(c.previous_addresses || []).map((x) => ["Previous", (x.lines || []).join(", ")]),
  ].filter(([, v]) => v);
}

function recordDetail(r) {
  const a = r.added_by;
  return h("div", { class: "rec-detail" },
    a ? h("p", { class: "added-by" }, h("b", { text: `Added in custIDR by ${a.name}` }), `, ${fmtDateTime(a.at)}`,
      a.note ? h("span", { class: "note", text: `: ${a.note}` }) : ".") : null,
    h("div", { class: "rec-cols" },
      h("div", { class: "rec-box" }, h("h4", { text: "As received" }),
        h("pre", { class: "mono", text: JSON.stringify(r.raw, null, 2) })),
      h("div", { class: "rec-box" }, h("h4", { text: "Cleansed" }),
        h("dl", {}, cleansedLines(r.cleansed).map(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })])))),
    r.data_quality.length
      ? h("div", { class: "tags dq" }, r.data_quality.map((i) => tag(`${i.field}: ${i.code.replace(/_/g, " ")}`, "tag-dq tag-dq-" + i.severity)))
      : h("p", { class: "hint", text: "No data quality notes." }));
}

function splitPanel(v, picked, boxes, onDone, onCancel) {
  const reason = h("textarea", { id: "split-reason", rows: 2, maxlength: 1000,
    placeholder: "For example: billing record is his father, same name and address" });
  const count = h("span", { class: "split-count" });
  const err = h("p", { class: "error", role: "alert", hidden: true });
  const go = h("button", { type: "button", class: "btn btn-primary", onclick: async () => {
    go.disabled = true;
    err.hidden = true;
    try {
      const r = await api(`/customers/${v.customer_id}/split`, { method: "POST",
        body: { record_ids: [...picked], reason: reason.value } });
      toast(`${plural(r.source_records_moved, "record")} moved to ${r.restored ? "restored" : "new"} customer ${shortId(r.moved_to_customer_id)}.`);
      onDone(r);
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
      go.disabled = false;
    }
  } });
  function refresh() {
    const n = picked.size;
    const all = n === v.records.length;
    count.textContent = all ? "At least one record must stay with this customer."
      : n ? `${plural(n, "record")} selected` : "Select the records that belong to someone else.";
    go.textContent = n > 1 ? `Move ${n} records out` : "Move record out";
    go.disabled = !n || all || reason.value.trim().length < 3;
  }
  reason.addEventListener("input", refresh);
  boxes.forEach((b) => b.addEventListener("change", refresh));
  refresh();
  const panel = h("div", { class: "split-panel" },
    h("p", {}, h("b", { text: "Move records to a separate customer. " }),
      "Use this when records were linked to the wrong person. If they came from a customer merged in earlier, that customer is brought back; otherwise a new one is created. They will not be linked here again."),
    count,
    h("div", { class: "field" }, h("label", { for: "split-reason", text: "Why don't they belong here?" }), reason),
    err,
    h("div", { class: "actions" }, go, h("button", { type: "button", class: "btn", text: "Cancel", onclick: onCancel })));
  return { panel, reset: () => { reason.value = ""; err.hidden = true; refresh(); boxes[0]?.focus(); } };
}

function recordsTable(v, onSplit) {
  const tbody = h("tbody");
  const picked = new Set();
  const boxes = [];
  const pickCells = [];
  for (const r of v.records) {
    const box = h("input", { type: "checkbox", "aria-label": `Move ${r.source_system} ${r.source_record_id} out`,
      onchange: () => { box.checked ? picked.add(r.record_id) : picked.delete(r.record_id); } });
    boxes.push(box);
    const pickCell = h("td", { class: "pick-cell", hidden: true }, box);
    pickCells.push(pickCell);
    const detail = h("tr", { class: "detail-row", hidden: true }, h("td", { colspan: 6 }, recordDetail(r)));
    const btn = h("button", { type: "button", class: "btn btn-sm", "aria-expanded": "false",
      "aria-label": `Show ${r.source_system} ${r.source_record_id}`, text: "Show",
      onclick: () => {
        const open = detail.hidden;
        detail.hidden = !open;
        btn.textContent = open ? "Hide" : "Show";
        btn.setAttribute("aria-expanded", String(open));
      } });
    tbody.append(
      h("tr", {}, pickCell,
        h("td", {}, h("div", { class: "strong", text: r.source_system }),
          r.added_by ? h("div", { class: "muted small", text: `Added by ${r.added_by.name}` }) : null),
        h("td", { class: "mono small", text: r.source_record_id }),
        h("td", {}, linkedBy(r.linked_by)),
        h("td", { class: "muted nowrap", text: fmtDate(r.received_at), title: `Received ${fmtDateTime(r.received_at)}, updated ${fmtDateTime(r.updated_at)}` }),
        h("td", { class: "right" }, btn)),
      detail);
  }
  const pickHead = h("th", { scope: "col", class: "pick-cell", hidden: true }, h("span", { class: "sr-only", text: "Move out" }));
  const slot = h("div", { hidden: true });
  let split = null;
  const moveBtn = h("button", { type: "button", class: "btn btn-sm", text: "Move records out",
    hidden: v.records.length < 2, onclick: () => setPicking(true) });
  function setPicking(on) {
    picked.clear();
    boxes.forEach((b) => { b.checked = false; });
    [pickHead, ...pickCells].forEach((c) => { c.hidden = !on; });
    moveBtn.hidden = on || v.records.length < 2;
    slot.hidden = !on;
    if (!on) return;
    split ||= splitPanel(v, picked, boxes, onSplit, () => { setPicking(false); moveBtn.focus(); });
    slot.replaceChildren(split.panel);
    split.reset();
  }
  return h("section", { class: "panel", "aria-labelledby": "src-h" },
    h("div", { class: "panel-head" }, h("h2", { id: "src-h", text: "Source records" }),
      h("span", { class: "hint", text: "How each record joined this customer" }), moveBtn),
    slot,
    h("div", { class: "table-scroll" }, h("table", { class: "records-table" },
      h("thead", {}, h("tr", {}, pickHead, ["Source", "Record ID", "Linked by", "Received", ""].map((t) =>
        h("th", { scope: "col" }, t || h("span", { class: "sr-only", text: "Details" }))))),
      tbody)));
}

function contactList(title, items, render) {
  return h("div", { class: "contact" }, h("h3", { text: title }),
    items.length ? h("ul", {}, items.map((x) => h("li", {}, render(x)))) : h("p", { class: "muted", text: "None held" }));
}

function profilePanels(v) {
  const p = v.profile;
  return h("aside", { class: "c360-side" },
    h("section", { class: "panel pad", "aria-labelledby": "who-h" },
      h("h2", { id: "who-h", text: "Who they are" }),
      h("dl", { class: "facts" },
        h("dt", { text: "Names" }), h("dd", {}, p.names.length ? p.names.map((n) => h("div", {}, n.value, " ", sources(n.sources))) : h("span", { class: "muted", text: "None held" })),
        h("dt", { text: "Date of birth" }), h("dd", {}, p.dates_of_birth.length
          ? p.dates_of_birth.map((d) => h("div", {}, fmtDob(d.value), " ", sources(d.sources)))
          : h("span", { class: "muted", text: "Not given" })))),
    h("section", { class: "panel pad", "aria-labelledby": "contact-h" },
      h("h2", { id: "contact-h", text: "Contact" }),
      contactList("Email", p.emails, (e) => [h("span", { class: "mono", text: e.value }), " ",
        e.shared ? tag("Shared", "tag-warn") : null, !e.usable ? tag("Not used for matching", "tag-muted") : null, sources(e.sources)]),
      contactList("Phone", p.phones, (x) => [h("span", { class: "mono", text: x.national || x.value }), " ",
        h("span", { class: "muted small", text: x.type }), x.shared ? tag("Shared", "tag-warn") : null, sources(x.sources)]),
      contactList("Addresses", p.addresses, (a) => [h("span", { text: a.value }), " ",
        a.current ? tag("Current", "tag-ok") : null, h("div", {}, a.uprn ? h("span", { class: "muted small", text: `UPRN ${a.uprn} · ` }) : null, sources(a.sources))])));
}

async function openCase(caseId, cid) {
  try {
    await api(`/cases/${caseId}/claim`, { method: "POST" });
  } catch (ex) {
    if (ex.status !== 409) return toast(ex.message);
    toast(ex.message);
  }
  location.hash = `#/case/${caseId}?from=${cid}`;
}

export async function viewCustomer(id) {
  setNav("lookup");
  show(loading("Loading the customer"));
  let v;
  try {
    v = await api(`/customers/${id}`, { query: { reason: state.reason } });
  } catch (ex) {
    show(h("div", { class: "empty-state" }, h("h1", { text: ex.status === 404 ? "No such customer" : "Couldn't load this customer" }),
      h("p", { class: "error", role: "alert", text: ex.message }), h("a", { class: "btn", href: "#/lookup", text: "Back to lookup" })));
    return;
  }
  // The name most sources agree on (a one-off typo should not head the page); the newest on a tie.
  const name = v.profile.names.reduce((best, n) => (!best || n.sources.length >= best.sources.length ? n : best), null)?.value
    || "Unnamed customer";
  const initials = name.split(/\s+/).map((x) => x[0]).slice(0, 2).join("").toUpperCase();
  const systems = [...new Set(v.records.map((r) => r.source_system))];

  const head = h("section", { class: "c360-head" },
    h("div", { class: "c360-id" },
      h("span", { class: "avatar avatar-lg", "aria-hidden": "true", text: initials }),
      h("div", {},
        h("h1", { text: name }),
        h("div", { class: "c360-meta" },
          tag(v.status === "active" ? "Active" : v.status, "tag-ok"),
          h("span", {}, "Customer ID ", h("span", { class: "mono", text: v.customer_id })),
          h("span", { text: `${plural(v.records.length, "source record")} from ${plural(systems.length, "system")}` }),
          h("span", { text: `Customer since ${fmtDate(v.created_at)}` })))),
    h("div", { class: "c360-actions" },
      h("button", { type: "button", class: "btn", text: "Copy customer ID", onclick: async () => {
        try { await navigator.clipboard.writeText(v.customer_id); toast("Customer ID copied."); }
        catch { toast("Couldn't copy; select the ID instead."); }
      } })));

  const notices = [];
  if (v.requested_customer_id !== v.customer_id) {
    notices.push(h("div", { class: "callout", role: "note" },
      h("p", {}, "Customer ", h("span", { class: "mono", text: shortId(v.requested_customer_id) }),
        " was merged into this one. Its ID still finds this customer.")));
  }
  for (const c of v.open_cases) {
    notices.push(h("div", { class: "callout callout-case" }, icon(ICONS.pair, 22),
      h("p", {}, h("b", { text: "Possible duplicate waiting for review. " }),
        "Customer ", h("a", { href: `#/customer/${c.other_customer_id}`, class: "mono", text: shortId(c.other_customer_id) }),
        ` matched on ${c.rule}. Case ${c.case_id}, raised ${fmtDate(c.created_at)}.`),
      h("button", { type: "button", class: "btn btn-primary", text: "Review this case", onclick: () => openCase(c.case_id, v.customer_id) })));
  }

  const history = h("section", { class: "panel pad", "aria-labelledby": "hist-h" },
    h("h2", { id: "hist-h", text: "History" }),
    h("ol", { class: "timeline" }, v.history.map((e) => h("li", {},
      h("span", { class: "when", text: fmtDateTime(e.created_at) }), h("span", { text: outcomeText(e) })))),
    v.merged_from.length ? h("p", { class: "hint" }, `Merged into this customer: ${v.merged_from.map(shortId).join(", ")}`) : null);

  const access = h("section", { class: "panel pad", "aria-labelledby": "acc-h" },
    h("h2", { id: "acc-h", text: "Who has looked at this customer" }),
    h("ul", { class: "access" }, v.recent_access.map((a) => h("li", {},
      h("b", { text: a.display_name }), ` ${fmtDateTime(a.accessed_at)}`,
      h("span", { class: "muted", text: ` · ${a.reason || "no reason given"}` })))));

  show(h("div", { class: "c360" },
    h("nav", { class: "crumbs", "aria-label": "Breadcrumb" }, h("a", { href: "#/lookup", text: "Lookup" }), " / ", name),
    head, ...notices,
    h("div", { class: "c360-cols" },
      profilePanels(v),
      h("div", { class: "c360-main" }, recordsTable(v, () => viewCustomer(v.customer_id)), history, access))));
}
