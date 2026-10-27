"use strict";

/* ------------------------------------------------------------------ helpers */

const $ = (sel, root = document) => root.querySelector(sel);
const main = $("#main");

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  kids.forEach((k) => el.append(k));
  return el;
}

const fmtDateTime = (iso) =>
  iso ? new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(iso)) : "";
const fmtDate = (iso) =>
  iso ? new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(new Date(iso)) : "";
const fmtTime = (iso) =>
  iso ? new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(new Date(iso)) : "";
const fmtDob = (d) => (d ? fmtDate(d + "T12:00:00") : "");
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const shortId = (id) => (id ? id.slice(0, 8) : "");

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 4500);
}

/* ------------------------------------------------------------------ API */

const state = { user: null };

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch("/review/api" + path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Requested-With": "review-app" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && path !== "/login") {
    state.user = null;
    location.hash = "#/login";
    throw new Error("Sign in to continue.");
  }
  if (!res.ok) {
    const detail = data && (typeof data.detail === "string" ? data.detail : "Check the form and try again.");
    throw new Error(detail || `Request failed (${res.status}).`);
  }
  return data;
}

/* ------------------------------------------------------------------ verdict marks */

const VERDICT = {
  match: "Match",
  close: "Close",
  differs: "Differs",
  conflict: "Conflict",
  missing: "Missing",
};

function mark(verdict) {
  const svg = s("svg", { class: "mark", viewBox: "0 0 18 18", role: "img", "aria-label": VERDICT[verdict] });
  svg.append(s("title", {}, VERDICT[verdict]));
  if (verdict === "match") {
    svg.append(s("circle", { cx: 9, cy: 9, r: 8, fill: "var(--match)" }),
      s("path", { d: "M5.2 9.3l2.5 2.4 5-5.3", fill: "none", stroke: "#fff", "stroke-width": 1.9, "stroke-linecap": "round", "stroke-linejoin": "round" }));
  } else if (verdict === "close") {
    svg.append(s("circle", { cx: 9, cy: 9, r: 7.2, fill: "var(--sheet)", stroke: "var(--close)", "stroke-width": 1.7 }),
      s("path", { d: "M9 1.8a7.2 7.2 0 0 1 0 14.4z", fill: "var(--close)" }));
  } else if (verdict === "conflict") {
    svg.append(s("circle", { cx: 9, cy: 9, r: 8, fill: "var(--conflict)" }),
      s("path", { d: "M6 6l6 6M12 6l-6 6", stroke: "#fff", "stroke-width": 1.9, "stroke-linecap": "round" }));
  } else if (verdict === "differs") {
    svg.append(s("circle", { cx: 9, cy: 9, r: 7.2, fill: "var(--sheet)", stroke: "var(--ink-3)", "stroke-width": 1.7 }));
  } else {
    svg.append(s("circle", { cx: 9, cy: 9, r: 7, fill: "var(--sheet)", stroke: "var(--missing)", "stroke-width": 1.5, "stroke-dasharray": "2.6 2.4" }));
  }
  return svg;
}

/* ------------------------------------------------------------------ shell */

function setNav(active) {
  $("#bar").hidden = !state.user;
  if (state.user) $("#who-name").textContent = state.user.display_name;
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

function show(...nodes) {
  main.replaceChildren(...nodes);
  main.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

$("#signout").addEventListener("click", async () => {
  try { await api("/logout", { method: "POST" }); } catch { /* already signed out */ }
  state.user = null;
  location.hash = "#/login";
});

/* ------------------------------------------------------------------ sign in */

function viewSignIn() {
  setNav(null);
  const err = h("p", { class: "error", role: "alert", hidden: true });
  const email = h("input", { class: "input", id: "email", type: "email", autocomplete: "username", required: true });
  const pw = h("input", { class: "input", id: "password", type: "password", autocomplete: "current-password", required: true });
  const btn = h("button", { class: "btn btn-primary", type: "submit", text: "Sign in" });
  const form = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      err.hidden = true;
      btn.disabled = true;
      try {
        state.user = await api("/login", { method: "POST", body: { email: email.value, password: pw.value } });
        location.hash = "#/queue";
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
        btn.disabled = false;
        pw.select();
      }
    },
  },
    h("div", { class: "field" }, h("label", { for: "email", text: "Email" }), email),
    h("div", { class: "field" }, h("label", { for: "password", text: "Password" }), pw),
    err, btn,
  );
  show(h("section", { class: "signin" },
    h("div", { class: "signin-panel" },
      s("svg", { class: "signin-mark", viewBox: "0 0 32 20", "aria-hidden": "true" },
        s("circle", { cx: 10, cy: 10, r: 7.5 }), s("circle", { cx: 22, cy: 10, r: 7.5 })),
      h("div", {}, h("h1", { text: "Identity review" }),
        h("p", { text: "Decide whether two customer records belong to the same person." })),
      form,
    )));
  email.focus();
}

/* ------------------------------------------------------------------ queue */

async function takeNext() {
  try {
    const { case_id } = await api("/cases/next", { method: "POST" });
    if (case_id === null) {
      toast("No cases are waiting.");
      if (location.hash !== "#/queue") location.hash = "#/queue";
      else route();
      return;
    }
    location.hash = `#/case/${case_id}`;
  } catch (ex) { toast(ex.message); }
}

function decisionTag(d) {
  return h("span", { class: `tag tag-${d}`, text: d === "merge" ? "Merged" : "Kept apart" });
}

async function viewQueue() {
  setNav("queue");
  show(h("p", { class: "loading", text: "Loading the queue" }));
  const [st, recent] = await Promise.all([api("/stats"), api("/history")]);

  const lede = st.open === 0
    ? h("h1", { class: "queue-lede", text: "No cases are waiting." })
    : h("h1", { class: "queue-lede" }, h("strong", { text: st.open.toLocaleString("en-GB") }),
      st.open === 1 ? " case is waiting for review." : " cases are waiting for review.");

  const action = st.mine > 0
    ? h("button", { class: "btn btn-primary btn-lg", onclick: takeNext, text: "Continue your case" })
    : h("button", { class: "btn btn-primary btn-lg", onclick: takeNext, disabled: st.open === 0, text: "Take next case" });

  const facts = h("dl", { class: "queue-facts" },
    h("dt", { text: "Held by you" }), h("dd", { text: st.mine }),
    h("dt", { text: "You decided today" }), h("dd", { text: st.decided_today }),
    h("dt", { text: "Team decided today" }), h("dd", { text: st.team_today }),
  );

  const left = h("section", {}, lede,
    st.open === 0 ? h("p", { class: "queue-empty", text: "New cases appear when an incoming record matches two customers equally well, or when a match falls in a tier that is set to go to review." }) : null,
    h("div", { class: "queue-actions" }, action), facts);

  const items = recent.slice(0, 6).map((r) =>
    h("li", {}, h("a", { href: `#/case/${r.case_id}` },
      decisionTag(r.decision), h("span", { text: `Case ${r.case_id}` }), h("span", { class: "when", text: fmtDateTime(r.decided_at) }),
      h("span", { class: "note", text: r.notes }))));
  const right = h("section", { class: "recent", "aria-labelledby": "recent-h" },
    h("h2", { id: "recent-h", text: "Your recent decisions" }),
    items.length ? h("ol", {}, items) : h("p", { class: "recent-empty", text: "Decisions you make will be listed here." }));

  show(h("div", { class: "queue" }, left, right));
}

/* ------------------------------------------------------------------ case */

const GENDER = { M: "Male", F: "Female", X: "Non-binary or other" };
function display(field, value) {
  if (field === "dob") return fmtDob(value);
  if (field === "gender") return GENDER[value] || value;
  return value;
}

function values(field, list) {
  if (!list.length) return h("span", { class: "none", text: "Not recorded" });
  return h("ul", { class: "vals" }, list.map((v) =>
    h("li", { class: "val" + (v.shared ? " shared" : ""), title: v.sources.join("\n") },
      h("span", { class: "v", text: display(field, v.value), "aria-label": v.shared ? `${display(field, v.value)}, in both customers` : null }),
      h("span", { class: "src", text: plural(v.sources.length, "source") }))));
}

function customerHead(cust, letter, older) {
  return h("div", { class: "hd" },
    h("div", { class: "hd-title" }, `Customer ${letter}`, older ? h("span", { class: "hd-sub", text: "older" }) : null),
    h("div", { class: "hd-sub", text: `Since ${fmtDate(cust.created_at)}, ${plural(cust.records.length, "source record")}` }),
    h("div", { class: "hd-id", text: cust.customer_id }));
}

function comparisonSheet(c) {
  const order = ["match", "close", "differs", "conflict", "missing"];
  const summary = h("div", { class: "summary", "aria-label": "Summary" },
    order.filter((k) => c.summary[k]).map((k) => h("span", {}, mark(k), `${c.summary[k]} ${VERDICT[k].toLowerCase()}`)));

  const grid = h("div", { class: "cmp", role: "table", "aria-label": "Customer comparison" },
    h("div", { class: "hd hd-lbl", role: "columnheader" }),
    customerHead(c.customer_a, "A", true),
    h("div", { class: "hd spine", "aria-hidden": "true" }),
    customerHead(c.customer_b, "B", false),
  );
  c.comparison.forEach((r, i) => {
    const last = i === c.comparison.length - 1;
    const cls = (base) => base + (last && !r.note ? " last" : "") + (r.note ? " row-note-prev" : "");
    grid.append(
      h("div", { class: cls("lbl"), role: "rowheader", text: r.label }),
      h("div", { class: cls(""), role: "cell" }, values(r.field, r.a)),
      h("div", { class: cls("spine"), role: "cell" }, mark(r.verdict)),
      h("div", { class: cls(""), role: "cell" }, values(r.field, r.b)),
    );
    if (r.note) grid.append(h("div", { class: "row-note" + (last ? " last" : ""), text: r.note }));
  });

  const legend = h("div", { class: "legend" },
    order.map((k) => h("span", {}, mark(k), VERDICT[k])),
    h("span", {}, h("span", { class: "shared-eg", text: "Underlined" }), "in both customers"));

  return h("section", { class: "sheet" }, summary, grid, legend);
}

function recordList(cust, letter, triggerId) {
  const recs = cust.records.map((r) => {
    const c = r.cleansed || {};
    const n = c.name || {};
    const name = [n.title, n.first_name, n.middle_names, n.last_name, n.suffix].filter(Boolean).join(" ");
    const addr = c.address ? (c.address.lines || []).join(", ") : "";
    const rows = [
      ["Name", name], ["Born", fmtDob(c.date_of_birth_usable ? c.date_of_birth : null)],
      ["Email", (c.emails || []).map((e) => e.address).join(", ")],
      ["Phone", (c.phones || []).map((p) => p.national || p.e164).join(", ")],
      ["Address", addr],
    ].filter(([, v]) => v);
    return h("div", { class: "rec" },
      h("div", { class: "rec-head" }, h("span", { text: `${r.source_system} ${r.source_record_id}` }),
        h("span", { class: "when", text: fmtDateTime(r.received_at) })),
      r.record_id === triggerId ? h("span", { class: "trig", text: "This record raised the case" }) : null,
      h("dl", {}, rows.map(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })])));
  });
  return h("details", { class: "recs" }, h("summary", { text: `Customer ${letter} source records (${cust.records.length})` }), recs);
}

function outcomePanel(c, result, fresh) {
  const merged = (result ? result.decision : c.decision) === "merge";
  const stamp = h("span", { class: "stamp" + (merged ? "" : " keep") + (fresh ? " fresh" : ""), text: merged ? "Merged" : "Kept apart" });
  let msg;
  if (result) {
    const keptLetter = result.survivor_customer_id === c.customer_a.customer_id ? "A" : "B";
    msg = merged
      ? `${plural(result.source_records_moved, "source record")} moved to customer ${keptLetter}. The other customer ID now redirects to it.`
      : "These customers stay separate. This pair won't be suggested again.";
  } else {
    msg = `Decided by ${c.decided_by} on ${fmtDateTime(c.decided_at)}.`;
  }
  const labels = Object.fromEntries(Object.values(c.reason_options || {}).flat().map((o) => [o.code, o.label]));
  const codes = result ? result.reason_codes || [] : c.reason_codes || [];
  const conf = result ? result.confidence : c.confidence;
  return h("section", { class: "outcome", "aria-live": "polite" },
    stamp, h("p", { text: msg }),
    codes.length ? h("ul", { class: "reason-list" }, codes.map((k) => h("li", { text: labels[k] || k }))) : null,
    conf ? h("p", { class: "hint", text: `Confidence: ${conf}` }) : null,
    !result && c.notes ? h("blockquote", { text: c.notes }) : null,
    h("div", { class: "actions" },
      h("button", { class: "btn btn-primary", onclick: takeNext, text: "Take next case" }),
      h("a", { class: "btn", href: "#/queue", text: "Back to queue" })));
}

function decisionForm(c, onDone) {
  let decision = null;
  let survivor = c.customer_a.customer_id;
  const notes = h("textarea", { id: "notes", maxlength: 4000, placeholder: "What decided it? For example: same mobile and date of birth; surname changed after marriage." });
  const err = h("p", { class: "error", role: "alert", hidden: true });
  const submit = h("button", { class: "btn btn-primary btn-lg", type: "submit", disabled: true, text: "Choose a decision" });
  const conflicts = c.comparison.filter((r) => r.verdict === "conflict").map((r) => r.label.toLowerCase());
  const warn = h("p", { class: "warn", role: "status", hidden: true,
    text: conflicts.length ? `The ${conflicts.join(" and ")} conflict. Merge only if you're sure one of them is a data error.` : "" });

  const survivorBox = h("fieldset", { class: "choices survivor", hidden: true },
    h("legend", { class: "label", text: "Keep which customer ID?" }),
    h("label", { class: "choice" },
      h("input", { type: "radio", name: "survivor", value: c.customer_a.customer_id, checked: true, onchange: (e) => { survivor = e.target.value; } }),
      h("b", { text: "Customer A" }), h("small", { text: `Older, since ${fmtDate(c.customer_a.created_at)}. Other systems are more likely to hold this ID.` })),
    h("label", { class: "choice" },
      h("input", { type: "radio", name: "survivor", value: c.customer_b.customer_id, onchange: (e) => { survivor = e.target.value; } }),
      h("b", { text: "Customer B" }), h("small", { text: `Since ${fmtDate(c.customer_b.created_at)}.` })));

  const picked = new Set();
  let confidence = null;
  const reasonBox = h("fieldset", { class: "reasons", hidden: true },
    h("legend", { class: "label", text: "What decided it?" }));
  const reasonChips = h("div", { class: "chips" });
  reasonBox.append(reasonChips, h("span", { class: "hint", text: "Pick every reason that applies." }));

  function renderReasons() {
    picked.clear();
    reasonChips.replaceChildren(...((c.reason_options || {})[decision] || []).map((o) =>
      h("label", { class: "chip" },
        h("input", { type: "checkbox", value: o.code, onchange: (e) => { e.target.checked ? picked.add(o.code) : picked.delete(o.code); refresh(); } }),
        h("span", { text: o.label }))));
  }

  const confBox = h("fieldset", { class: "segmented-wrap", hidden: true },
    h("legend", { class: "label", text: "How sure are you?" }),
    h("div", { class: "segmented" }, [["certain", "Certain"], ["likely", "Likely"], ["unsure", "Unsure"]].map(([v, t]) =>
      h("label", {}, h("input", { type: "radio", name: "confidence", value: v, onchange: () => { confidence = v; refresh(); } }), h("span", { text: t })))));

  function refresh() {
    survivorBox.hidden = decision !== "merge";
    reasonBox.hidden = !decision;
    confBox.hidden = !decision;
    warn.hidden = decision !== "merge" || !conflicts.length;
    submit.textContent = decision === "merge" ? "Merge customers" : decision === "keep_apart" ? "Keep apart" : "Choose a decision";
    submit.disabled = !decision || !picked.size || !confidence || notes.value.trim().length < 3;
  }
  notes.addEventListener("input", refresh);

  const choice = (value, title, sub) => h("label", { class: "choice" },
    h("input", { type: "radio", name: "decision", value, onchange: () => { decision = value; renderReasons(); refresh(); } }),
    h("b", { text: title }), h("small", { text: sub }));

  const form = h("form", {
    class: "decide",
    onsubmit: async (e) => {
      e.preventDefault();
      if (submit.disabled) return;
      err.hidden = true;
      submit.disabled = true;
      try {
        const result = await api(`/cases/${c.case_id}/decision`, {
          method: "POST",
          body: { decision, notes: notes.value, survivor_customer_id: decision === "merge" ? survivor : null,
                  reason_codes: [...picked], confidence },
        });
        onDone({ ...result, reason_codes: [...picked], confidence });
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
        refresh();
      }
    },
    onkeydown: (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") form.requestSubmit(); },
  },
    h("h2", { text: "Your decision" }),
    h("fieldset", { class: "choices" },
      h("legend", { class: "label", text: "Are these the same person?" }),
      choice("merge", "Same person", "Merge into one customer. Records from both move to the ID you keep."),
      choice("keep_apart", "Different people", "Keep both customers. This pair won't be suggested again.")),
    warn,
    survivorBox,
    reasonBox,
    confBox,
    h("div", { class: "field" }, h("label", { for: "notes", text: "Notes" }), notes,
      h("span", { class: "hint", text: "Required. Saved for audit and to teach the future AI reviewer." })),
    err, submit,
    h("p", { class: "kbd", text: "Ctrl + Enter to submit" }),
  );
  return form;
}

async function viewCase(id) {
  setNav("queue");
  show(h("p", { class: "loading", text: "Loading the case" }));
  let c;
  try { c = await api(`/cases/${id}`); } catch (ex) { show(h("p", { class: "error", text: ex.message })); return; }

  const tierText = c.tier ? ` (${c.tier} tier)` : "";
  const why = h("p", { class: "case-why" },
    "An incoming record matched these customers on ", h("b", { text: c.rule_description }), tierText + ".",
    c.trigger ? ` That record was ${c.trigger.source}, received ${fmtDateTime(c.trigger.received_at)}.` : "");

  const hold = h("div", { class: "case-hold" });
  const rail = h("aside", { class: "rail", "aria-label": "Decision" });

  function renderRail(result, fresh) {
    if (result || c.status !== "open") {
      rail.replaceChildren(outcomePanel(c, result, fresh));
      hold.replaceChildren(decisionTag(result ? result.decision : c.decision));
      return;
    }
    if (c.held_by_me) {
      hold.replaceChildren(
        h("span", { text: `Held for you until ${fmtTime(c.claim_expires_at)}` }),
        h("button", { class: "link", type: "button", text: "Release case", onclick: async () => {
          try { await api(`/cases/${c.case_id}/release`, { method: "POST" }); toast("Case released to the queue."); location.hash = "#/queue"; }
          catch (ex) { toast(ex.message); }
        } }));
      rail.replaceChildren(decisionForm(c, (res) => { renderRail(res, true); }));
    } else {
      hold.replaceChildren(h("span", { text: c.claimed_by ? `Held by ${c.claimed_by}` : "Waiting in the queue" }));
      rail.replaceChildren(h("section", { class: "outcome" },
        h("p", { text: c.claimed_by ? `${c.claimed_by} is reviewing this case.` : "Cases are reviewed in the order they were raised." }),
        h("button", { class: "btn btn-primary", onclick: takeNext, text: "Take next case" })));
    }
  }
  renderRail(null, false);

  const trigId = c.trigger ? c.trigger.record_id : null;
  show(h("div", { class: "case" },
    h("header", { class: "case-head" }, h("h1", { text: `Case ${c.case_id}` }), hold, why),
    h("div", {}, comparisonSheet(c),
      h("div", { class: "records" }, recordList(c.customer_a, "A", trigId), recordList(c.customer_b, "B", trigId))),
    rail));
}

/* ------------------------------------------------------------------ history */

async function viewHistory() {
  setNav("history");
  const everyone = new URLSearchParams(location.hash.split("?")[1] || "").get("everyone") === "1";
  show(h("p", { class: "loading", text: "Loading decisions" }));
  const rows = await api(`/history${everyone ? "?everyone=true" : ""}`);

  const toggle = state.user && state.user.role === "admin"
    ? h("label", { class: "toggle" },
      h("input", { type: "checkbox", checked: everyone, onchange: (e) => { location.hash = e.target.checked ? "#/history?everyone=1" : "#/history"; } }),
      "Show everyone's decisions")
    : null;

  const body = rows.length
    ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Case", "Decision", "Raised by", "Decided", everyone ? "Reviewer" : null, "Notes"].filter(Boolean).map((t) => h("th", { text: t })))),
      h("tbody", {}, rows.map((r) => h("tr", { tabindex: 0, onclick: () => (location.hash = `#/case/${r.case_id}`), onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/case/${r.case_id}`; } },
        h("td", { text: r.case_id }), h("td", {}, decisionTag(r.decision)), h("td", { class: "muted", text: r.rule }),
        h("td", { class: "muted", text: fmtDateTime(r.decided_at) }), everyone ? h("td", { class: "muted", text: r.decided_by }) : null,
        h("td", { class: "notes", text: r.notes }))))))
    : h("p", { class: "queue-empty", text: "No decisions yet. Take a case from the queue to start." });

  const panel = state.user && state.user.role === "admin" ? await trainingPanel() : null;
  show(h("div", { class: "page-head" }, h("h1", { text: everyone ? "All decisions" : "Your decisions" }), toggle), body, panel);
}

async function trainingPanel() {
  let summary = {};
  try { summary = await api("/examples/summary"); } catch { return null; }
  const count = (src) => Object.values(summary[src] || {}).reduce((a, b) => a + b, 0);
  const splits = (src) => ["train", "dev", "test"].map((k) => `${(summary[src] || {})[k] || 0} ${k}`).join(", ");
  let source = "reviewer";
  const links = h("div", { class: "export-links" });
  function renderLinks() {
    const q = (fmt, extra = "") => `/review/api/examples/export?format=${fmt}&source=${source}&pii=masked${extra}`;
    links.replaceChildren(
      h("a", { class: "btn", href: q("chat"), download: "", text: "Chat examples" }),
      h("a", { class: "btn", href: q("eval", "&split=test"), download: "", text: "Eval set (test split)" }),
      h("a", { class: "btn", href: q("prompt", "&shots=6"), download: "", text: "Few-shot prompt" }));
  }
  renderLinks();
  const sourceSel = h("select", { class: "input select", "aria-label": "Labels to export", onchange: (e) => { source = e.target.value; renderLinks(); } },
    h("option", { value: "reviewer", text: `Reviewer decisions (${count("reviewer")})` }),
    h("option", { value: "synthetic_truth", text: `Synthetic ground truth (${count("synthetic_truth")})` }),
    h("option", { value: "all", text: "Both" }));
  return h("section", { class: "training", "aria-labelledby": "training-h" },
    h("h2", { id: "training-h", text: "AI reviewer training data" }),
    h("p", { class: "hint", text: `Every decision is saved as a labelled example with a snapshot of the case as it was shown. Reviewer examples: ${splits("reviewer")}. Exports mask names, emails, phones, addresses and dates of birth.` }),
    h("div", { class: "export-row" }, sourceSel, links));
}

/* ------------------------------------------------------------------ router */

async function route() {
  const hash = location.hash || "#/queue";
  if (!state.user && hash !== "#/login") {
    try { state.user = await api("/me"); } catch { return; }
  }
  try {
    if (hash === "#/login") return viewSignIn();
    const m = hash.match(/^#\/case\/(\d+)/);
    if (m) return await viewCase(m[1]);
    if (hash.startsWith("#/history")) return await viewHistory();
    return await viewQueue();
  } catch (ex) {
    toast(ex.message);
  }
}

window.addEventListener("hashchange", route);
route();
