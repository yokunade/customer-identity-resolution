// Shared helpers: DOM building, formatting, the API client and app state.

export const $ = (sel, root = document) => root.querySelector(sel);

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "style") el.style.cssText = v; // CSSOM, which the CSP allows; a style attribute it blocks
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const SVGNS = "http://www.w3.org/2000/svg";
export function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  kids.forEach((k) => el.append(k));
  return el;
}

/** A small stroke icon from a list of path strings. */
export function icon(paths, size = 18) {
  const svg = s("svg", { class: "icon", width: size, height: size, viewBox: "0 0 20 20", "aria-hidden": "true" });
  for (const d of paths) svg.append(s("path", { d }));
  return svg;
}
export const ICONS = {
  search: ["M8.5 3a5.5 5.5 0 1 1 0 11a5.5 5.5 0 0 1 0-11z", "M13 13l4.5 4.5"],
  warn: ["M10 3l8 14H2z", "M10 8.5v3.5", "M10 14.5v.5"],
  pair: ["M7.5 4.5a5.5 5.5 0 1 1 0 11a5.5 5.5 0 0 1 0-11z", "M12.5 4.5a5.5 5.5 0 1 1 0 11a5.5 5.5 0 0 1 0-11z"],
};

export function brandMark(cls = "brand-mark") {
  return s("svg", { class: cls, viewBox: "0 0 40 26", "aria-hidden": "true" },
    s("circle", { cx: 13, cy: 13, r: 10 }), s("circle", { cx: 27, cy: 13, r: 10 }));
}

export const fmtDateTime = (iso) =>
  iso ? new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(iso)) : "";
export const fmtDate = (iso) =>
  iso ? new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(new Date(iso)) : "";
export const fmtTime = (iso) =>
  iso ? new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(new Date(iso)) : "";
export const fmtDob = (d) => (d ? fmtDate(d + "T12:00:00") : "");
export const fmtNum = (n) => n.toLocaleString("en-GB");
export const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
export const shortId = (id) => (id ? id.slice(0, 8) : "");

export function fmtAge(iso) {
  if (!iso) return "";
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (mins < 60) return mins <= 1 ? "just now" : `${mins} min`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return plural(hrs, "hour");
  return plural(Math.round(hrs / 24), "day");
}

export function fmtAgo(iso) {
  const age = fmtAge(iso);
  return age === "just now" || !age ? age : `${age} ago`;
}

let toastTimer;
export function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 4500);
}

/* ------------------------------------------------------------------ state and API */

export const state = { user: null, reason: "" };

export async function api(path, { method = "GET", body, query } = {}) {
  let url = "/review/api" + path;
  if (query) {
    const qs = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== ""));
    if ([...qs].length) url += "?" + qs;
  }
  const res = await fetch(url, {
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
    const err = new Error(detail || `Request failed (${res.status}).`);
    err.status = res.status;
    throw err;
  }
  return data;
}

/** Replace the page content and move focus to it, as a page load would. */
export function show(...nodes) {
  const main = $("#main");
  main.replaceChildren(...nodes);
  main.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

export const loading = (text) => h("p", { class: "loading", text });

/** The query string of the current hash route, e.g. #/search?q=x. */
export const hashParams = () => new URLSearchParams(location.hash.split("?")[1] || "");
