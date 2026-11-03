// Light, dark, or follow the system. The choice is kept in this browser only.

import { h, s } from "./core.js";

const KEY = "custidr-theme";
const ORDER = ["system", "light", "dark"];
const LABEL = { system: "Theme: match system", light: "Theme: light", dark: "Theme: dark" };
const ICON = {
  system: ["M3 4h14v9H3z", "M7 16.5h6", "M10 13v3.5"],
  light: ["M10 6.5a3.5 3.5 0 1 1 0 7a3.5 3.5 0 0 1 0-7z", "M10 2v1.5", "M10 16.5V18", "M2 10h1.5", "M16.5 10H18",
    "M4.3 4.3l1.1 1.1", "M14.6 14.6l1.1 1.1", "M4.3 15.7l1.1-1.1", "M14.6 5.4l1.1-1.1"],
  dark: ["M16 12.5A6.5 6.5 0 0 1 7.5 4a6.5 6.5 0 1 0 8.5 8.5z"],
};

function current() {
  const t = document.documentElement.dataset.theme;
  return t === "light" || t === "dark" ? t : "system";
}

function apply(t) {
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  try {
    if (t === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, t);
  } catch { /* storage blocked: the choice lasts for this page only */ }
  document.querySelectorAll(".theme-btn").forEach(paint);
}

function paint(btn) {
  const t = current();
  const svg = s("svg", { viewBox: "0 0 20 20", "aria-hidden": "true" });
  ICON[t].forEach((d) => svg.append(s("path", { d })));
  btn.replaceChildren(svg);
  btn.title = `${LABEL[t]} (click to change)`;
  btn.setAttribute("aria-label", btn.title);
}

/** A button that cycles match system, light and dark. */
export function themeButton() {
  const btn = h("button", { type: "button", class: "theme-btn",
    onclick: () => apply(ORDER[(ORDER.indexOf(current()) + 1) % ORDER.length]) });
  paint(btn);
  return btn;
}
