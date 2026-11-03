// The top bar: navigation, the quick search box, who is signed in.

import { $, api, fmtNum, state } from "./core.js";
import { themeButton } from "./theme.js";

const ROLE = { reviewer: "Reviewer", manager: "Manager", admin: "Admin" };

export function setNav(active) {
  const u = state.user;
  $("#bar").hidden = !u;
  document.body.classList.toggle("signed-out", !u);
  if (!u) return;
  $("#who-name").textContent = u.display_name;
  $("#who-role").textContent = ROLE[u.role] || u.role;
  $("#who-initials").textContent = u.display_name.split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  $("#nav-lookup").hidden = !u.can_lookup;
  $("#nav-add").hidden = !u.can_lookup;
  $("#quick").hidden = !u.can_lookup || active === "lookup";
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  refreshBadge();
}

export async function refreshBadge() {
  const badge = $("#queue-badge");
  try {
    const st = await api("/stats");
    badge.textContent = fmtNum(st.open);
    badge.hidden = !st.open;
    badge.setAttribute("aria-label", `${fmtNum(st.open)} open cases`);
  } catch { badge.hidden = true; }
}

export function initShell() {
  $("#theme-slot").replaceWith(themeButton());
  $("#signout").addEventListener("click", async () => {
    try { await api("/logout", { method: "POST" }); } catch { /* already signed out */ }
    state.user = null;
    location.hash = "#/login";
  });
  $("#quick").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#quick-q").value.trim();
    if (!q) return;
    $("#quick-q").value = "";
    location.hash = "#/search?" + new URLSearchParams({ q });
  });
}
