// custIDR: routes between views. Managers land on lookup, everyone else on the queue.

import { api, state, toast } from "./core.js";
import { viewAddRecord } from "./addrecord.js";
import { viewCase } from "./case.js";
import { viewCustomer } from "./customer.js";
import { viewHistory } from "./history.js";
import { viewLookup, viewSearch } from "./lookup.js";
import { viewQueue } from "./queue.js";
import { initShell } from "./shell.js";
import { viewSignIn } from "./signin.js";

const LOOKUP_ONLY = /^#\/(lookup|search|customer|add)/;

async function route() {
  const hash = location.hash || "#/";
  if (hash === "#/login") return viewSignIn();
  if (!state.user) {
    try { state.user = await api("/me"); } catch { return; } // api() sends a 401 to #/login
  }
  if (hash === "#/" || hash === "#") {
    location.replace(state.user.can_lookup ? "#/lookup" : "#/queue");
    return;
  }
  if (LOOKUP_ONLY.test(hash) && !state.user.can_lookup) {
    toast("Customer lookup is for managers.");
    location.replace("#/queue");
    return;
  }
  try {
    let m;
    if (hash.startsWith("#/lookup")) return await viewLookup();
    if (hash.startsWith("#/search")) return await viewSearch();
    if (hash.startsWith("#/add")) return viewAddRecord();
    if ((m = hash.match(/^#\/customer\/([0-9a-f-]{36})/i))) return await viewCustomer(m[1]);
    if ((m = hash.match(/^#\/case\/(\d+)/))) return await viewCase(m[1]);
    if (hash.startsWith("#/history")) return await viewHistory();
    return await viewQueue();
  } catch (ex) {
    toast(ex.message);
  }
}

initShell();
window.addEventListener("hashchange", route);
route();
