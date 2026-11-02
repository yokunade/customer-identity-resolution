// One review case: the side-by-side comparison and the decision.

import { api, fmtDate, fmtDateTime, fmtDob, fmtTime, h, hashParams, loading, plural, s, show, state, toast } from "./core.js";
import { setNav } from "./shell.js";
import { decisionTag, takeNext } from "./queue.js";

const VERDICT = { match: "Match", close: "Close", differs: "Differs", conflict: "Conflict", missing: "Missing" };

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
  const link = state.user && state.user.can_lookup
    ? h("a", { href: `#/customer/${cust.customer_id}`, class: "hd-id mono", text: cust.customer_id })
    : h("div", { class: "hd-id mono", text: cust.customer_id });
  return h("div", { class: "hd" },
    h("div", { class: "hd-title" }, h("span", { class: `letter letter-${letter}`, text: letter }), `Customer ${letter}`,
      older ? h("span", { class: "hd-sub", text: "older" }) : null),
    h("div", { class: "hd-sub", text: `Since ${fmtDate(cust.created_at)}, ${plural(cust.records.length, "source record")}` }),
    link);
}

function comparisonSheet(c) {
  const order = ["match", "close", "differs", "conflict", "missing"];
  const summary = h("div", { class: "summary", "aria-label": "Summary" },
    order.filter((k) => c.summary[k]).map((k) => h("span", {}, mark(k), `${c.summary[k]} ${VERDICT[k].toLowerCase()}`)));

  const grid = h("div", { class: "cmp", role: "table", "aria-label": "Customer comparison" },
    h("div", { class: "hd hd-lbl", role: "columnheader" }),
    customerHead(c.customer_a, "A", true),
    h("div", { class: "hd spine", "aria-hidden": "true" }),
    customerHead(c.customer_b, "B", false));
  c.comparison.forEach((r, i) => {
    const last = i === c.comparison.length - 1;
    const cls = (base) => base + (last && !r.note ? " last" : "") + (r.note ? " row-note-prev" : "");
    grid.append(
      h("div", { class: cls("lbl"), role: "rowheader", text: r.label }),
      h("div", { class: cls(""), role: "cell" }, values(r.field, r.a)),
      h("div", { class: cls("spine"), role: "cell" }, mark(r.verdict)),
      h("div", { class: cls(""), role: "cell" }, values(r.field, r.b)));
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

function outcomePanel(c, result, fresh, from) {
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
  const answer = result ? result.demo_answer : c.demo_answer;
  let reveal = null;
  if (answer) {
    const truth = answer.decision === "merge" ? "same person" : "different people";
    reveal = h("div", { class: "demo-answer " + (answer.matches ? "ok" : "miss") },
      h("p", { class: "demo-answer-head" }, mark(answer.matches ? "match" : "conflict"),
        answer.matches ? `Matches the known answer: ${truth}.` : `The known answer is ${truth}.`),
      answer.title ? h("p", { class: "demo-answer-title", text: answer.title }) : null,
      h("p", { text: answer.explanation || "Synthetic demo data: the generator knows which person each record belongs to." }),
      h("p", { class: "hint", text: "Demo mode only" }));
  }
  return h("section", { class: "outcome", "aria-live": "polite" },
    stamp, h("p", { text: msg }), reveal,
    codes.length ? h("ul", { class: "reason-list" }, codes.map((k) => h("li", { text: labels[k] || k }))) : null,
    conf ? h("p", { class: "hint", text: `Confidence: ${conf}` }) : null,
    !result && c.notes ? h("blockquote", { text: c.notes }) : null,
    h("div", { class: "actions" },
      from ? h("a", { class: "btn btn-primary", href: `#/customer/${from}`, text: "Back to the customer" }) : null,
      h("button", { class: from ? "btn" : "btn btn-primary", onclick: takeNext, text: "Take the next case" }),
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
  const reasonBox = h("fieldset", { class: "reasons", hidden: true }, h("legend", { class: "label", text: "What decided it?" }));
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
    warn, survivorBox, reasonBox, confBox,
    h("div", { class: "field" }, h("label", { for: "notes", text: "Notes" }), notes,
      h("span", { class: "hint", text: "Required. Saved for audit and to teach the future AI reviewer." })),
    err, submit,
    h("p", { class: "kbd", text: "Ctrl + Enter to submit" }));
  return form;
}

export async function viewCase(id) {
  setNav("queue");
  const from = hashParams().get("from");
  show(loading("Loading the case"));
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
      rail.replaceChildren(outcomePanel(c, result, fresh, from));
      hold.replaceChildren(decisionTag(result ? result.decision : c.decision));
      return;
    }
    if (c.held_by_me) {
      hold.replaceChildren(
        h("span", { text: `Held for you until ${fmtTime(c.claim_expires_at)}` }),
        h("button", { class: "link", type: "button", text: "Release case", onclick: async () => {
          try {
            await api(`/cases/${c.case_id}/release`, { method: "POST" });
            toast("Case released to the queue.");
            location.hash = from ? `#/customer/${from}` : "#/queue";
          } catch (ex) { toast(ex.message); }
        } }));
      rail.replaceChildren(decisionForm(c, (res) => { renderRail(res, true); }));
    } else {
      hold.replaceChildren(h("span", { text: c.claimed_by ? `Held by ${c.claimed_by}` : "Waiting in the queue" }));
      rail.replaceChildren(h("section", { class: "outcome" },
        h("p", { text: c.claimed_by ? `${c.claimed_by} is reviewing this case.` : "Cases are reviewed in the order they were raised." }),
        h("button", { class: "btn btn-primary", onclick: takeNext, text: "Take the next case" })));
    }
  }
  renderRail(null, false);

  const crumbs = h("nav", { class: "crumbs", "aria-label": "Breadcrumb" },
    h("a", { href: "#/queue", text: "Review queue" }), ` / Case ${c.case_id}`,
    from ? [" · ", h("a", { href: `#/customer/${from}`, text: "opened from a customer" })] : null);
  const trigId = c.trigger ? c.trigger.record_id : null;
  show(crumbs, h("div", { class: "case" },
    h("header", { class: "case-head" }, h("h1", { text: "Are these the same person?" }), hold, why),
    h("div", {}, comparisonSheet(c),
      h("div", { class: "records" }, recordList(c.customer_a, "A", trigId), recordList(c.customer_b, "B", trigId))),
    rail));
}
