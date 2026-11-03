// Past decisions, and the training-data export for admins.

import { api, fmtDateTime, h, hashParams, loading, show, state } from "./core.js";
import { setNav } from "./shell.js";
import { decisionTag } from "./queue.js";

export async function viewHistory() {
  setNav("history");
  const everyone = hashParams().get("everyone") === "1";
  show(loading("Loading decisions"));
  const rows = await api("/history", { query: { everyone: everyone ? "true" : undefined } });
  const admin = state.user && state.user.role === "admin";

  const toggle = admin
    ? h("label", { class: "toggle" },
      h("input", { type: "checkbox", checked: everyone, onchange: (e) => { location.hash = e.target.checked ? "#/history?everyone=1" : "#/history"; } }),
      "Show everyone's decisions")
    : null;

  const body = rows.length
    ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Case", "Decision", "Raised by", "Decided", everyone ? "Reviewer" : null, "Notes"].filter(Boolean).map((t) => h("th", { scope: "col", text: t })))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", {}, h("a", { href: `#/case/${r.case_id}`, text: r.case_id })), h("td", {}, decisionTag(r.decision)),
        h("td", { class: "muted", text: r.rule }),
        h("td", { class: "muted", text: fmtDateTime(r.decided_at) }), everyone ? h("td", { class: "muted", text: r.decided_by }) : null,
        h("td", { class: "notes", text: r.notes }))))))
    : h("p", { class: "empty-note", text: "No decisions yet. Take a case from the queue to start." });

  const panel = admin ? await trainingPanel() : null;
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
      h("a", { class: "btn", href: q("chat"), download: "", text: "Chat examples (masked, best effort)" }),
      h("a", { class: "btn", href: q("eval", "&split=test"), download: "", text: "Eval set, test split (masked, best effort)" }),
      h("a", { class: "btn", href: q("prompt", "&shots=6"), download: "", text: "Few-shot prompt (masked, best effort)" }));
  }
  renderLinks();
  const sourceSel = h("select", { class: "input select", "aria-label": "Labels to export", onchange: (e) => { source = e.target.value; renderLinks(); } },
    h("option", { value: "reviewer", text: `Reviewer decisions (${count("reviewer")})` }),
    h("option", { value: "synthetic_truth", text: `Synthetic ground truth (${count("synthetic_truth")})` }),
    h("option", { value: "all", text: "Both" }));
  return h("section", { class: "panel pad training", "aria-labelledby": "training-h" },
    h("h2", { id: "training-h", text: "AI reviewer training data" }),
    h("p", { class: "hint", text: `Every decision is saved as a labelled example with a snapshot of the case as it was shown. Reviewer examples: ${splits("reviewer")}. Best-effort masking: names, emails, phones, addresses and dates of birth are replaced, and reviewer notes are left out. Check a sample before sharing.` }),
    h("div", { class: "export-row" }, sourceSel, links));
}
