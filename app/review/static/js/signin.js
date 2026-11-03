// Sign in: a brand panel and the email and password form.

import { api, brandMark, h, s, show, state } from "./core.js";
import { setNav } from "./shell.js";
import { themeButton } from "./theme.js";

const POINTS = [
  [["M8.5 3a5.5 5.5 0 1 1 0 11a5.5 5.5 0 0 1 0-11z", "M13 13l4.5 4.5"], "Search by email, phone, postcode or source ID"],
  [["M2 4h16v12H2z", "M2 8.5h16", "M8 8.5V16"], "A single view of each customer and their source records"],
  [["M4 10l4 4l8-9"], "A review queue for the matches that need a person"],
];

// Three source records, written differently, resolving to one customer.
const SOURCES = [
  ["CRM", "Mrs S. Jones", "sarah.jones@example.com"],
  ["Web shop", "Sarah Jones", "07700 900123"],
  ["Billing", "SARAH JONES", "12 Elm Road, LS6 2AB"],
];
const MERGED = [
  ["Email", "sarah.jones@example.com", "CRM"],
  ["Mobile", "07700 900123", "Web shop"],
  ["Address", "12 Elm Road, LS6 2AB", "Billing"],
];

function mergeGraphic() {
  const links = s("svg", { class: "merge-links", "aria-hidden": "true", viewBox: "0 0 100 100", preserveAspectRatio: "none" },
    ...[15, 50, 85].map((y) => s("path", { d: `M0 ${y} C 55 ${y}, 45 50, 100 50`, "vector-effect": "non-scaling-stroke" })));
  return h("figure", { class: "merge", "aria-label": "Three source records for Sarah Jones, written differently, combined into one customer record." },
    h("ol", { class: "merge-sources", "aria-hidden": "true" }, SOURCES.map(([src, name, detail]) =>
      h("li", {}, h("span", { class: "merge-src", text: src }), h("b", { text: name }), h("span", { class: "merge-detail", text: detail })))),
    links,
    h("div", { class: "merge-customer", "aria-hidden": "true" },
      h("div", { class: "merge-head" }, h("span", { class: "merge-avatar", text: "SJ" }),
        h("div", {}, h("b", { text: "Sarah Jones" }), h("span", { text: "1 customer · 3 source records" }))),
      h("dl", {}, MERGED.map(([k, v, src]) => [h("dt", { text: k }), h("dd", {}, v, h("span", { class: "merge-src", text: src }))]))));
}

export function viewSignIn() {
  state.user = null;
  setNav(null);
  const err = h("p", { class: "error", role: "alert", hidden: true });
  const email = h("input", { class: "input input-lg", id: "email", type: "email", autocomplete: "username", required: true, maxlength: 254 });
  const pw = h("input", { class: "input input-lg", id: "password", type: "password", autocomplete: "current-password", required: true, maxlength: 200 });
  const reveal = h("button", { type: "button", class: "reveal", "aria-controls": "password", "aria-pressed": "false", text: "Show",
    onclick: () => {
      const shown = pw.type === "text";
      pw.type = shown ? "password" : "text";
      reveal.textContent = shown ? "Show" : "Hide";
      reveal.setAttribute("aria-pressed", String(!shown));
    } });
  const btn = h("button", { class: "btn btn-primary btn-lg btn-block", type: "submit", text: "Sign in" });
  const form = h("form", {
    class: "signin-form", "aria-labelledby": "signin-h",
    onsubmit: async (e) => {
      e.preventDefault();
      err.hidden = true;
      btn.disabled = true;
      try {
        await api("/login", { method: "POST", body: { email: email.value, password: pw.value } });
        state.user = await api("/me");
        location.hash = "#/";
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
        btn.disabled = false;
        pw.select();
      }
    },
  },
    h("div", { class: "signin-title" },
      h("h2", { id: "signin-h", text: "Sign in" }),
      h("p", { text: "Use the email and password your administrator set up." })),
    h("div", { class: "field" }, h("label", { for: "email", text: "Email" }), email),
    h("div", { class: "field" }, h("label", { for: "password", text: "Password" }),
      h("div", { class: "pw-wrap" }, pw, reveal)),
    err, btn,
    h("p", { class: "notice", text: "Every customer lookup is recorded with your name. After 5 failed attempts, sign-in pauses for 5 minutes." }));

  show(h("div", { class: "signin" },
    h("section", { class: "signin-brand" },
      h("div", { class: "signin-logo" }, brandMark("signin-mark"), h("span", {}, "cust", h("b", { text: "IDR" }))),
      h("div", { class: "signin-pitch" },
        h("h1", {}, "One customer.", h("br"), "Every record."),
        h("p", { text: "Look up anyone by email or phone, see every source record behind them, and settle the matches the rules could not." }),
        h("ul", {}, POINTS.map(([d, t]) => {
          const svg = s("svg", { viewBox: "0 0 20 20", "aria-hidden": "true" });
          d.forEach((p) => svg.append(s("path", { d: p })));
          return h("li", {}, svg, t);
        }))),
      mergeGraphic(),
      h("p", { class: "signin-foot", text: "custIDR · open source, MIT licence" })),
    h("section", { class: "signin-side" }, themeButton(), form)));
  email.focus();
}
