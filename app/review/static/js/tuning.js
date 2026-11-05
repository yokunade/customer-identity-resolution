// Demo tuning (admins): change the engine settings, re-run the demo records and compare accuracy run by run.

import { api, fmtDateTime, fmtNum, h, loading, plural, show, toast } from "./core.js";
import { setNav } from "./shell.js";

const STATE = { correct: "Correct", split: "Split across customers", mixed: "Mixed with someone else" };
const GROUPS = [
  ["fixed", "Now resolved correctly", "good"],
  ["broken", "Now resolved wrongly", "bad"],
  ["left_review", "No longer in review", "good"],
  ["entered_review", "Now in review", "bad"],
  ["changed", "Still wrong, differently", ""],
];
const MODES = [["auto", "Links"], ["review", "Review"], ["off", "Off"]];
const TIERS = ["exact", "strong", "probable", "possible"];
let poll = null;

const pct = (x) => `${(100 * x).toFixed(2)}%`;
const clone = (x) => JSON.parse(JSON.stringify(x));

// label, value, sub-text, which way is better, how to show a change
const TILES = [
  ["Precision", (s) => pct(s.precision), (s) => "Links that joined the same person", "up", (d) => `${(100 * d).toFixed(2)} pts`, "precision"],
  ["Recall", (s) => pct(s.recall), (s) => "Right links that were made", "up", (d) => `${(100 * d).toFixed(2)} pts`, "recall"],
  ["People resolved correctly", (s) => fmtNum(s.people_correct), (s) => `of ${fmtNum(s.people)} people`, "up", fmtNum, "people_correct"],
  ["Customers mixing people", (s) => fmtNum(s.customers_overmerged), (s) => `${fmtNum(s.people_split)} people split across customers`, "down", fmtNum, "customers_overmerged"],
  ["Review cases", (s) => fmtNum(s.open_cases), (s) => `${fmtNum(s.cases_different)} different people · ${fmtNum(s.cases_mixed)} mixed`, "down", fmtNum, "open_cases"],
  ["Avoidable reviews", (s) => fmtNum(s.cases_same), (s) => "Cases where both are the same person", "down", fmtNum, "cases_same"],
  ["Wrong links", (s) => fmtNum(s.wrong_links), (s) => `of ${fmtNum(s.links)} links on arrival`, "down", fmtNum, "wrong_links"],
];

function delta(now, before, key, better, fmt, prevId) {
  if (!before) return null;
  const d = now[key] - before[key];
  if (Math.abs(d) < 1e-9) return h("dd", { class: "tile-sub", text: `No change from run ${prevId}` });
  const good = (d > 0) === (better === "up");
  return h("dd", { class: `tile-delta ${good ? "good" : "bad"}`, text: `${d > 0 ? "▲" : "▼"} ${fmt(Math.abs(d))} vs run ${prevId}` });
}

function results(run) {
  const s = run.scores;
  const before = run.previous?.scores;
  return h("section", { class: "tune-section", "aria-labelledby": "tune-res" },
    h("div", { class: "tune-section-head" },
      h("h2", { id: "tune-res", text: `Run ${run.run_id}${run.note ? `: ${run.note}` : ""}` }),
      h("p", { class: "hint", text: `${fmtNum(s.records)} records, ${fmtNum(s.customers)} customers, ${fmtNum(s.auto_merges)} merged automatically. Finished ${fmtDateTime(run.finished_at)} in ${s.seconds}s.` })),
    h("dl", { class: "tiles" }, TILES.map(([label, val, sub, better, fmt, key]) => h("div", { class: "tile" },
      h("dt", { text: label }), h("dd", { class: "tile-value", text: val(s) }), h("dd", { class: "tile-sub", text: sub(s) }),
      delta(s, before, key, better, fmt, run.previous?.run_id)))),
    run.changes.length ? h("p", { class: "hint" }, "Settings changed from the run before: ", run.changes.join("; ")) : null);
}

function stateCell(st, review) {
  return h("div", { class: "tune-state" }, h("span", { text: STATE[st] || st }),
    review ? h("span", { class: "tag tag-warn", text: "in review" }) : null);
}

function changedPanel(run, onFind) {
  if (!run.previous) return null;
  const groups = GROUPS.filter(([k]) => run.changed?.[k]);
  return h("section", { class: "panel", "aria-labelledby": "tune-changed" },
    h("div", { class: "panel-head" }, h("h2", { id: "tune-changed", text: `What changed since run ${run.previous.run_id}` }),
      h("span", { class: "hint", text: "Per person, against the known answer" })),
    groups.length ? null : h("p", { class: "panel-empty", text: "Every person came out the same as in the run before." }),
    groups.map(([k, label, tone]) => {
      const g = run.changed[k];
      const body = h("div", { class: "check-examples", hidden: true });
      const btn = h("button", { type: "button", class: "btn btn-sm", "aria-expanded": "false", text: "Show", onclick: () => {
        const open = body.hidden;
        if (open && !body.firstChild) {
          body.append(h("div", { class: "table-scroll" }, h("table", { class: "check-table" },
            h("thead", {}, h("tr", {}, ["Source record", "Before", "Now", "Customer now"].map((t) => h("th", { scope: "col", text: t })))),
            h("tbody", {}, g.examples.map((e) => h("tr", {},
              h("td", {}, h("button", { type: "button", class: "link mono", text: `${e.record[0]}/${e.record[1]}`,
                title: "Follow this person across runs", onclick: () => onFind(e.record[1]) })),
              h("td", {}, stateCell(e.before, e.review_before)),
              h("td", {}, stateCell(e.after, e.review_after)),
              h("td", {}, e.customer_id ? h("a", { href: `#/customer/${e.customer_id}`, text: "Open" }) : h("span", { class: "muted", text: "—" }))))))));
          if (g.count > g.examples.length) body.append(h("p", { class: "hint", text: `The first ${g.examples.length} of ${fmtNum(g.count)}.` }));
        }
        body.hidden = !open;
        btn.textContent = open ? "Hide" : "Show";
        btn.setAttribute("aria-expanded", String(open));
      } });
      return h("div", { class: "tune-group" },
        h("div", { class: "check-head" },
          h("h3", { text: label }),
          h("div", { class: "check-count" }, h("b", { class: tone, text: fmtNum(g.count) }), btn)),
        body);
    }));
}

function findPanel(onReady) {
  const input = h("input", { class: "input", id: "tune-find", type: "text", placeholder: "LOY-0000548 or LOYALTY/LOY-0000548", autocomplete: "off" });
  const out = h("div", { class: "tune-found", "aria-live": "polite" });
  async function find(q) {
    input.value = q;
    out.replaceChildren(loading("Finding"));
    try {
      const f = await api("/tuning/find", { query: { record: q } });
      out.replaceChildren(
        h("p", {}, h("b", { class: "mono", text: `${f.source_system}/${f.source_record_id}` }), " ",
          f.customer_id ? h("a", { href: `#/customer/${f.customer_id}`, text: "Open its customer" }) : "is not loaded yet."),
        f.runs.length ? h("ol", { class: "tune-history" }, f.runs.map((r) => h("li", { class: r.state === "correct" ? "good" : "bad" },
          h("span", { class: "hint", text: `Run ${r.run_id}${r.note ? ` · ${r.note}` : ""}` }),
          stateCell(r.state, r.review)))) : null);
    } catch (ex) { out.replaceChildren(h("p", { class: "hint", text: ex.message })); }
  }
  onReady(find);
  return h("section", { class: "panel pad", "aria-labelledby": "tune-find-h" },
    h("h2", { id: "tune-find-h", text: "Follow a record" }),
    h("p", { class: "hint", text: "Customer IDs change on each run; source record IDs don't. Enter one from a case to see how that person came out in every run." }),
    h("form", { class: "tune-find-form", onsubmit: (e) => { e.preventDefault(); if (input.value.trim()) find(input.value.trim()); } },
      h("label", { class: "sr-only", for: "tune-find", text: "Source record ID" }), input,
      h("button", { type: "submit", class: "btn", text: "Find" })),
    out);
}

function settingsPanel(page, stats, onRun) {
  const saved = page.config;
  const draft = clone(saved);
  const cat = page.catalogue;
  const count = h("span", { class: "hint", "aria-live": "polite" });
  const rows = new Map();

  function refresh() {
    let n = 0;
    for (const r of cat.rules) {
      const a = draft.rules[r.key], b = saved.rules[r.key];
      const diff = a.mode !== b.mode || a.dob_mode !== b.dob_mode || [...a.vetoes].sort().join() !== [...b.vetoes].sort().join();
      rows.get(r.key).row.classList.toggle("changed", diff);
      n += diff;
    }
    for (const x of cat.numbers) n += draft[x.key] !== saved[x.key];
    count.textContent = n ? `${plural(n, "change")} from the settings in force` : "No changes from the settings in force";
    runBtn.disabled = false;
  }

  function syncRow(key) {
    const { mode, dob, boxes } = rows.get(key);
    const rc = draft.rules[key];
    mode.value = rc.mode;
    dob.value = rc.dob_mode;
    for (const [v, box] of boxes) box.checked = rc.vetoes.includes(v);
  }

  function ruleRow(r) {
    const rc = draft.rules[r.key];
    const st = stats?.[r.key] || {};
    const mode = h("select", { class: "input input-sm", "aria-label": `${r.key}: what a match does`, onchange: () => { rc.mode = mode.value; refresh(); } },
      MODES.map(([v, label]) => h("option", { value: v, text: label })));
    const dob = h("select", { class: "input input-sm", "aria-label": `${r.key}: dates of birth`, onchange: () => { rc.dob_mode = dob.value; refresh(); } },
      h("option", { value: "exact", text: "Exact" }), h("option", { value: "lenient", text: "Lenient" }));
    const boxes = new Map();
    const vetoes = h("div", { class: "veto-chips", role: "group", "aria-label": `${r.key}: vetoes` }, cat.vetoes.map((v) => {
      const box = h("input", { type: "checkbox", onchange: () => {
        rc.vetoes = box.checked ? [...rc.vetoes, v.key] : rc.vetoes.filter((x) => x !== v.key);
        refresh();
      } });
      boxes.set(v.key, box);
      return h("label", { class: "veto-chip", title: v.label }, box, v.key.replace(/_/g, " "));
    }));
    const row = h("tr", {},
      h("td", {}, h("div", { class: "mono rule-key", text: r.key }), h("div", { class: "hint", text: r.description })),
      h("td", {}, mode), h("td", {}, vetoes), h("td", {}, dob),
      h("td", { class: "num" }, st.links ? `${fmtNum(st.links)}` : "—",
        st.wrong ? h("div", { class: "bad small", text: `${fmtNum(st.wrong)} wrong` }) : null),
      h("td", { class: "num" }, st.cases ? `${fmtNum(st.cases)}` : "—",
        st.cases_same ? h("div", { class: "bad small", text: `${fmtNum(st.cases_same)} avoidable` }) : null));
    rows.set(r.key, { row, mode, dob, boxes });
    syncRow(r.key);
    return row;
  }

  function setTier(tier, m) {
    for (const r of cat.rules) if (r.tier === tier) { draft.rules[r.key].mode = m; syncRow(r.key); }
    refresh();
  }

  const numbers = cat.numbers.map((x) => {
    const id = `tune-${x.key}`;
    const input = h("input", { class: "input", id, type: "number", min: x.min, max: x.max, step: 1, value: draft[x.key],
      oninput: () => { const v = Number(input.value); if (Number.isInteger(v)) draft[x.key] = v; refresh(); } });
    return h("div", { class: "field" }, h("label", { for: id, text: x.label }), input);
  });

  const note = h("input", { class: "input", id: "tune-note", type: "text", maxlength: 300, placeholder: "e.g. Name + postcode to review" });
  const runBtn = h("button", { type: "button", class: "btn btn-primary", text: "Re-run with these settings", onclick: async () => {
    runBtn.disabled = true;
    try { await onRun(draft, note.value.trim()); } catch (ex) { toast(ex.message); runBtn.disabled = false; }
  } });
  const reset = h("button", { type: "button", class: "btn", text: "Defaults", title: "The rules as written in code, every one linking", onclick: () => {
    for (const r of cat.rules) Object.assign(draft.rules[r.key], { mode: "auto", vetoes: [...r.default_vetoes], dob_mode: r.default_dob_mode });
    cat.rules.forEach((r) => syncRow(r.key));
    refresh();
  } });
  const undo = h("button", { type: "button", class: "btn", text: "Undo changes", onclick: () => {
    Object.assign(draft, clone(saved));
    cat.rules.forEach((r) => syncRow(r.key));
    numbers.forEach((f, i) => { f.querySelector("input").value = saved[cat.numbers[i].key]; });
    refresh();
  } });

  const table = h("div", { class: "table-scroll" }, h("table", { class: "tune-rules" },
    h("thead", {}, h("tr", {}, ["Rule", "Match", "Vetoes: keep apart when…", "DOB", "Links", "Cases"].map((t, i) =>
      h("th", { scope: "col", class: i > 3 ? "num" : null, text: t })))),
    TIERS.map((tier) => h("tbody", {},
      h("tr", { class: "tier-row" }, h("th", { scope: "rowgroup", colspan: 6 },
        h("div", { class: "tier-head" }, h("span", { class: `tag tag-tier tag-${tier}`, text: tier }),
          h("span", { class: "hint", text: "Set all:" }),
          MODES.map(([m, label]) => h("button", { type: "button", class: "btn btn-xs", text: label, onclick: () => setTier(tier, m) }))))),
      cat.rules.filter((r) => r.tier === tier).map(ruleRow)))));

  const panel = h("section", { class: "panel", "aria-labelledby": "tune-settings" },
    h("div", { class: "panel-head" }, h("h2", { id: "tune-settings", text: "Engine settings" }), count),
    h("p", { class: "tune-intro hint" }, "Rules are tried strongest first; a record links to the customer whose records fire the strongest rule. ",
      h("b", { text: "Links" }), " links automatically, ", h("b", { text: "Review" }), " raises a case instead, ", h("b", { text: "Off" }),
      " never fires. Vetoes stop a rule between two records that differ in that way. Links and cases are from the last run."),
    table,
    h("div", { class: "tune-numbers" }, numbers),
    h("details", { class: "tune-blocking" }, h("summary", { text: "Blocking keys (read only)" }),
      h("p", { class: "hint", text: "Only records sharing one of these with the incoming record are compared at all." }),
      h("ul", {}, cat.blocking_keys.map((b) => h("li", {}, h("span", { class: "mono", text: b.key }), ` ${b.description}`)))),
    h("div", { class: "tune-run" },
      h("div", { class: "field" }, h("label", { for: "tune-note", text: "Note for this run (optional)" }), note),
      h("p", { class: "hint", text: "A re-run replaces all demo customers and cases, review decisions included, and takes a minute or two." }),
      h("div", { class: "tune-actions" }, runBtn, undo, reset)));
  refresh();
  return panel;
}

function runsTable(runs, current, onView) {
  return h("section", { class: "panel", "aria-labelledby": "tune-runs" },
    h("div", { class: "panel-head" }, h("h2", { id: "tune-runs", text: "Runs" })),
    h("div", { class: "table-scroll" }, h("table", { class: "tune-runs" },
      h("thead", {}, h("tr", {}, ["Run", "Settings", "Precision", "Recall", "Correct people", "Cases", ""].map((t, i) =>
        h("th", { scope: "col", class: i >= 2 && i <= 5 ? "num" : null, text: t })))),
      h("tbody", {}, runs.map((r) => {
        const s = r.scores;
        return h("tr", { class: r.run_id === current ? "current" : null },
          h("td", {}, h("b", { text: `#${r.run_id}` }), h("div", { class: "hint", text: r.finished_at ? fmtDateTime(r.finished_at) : "running" }),
            r.started_by ? h("div", { class: "hint", text: r.started_by }) : null),
          h("td", { class: "notes" }, r.note ? h("div", { text: r.note }) : null,
            r.changes.length ? h("div", { class: "hint", text: r.changes.join("; ") }) : h("div", { class: "hint", text: "Same settings as the run before" }),
            r.status === "failed" ? h("div", { class: "bad small", text: r.error }) : null),
          h("td", { class: "num", text: s ? pct(s.precision) : "—" }),
          h("td", { class: "num", text: s ? pct(s.recall) : "—" }),
          h("td", { class: "num", text: s ? fmtNum(s.people_correct) : "—" }),
          h("td", { class: "num" }, s ? fmtNum(s.open_cases) : "—", s?.cases_same ? h("div", { class: "hint", text: `${fmtNum(s.cases_same)} avoidable` }) : null),
          h("td", {}, s && r.run_id !== current ? h("button", { type: "button", class: "btn btn-sm", text: "View", onclick: () => onView(r.run_id) }) : null));
      })))));
}

function progressPanel(run) {
  const p = run.progress || {};
  const frac = p.total ? p.done / p.total : 0;
  const bar = h("div", { class: "tune-bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(100 * frac) },
    h("span", { style: `width: ${Math.max(2, 100 * frac).toFixed(1)}%` }));
  return h("div", { class: "callout tune-progress" },
    h("div", {}, h("b", { text: `Run ${run.run_id} in progress${run.note ? `: ${run.note}` : ""}` }),
      h("p", { text: p.total ? `${fmtNum(p.done)} of ${fmtNum(p.total)} records resolved` : "Clearing the demo data" }), bar));
}

export async function viewTuning(runId) {
  setNav("tuning");
  if (poll) { clearInterval(poll); poll = null; }
  show(loading("Loading tuning"));
  const page = await api("/tuning");
  const running = page.runs.find((r) => r.status === "running");
  const latest = page.runs.find((r) => r.status === "done");
  const shown = runId || latest?.run_id;
  const run = shown ? await api(`/tuning/runs/${shown}`) : null;
  const stats = latest ? (shown === latest.run_id ? run.rules : (await api(`/tuning/runs/${latest.run_id}`)).rules) : null;
  let find = () => {};

  const head = h("div", { class: "page-head" }, h("div", {},
    h("h1", { text: "Tuning" }),
    h("p", { class: "sub", text: "Change how the engine decides, re-run the same demo records through it, and score the result against the known answer for every record." })));
  let progress = running ? progressPanel(running) : null;
  show(h("div", { class: "tune-page" }, head, progress,
    run ? results(run) : h("p", { class: "callout", text: "No runs yet. Re-run the demo below to score it." }),
    run && shown !== latest?.run_id ? h("p", { class: "callout callout-warn" }, `Showing run ${shown}. The data in the app is from run ${latest.run_id}. `,
      h("button", { type: "button", class: "link", text: "Show the latest run", onclick: () => viewTuning() })) : null,
    h("div", { class: "tune-grid" },
      run ? changedPanel(run, (q) => { find(q); document.getElementById("tune-find")?.scrollIntoView({ block: "center" }); }) : null,
      findPanel((f) => { find = f; })),
    running ? null : settingsPanel(page, stats, async (config, note) => {
      await api("/tuning/runs", { method: "POST", body: { config, note: note || null } });
      viewTuning();
    }),
    runsTable(page.runs, shown, (id) => viewTuning(id))));

  if (running) {
    poll = setInterval(async () => {
      if (!location.hash.startsWith("#/tuning")) { clearInterval(poll); poll = null; return; }
      try {
        const now = await api("/tuning");
        const r = now.runs.find((x) => x.run_id === running.run_id);
        if (r?.status === "running") { const next = progressPanel(r); progress.replaceWith(next); progress = next; }
        else { clearInterval(poll); poll = null; if (r?.status === "failed") toast(`Run ${r.run_id} failed: ${r.error}`); viewTuning(); }
      } catch { /* try again next tick */ }
    }, 2000);
  }
}
