// Lookup home and search results (managers).

import { ICONS, api, fmtAge, fmtAgo, fmtDate, fmtDateTime, fmtDob, fmtNum, h, hashParams, icon, loading, plural, shortId,
  show, state } from "./core.js";
import { setNav } from "./shell.js";

export const KINDS = [
  ["auto", "Anything"], ["email", "Email"], ["phone", "Phone"], ["postcode", "Postcode"],
  ["name_dob", "Name and date of birth"], ["source_record", "Source record"], ["customer_id", "Customer ID"],
];
const KIND_LABEL = Object.fromEntries(KINDS);
KIND_LABEL.auto = "search";
const PLACEHOLDER = {
  auto: "Email, phone number, postcode or ID", email: "name@example.co.uk", phone: "07700 900123",
  postcode: "LS6 2AB", name_dob: "Forename and surname", source_record: "SYSTEM/RECORD-ID, e.g. CRM/C-1042",
  customer_id: "79532f8d-d251-4a4a-b570-f4ff98ec1e92",
};
export const REASONS = ["Customer contact (call, email, chat)", "Complaint or dispute", "Data rights request",
  "Fraud investigation", "Data quality check"];

export function searchHash(q, type = "auto", dob = "") {
  const p = new URLSearchParams({ q });
  if (type !== "auto") p.set("type", type);
  if (dob) p.set("dob", dob);
  return "#/search?" + p;
}

export function reasonPicker(id = "reason") {
  const sel = h("select", { id, class: "input select", onchange: (e) => { state.reason = e.target.value; } },
    h("option", { value: "", text: "No reason given" }),
    REASONS.map((r) => h("option", { value: r, text: r, selected: state.reason === r })));
  return h("div", { class: "reason" },
    h("label", { for: id }, "Reason ", h("span", { class: "optional", text: "(optional)" })), sel,
    h("span", { class: "hint", text: "Every lookup is recorded with your name; a reason helps if it is ever questioned." }));
}

function searchForm({ q = "", type = "auto", dob = "" } = {}) {
  let kind = type;
  const input = h("input", { id: "q", class: "input search-input", type: "search", value: q, maxlength: 200, autocomplete: "off",
    placeholder: PLACEHOLDER[kind], "aria-describedby": "q-hint", required: true });
  const dobInput = h("input", { id: "dob", class: "input", type: "text", value: dob, maxlength: 40, inputmode: "numeric",
    placeholder: "DD/MM/YYYY", autocomplete: "off" });
  const dobField = h("div", { class: "field dob-field", hidden: kind !== "name_dob" },
    h("label", { for: "dob", text: "Date of birth" }), dobInput);
  const chips = h("div", { class: "kind-chips", role: "radiogroup", "aria-label": "Search by" },
    KINDS.map(([v, label]) => h("label", { class: "kind" },
      h("input", { type: "radio", name: "kind", value: v, checked: v === kind,
        onchange: () => { kind = v; input.placeholder = PLACEHOLDER[v]; dobField.hidden = v !== "name_dob"; input.focus(); } }),
      h("span", { text: label }))));
  const form = h("form", { class: "search", role: "search", onsubmit: (e) => {
    e.preventDefault();
    if (!input.value.trim()) return input.focus();
    location.hash = searchHash(input.value.trim(), kind, kind === "name_dob" ? dobInput.value.trim() : "");
  } },
    chips,
    h("div", { class: "search-row" },
      h("label", { class: "sr-only", for: "q", text: "Search for" }),
      h("div", { class: "search-box" }, icon(ICONS.search, 22), input),
      dobField,
      h("button", { class: "btn btn-primary btn-lg", type: "submit", text: "Search" })),
    h("p", { id: "q-hint", class: "hint", text: "What you type is cleansed like an incoming record: 07700 900123, +44 7700 900123 and 7700900123 find the same customers, and so do Bill.Smith+news@googlemail.com and billsmith@gmail.com." }),
    reasonPicker());
  return { form, input };
}

/* ------------------------------------------------------------------ home */

export async function viewLookup() {
  setNav("lookup");
  const { form, input } = searchForm();
  const recentList = h("div", {}, loading("Loading your recent lookups"));
  const queueBox = h("div", {}, loading("Loading the queue"));
  show(h("div", { class: "lookup" },
    h("section", { class: "find", "aria-labelledby": "find-h" }, h("h1", { id: "find-h", text: "Find a customer" }), form),
    h("div", { class: "lookup-cols" },
      h("section", { class: "panel", "aria-labelledby": "recent-h" },
        h("div", { class: "panel-head" }, h("h2", { id: "recent-h", text: "Your recent lookups" })), recentList),
      h("section", { class: "panel", "aria-labelledby": "rq-h" },
        h("div", { class: "panel-head" }, h("h2", { id: "rq-h", text: "Review queue" })), queueBox))));
  input.focus();

  const [recent, ov, st] = await Promise.all([api("/lookup/recent"), api("/overview"), api("/stats")]);
  recentList.replaceChildren(recent.length
    ? h("ul", { class: "recent-lookups" }, recent.map((r) => h("li", {}, h("a", {
      href: r.customers === 1 ? `#/customer/${r.customer_id}` : searchHash(r.search_value, r.search_type) },
    h("span", { class: "rl-who", text: r.customers === 1 ? (r.name || shortId(r.customer_id))
      : r.customers ? `${fmtNum(r.customers)} customers` : "No customer found" }),
    h("span", { class: "mono rl-what", text: r.search_value }),
    h("span", { class: "rl-when", text: `${r.reason ? r.reason.split(" (")[0] + " · " : ""}${fmtAgo(r.accessed_at)}` })))))
    : h("p", { class: "panel-empty", text: "Customers you look up will be listed here." }));

  const outstanding = ov.waiting + ov.in_progress;
  queueBox.replaceChildren(h("div", { class: "mini-tiles" },
    h("div", {}, h("b", { text: fmtNum(outstanding) }), h("span", { text: "Open cases" })),
    h("div", {}, h("b", { text: ov.oldest_open_at ? fmtAge(ov.oldest_open_at) : "None" }), h("span", { text: "Oldest waiting" })),
    h("div", {}, h("b", { text: fmtNum(ov.in_progress) }), h("span", { text: "In progress" })),
    h("div", {}, h("b", { text: fmtNum(st.team_today) }), h("span", { text: "Decided today" }))),
  h("div", { class: "panel-foot" }, h("a", { class: "btn btn-primary btn-block", href: "#/queue", text: "Go to the review queue" })));
}

/* ------------------------------------------------------------------ results */

const tag = (text, cls = "") => h("span", { class: "tag " + cls, text });

export async function viewSearch() {
  setNav("lookup");
  const p = hashParams();
  const q = p.get("q") || "", type = p.get("type") || "auto", dob = p.get("dob") || "";
  const { form } = searchForm({ q, type, dob });
  const body = h("div", {}, loading("Searching"));
  show(h("div", { class: "results" },
    h("details", { class: "refine" }, h("summary", { text: "Change the search" }), form),
    body));

  let res;
  try {
    res = await api("/lookup", { query: { q, type, dob, reason: state.reason } });
  } catch (ex) {
    body.replaceChildren(h("div", { class: "empty-state" },
      h("h1", { text: "That search didn't work" }), h("p", { class: "error", role: "alert", text: ex.message }),
      h("a", { class: "btn", href: "#/lookup", text: "Back to lookup" })));
    form.closest("details").open = true;
    return;
  }

  const what = `${KIND_LABEL[res.searched_by] || res.searched_by}`;
  if (res.customers.length === 1 && !res.shared && !res.truncated) {
    // One customer: straight to them, replacing this step in history so Back returns to the search.
    location.replace(`#/customer/${res.customers[0].customer_id}`);
    return;
  }
  if (!res.customers.length) {
    body.replaceChildren(h("div", { class: "empty-state" },
      h("h1", { text: "No customer found" }),
      h("p", {}, `Nothing is held for ${what.toLowerCase()} `, h("span", { class: "mono", text: res.normalised_value }), "."),
      h("p", { class: "hint", text: "Try another identifier: an old email, a landline, or the postcode." }),
      h("a", { class: "btn", href: "#/lookup", text: "Back to lookup" })));
    return;
  }

  const n = res.customers.length;
  const rows = res.customers.map((c) => h("tr", {},
    h("td", {}, h("a", { class: "strong-link", href: `#/customer/${c.customer_id}`, text: c.name || "Unnamed" }),
      h("div", { class: "mono sub", text: shortId(c.customer_id) })),
    h("td", { text: c.date_of_birth ? fmtDob(c.date_of_birth) : "", class: c.date_of_birth ? "" : "muted" },
      c.date_of_birth ? null : "Not given"),
    h("td", { text: c.postcode || "" }),
    h("td", { class: "mono small" }, [...c.emails, ...c.phones].filter((v) => v !== res.normalised_value).slice(0, 2).join(", ")),
    h("td", {}, h("div", { class: "tags" }, c.sources.map((src) => tag(src, "tag-source"))),
      c.open_case ? h("div", { class: "tags" }, tag("Open case", "tag-warn")) : null),
    h("td", { class: "muted nowrap", text: fmtDate(c.updated_at), title: fmtDateTime(c.updated_at) })));

  body.replaceChildren(...[
    h("div", { class: "page-head" },
      h("div", {},
        h("a", { href: "#/lookup", text: "Back to lookup" }),
        h("h1", { text: `${res.truncated ? "More than " : ""}${plural(n, "customer")} found` }),
        h("p", { class: "sub" }, `${what[0].toUpperCase() + what.slice(1)} `, h("span", { class: "mono", text: res.normalised_value }),
          res.truncated ? ` · showing the ${n} most recently updated` : " · most recently updated first"))),
    res.shared ? h("div", { class: "callout callout-warn", role: "note" }, icon(ICONS.warn, 22),
      h("div", {}, h("b", { text: "Shared value: not used for matching" }),
        h("p", { text: "So many customers hold this that custIDR treats it as shared (a company inbox, a switchboard, a big-user postcode) and never links records on it alone. Check name, date of birth and address before acting on any one customer." }))) : null,
    h("div", { class: "table-wrap" }, h("table", { class: "results-table" },
      h("thead", {}, h("tr", {}, ["Customer", "Date of birth", "Postcode", "Other contact", "Records", "Updated"].map((t) => h("th", { scope: "col", text: t })))),
      h("tbody", {}, rows)))].filter(Boolean));
}
