// The review queue.

import { api, fmtAge, fmtAgo, fmtDateTime, fmtNum, h, loading, show, state, toast } from "./core.js";
import { setNav } from "./shell.js";

export async function takeNext() {
  try {
    const { case_id } = await api("/cases/next", { method: "POST" });
    if (case_id === null) {
      toast("No cases are waiting.");
      if (location.hash !== "#/queue") location.hash = "#/queue";
      else window.dispatchEvent(new HashChangeEvent("hashchange"));
      return;
    }
    location.hash = `#/case/${case_id}`;
  } catch (ex) { toast(ex.message); }
}

export function decisionTag(d) {
  return h("span", { class: `tag tag-${d}`, text: d === "merge" ? "Merged" : "Kept apart" });
}

function tile(label, value, sub) {
  return h("div", { class: "tile" },
    h("dt", { text: label }), h("dd", { class: "tile-value", text: value }),
    sub ? h("dd", { class: "tile-sub", text: sub }) : null);
}

const TIERS = ["exact", "strong", "probable", "possible"];

function tierBreakdown(byTier) {
  const total = TIERS.reduce((a, t) => a + (byTier[t] || 0), 0);
  const max = Math.max(1, ...TIERS.map((t) => byTier[t] || 0));
  return h("section", { class: "panel", "aria-labelledby": "tiers-h" },
    h("div", { class: "panel-head" }, h("h2", { id: "tiers-h", text: "Outstanding by match tier" })),
    total === 0
      ? h("p", { class: "panel-empty", text: "Nothing outstanding." })
      : h("table", { class: "tiers" },
        h("thead", { class: "sr-only" }, h("tr", {}, h("th", { text: "Tier" }), h("th", { text: "Cases" }), h("th", { text: "Share" }))),
        h("tbody", {}, TIERS.map((t) => {
          const n = byTier[t] || 0;
          return h("tr", { title: `${fmtNum(n)} ${t} (${Math.round((100 * n) / total)}%)` },
            h("th", { scope: "row", text: t[0].toUpperCase() + t.slice(1) }),
            h("td", { class: "tier-n", text: fmtNum(n) }),
            h("td", { class: "tier-bar" }, h("span", { style: `width:${(100 * n) / max}%`, hidden: n === 0 })));
        }))));
}

function queueTable(rows, total) {
  const head = h("div", { class: "panel-head" },
    h("h2", { id: "next-h", text: total > rows.length ? `Next ${rows.length} of ${fmtNum(total)}` : "Outstanding cases" }),
    h("span", { class: "hint", text: "In the order Take next serves them" }));
  if (!rows.length) {
    return h("section", { class: "panel", "aria-labelledby": "next-h" }, head,
      h("p", { class: "panel-empty", text: "New cases appear when an incoming record matches two customers equally well, or when a match falls in a tier that is set to go to review." }));
  }
  return h("section", { class: "panel", "aria-labelledby": "next-h" }, head,
    h("div", { class: "table-scroll" }, h("table", { class: "queue-table" },
      h("thead", {}, h("tr", {}, ["Case", "Customers", "Matched on", "Tier", "Waiting", "Status"].map((t) => h("th", { scope: "col", text: t })))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", { class: "num" }, h("a", { href: `#/case/${r.case_id}`, text: r.case_id })),
        h("td", { class: "names" }, h("div", {}, h("span", { text: r.names[0] || "Unnamed" }), h("span", { class: "vs", text: "and" }), h("span", { text: r.names[1] || "Unnamed" }))),
        h("td", { class: "muted wrap", text: r.rule_description }),
        h("td", {}, r.tier ? h("span", { class: `tag tag-tier tag-${r.tier}`, text: r.tier }) : null),
        h("td", { class: "muted", title: fmtDateTime(r.created_at), text: fmtAge(r.created_at) }),
        h("td", { class: "muted" }, r.held_by
          ? h("span", { class: "held", text: r.held_by === state.user.display_name ? "Held by you" : `Held by ${r.held_by}` })
          : "Waiting")))))));
}

export async function viewQueue() {
  setNav("queue");
  show(loading("Loading the queue"));
  const [st, ov, recent] = await Promise.all([api("/stats"), api("/overview"), api("/history")]);
  const outstanding = ov.waiting + ov.in_progress;
  const complete = ov.merged + ov.kept_apart;

  const action = st.mine > 0
    ? h("button", { class: "btn btn-primary btn-lg", onclick: takeNext, text: "Continue your case" })
    : h("button", { class: "btn btn-primary btn-lg", onclick: takeNext, disabled: ov.waiting === 0, text: "Take the next case" });

  const head = h("div", { class: "page-head" },
    h("div", {},
      h("h1", { text: "Review queue" }),
      h("p", { class: "sub", text: outstanding === 0 ? "No cases are waiting."
        : `${fmtNum(outstanding)} ${outstanding === 1 ? "case" : "cases"} outstanding, the oldest raised ${fmtAgo(ov.oldest_open_at)}. A case is held for you while you work on it.` })),
    action);

  const tiles = h("dl", { class: "tiles" },
    tile("Waiting", fmtNum(ov.waiting), "Not yet taken"),
    tile("In progress", fmtNum(ov.in_progress), st.mine ? `${fmtNum(st.mine)} held by you` : "Held by a reviewer"),
    tile("Complete", fmtNum(complete), `${fmtNum(ov.merged)} merged, ${fmtNum(ov.kept_apart)} kept apart`),
    tile("Decided today", fmtNum(st.team_today), `${fmtNum(st.decided_today)} by you`),
    ov.resolved_elsewhere ? tile("Resolved elsewhere", fmtNum(ov.resolved_elsewhere), "Merged outside review") : null);

  const items = recent.slice(0, 6).map((r) =>
    h("li", {}, h("a", { href: `#/case/${r.case_id}` },
      decisionTag(r.decision), h("span", { text: `Case ${r.case_id}` }), h("span", { class: "when", text: fmtDateTime(r.decided_at) }),
      h("span", { class: "note", text: r.notes }))));
  const recentPanel = h("section", { class: "panel recent", "aria-labelledby": "recent-h" },
    h("div", { class: "panel-head" }, h("h2", { id: "recent-h", text: "Your recent decisions" })),
    items.length ? h("ol", {}, items) : h("p", { class: "panel-empty", text: "Decisions you make will be listed here." }));

  show(head, tiles, h("div", { class: "queue" },
    queueTable(ov.queue, outstanding),
    h("div", { class: "queue-side" }, tierBreakdown(ov.open_by_tier), recentPanel)));
}
