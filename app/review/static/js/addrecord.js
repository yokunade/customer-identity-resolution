// Add a customer record by hand (managers): fill in, check what custIDR would do, then save.

import { ICONS, api, fmtDob, h, hashParams, icon, plural, shortId, show, toast } from "./core.js";
import { cleansedLines } from "./customer.js";
import { setNav } from "./shell.js";

const MAX_CONTACTS = 5;
const TITLES = ["", "Mr", "Mrs", "Miss", "Ms", "Mx", "Dr"];
const GENDERS = [["", "Not given"], ["F", "Female"], ["M", "Male"], ["X", "Other"]];

/** A random v4 UUID; crypto.randomUUID needs HTTPS, getRandomValues does not. */
function newToken() {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const x = [...b].map((n) => n.toString(16).padStart(2, "0")).join("");
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
}

function field(id, label, input, hint) {
  return h("div", { class: "field" }, h("label", { for: id }, label), input,
    hint ? h("span", { class: "hint", text: hint }) : null);
}

const text = (id, attrs = {}) => h("input", { id, class: "input", type: "text", autocomplete: "off", maxlength: 200, ...attrs });

/** One input per value, with "Add another" until MAX_CONTACTS. */
function multi(name, label, attrs, first = "") {
  const list = h("div", { class: "multi" });
  const add = h("button", { type: "button", class: "link", text: `Add another ${label.toLowerCase()}`,
    onclick: () => { addRow(""); list.lastElementChild.querySelector("input").focus(); } });
  function addRow(value) {
    const n = list.children.length + 1;
    const input = h("input", { class: "input", name, value, autocomplete: "off", ...attrs,
      "aria-label": n === 1 ? label : `${label} ${n}` });
    list.append(h("div", { class: "multi-row" }, input));
    add.hidden = list.children.length >= MAX_CONTACTS;
  }
  addRow(first);
  list.firstElementChild.querySelector("input").id = name;
  return { el: h("div", { class: "field" }, h("label", { for: name, text: label }), list, add),
    values: () => [...list.querySelectorAll("input")].map((i) => i.value.trim()).filter(Boolean) };
}

function customerCard(c) {
  return h("a", { class: "pick", href: `#/customer/${c.customer_id}`, target: "_blank", rel: "noopener" },
    h("b", { text: c.name || "Unnamed" }),
    h("span", { class: "muted small" }, [c.date_of_birth ? `Born ${fmtDob(c.date_of_birth)}` : "", c.postcode || "",
      plural(c.record_count, "record")].filter(Boolean).join(" · ")),
    h("span", { class: "mono small muted", text: `${shortId(c.customer_id)} · ${c.sources.join(", ")}` }));
}

function outcome(res) {
  const how = res.rule_description ? ` on ${res.rule_description}${res.tier ? ` (${res.tier})` : ""}` : "";
  const parts = [];
  if (res.outcome === "matched") {
    parts.push(h("div", { class: "callout callout-ok" }, icon(ICONS.pair, 22),
      h("div", {}, h("b", { text: "Joins an existing customer" }), h("p", { text: `Matched${how}.` }),
        customerCard(res.customer))));
  } else {
    parts.push(h("div", { class: "callout" }, icon(ICONS.search, 22),
      h("div", {}, h("b", { text: "Creates a new customer" }),
        h("p", { text: res.review_with.length ? "No customer matches strongly enough to join automatically."
          : "No existing customer matches these details." }))));
  }
  if (res.merges_with?.length) {
    parts.push(h("div", { class: "callout callout-ok" }, icon(ICONS.pair, 22),
      h("div", {}, h("b", { text: `Also merges ${plural(res.merges_with.length, "customer")} into it` }),
        h("p", { text: "These details match them too, and nothing in their records conflicts, so they are the same person." }),
        res.merges_with.map(customerCard))));
  }
  if (res.review_with.length) {
    parts.push(h("div", { class: "callout callout-warn" }, icon(ICONS.warn, 22),
      h("div", {}, h("b", { text: `A review case will be raised with ${plural(res.review_with.length, "customer")}` }),
        h("p", { text: `A reviewer will decide whether they are the same person${res.outcome === "matched" ? "" : how}.` }),
        res.review_with.map(customerCard))));
  }
  if (res.shared_identifiers.length) {
    parts.push(h("p", { class: "hint", text: `Not used for matching, because many customers hold it: ${res.shared_identifiers.join(", ")}.` }));
  }
  return parts;
}

export function viewAddRecord() {
  setNav("add");
  const p = hashParams();
  const token = newToken();
  let checked = false;

  const title = h("select", { id: "title", class: "input" }, TITLES.map((t) => h("option", { value: t, text: t || "None" })));
  const first = text("first_name", { autocomplete: "off" });
  const middle = text("middle_names");
  const last = text("last_name", { required: true });
  const dob = text("date_of_birth", { maxlength: 40, inputmode: "numeric", placeholder: "DD/MM/YYYY" });
  const gender = h("select", { id: "gender", class: "input" }, GENDERS.map(([v, t]) => h("option", { value: v, text: t })));
  const emails = multi("email", "Email", { type: "email", maxlength: 254, placeholder: "name@example.co.uk" }, p.get("email") || "");
  const phones = multi("phone", "Phone", { type: "tel", maxlength: 50, placeholder: "07700 900123" }, p.get("phone") || "");
  const line1 = text("line1"), line2 = text("line2"), town = text("town"), county = text("county");
  const postcode = text("postcode", { maxlength: 12, value: p.get("postcode") || "", class: "input input-postcode" });
  const note = h("textarea", { id: "note", maxlength: 500, rows: 3 });
  const err = h("p", { class: "error", role: "alert", hidden: true });
  const checkBtn = h("button", { type: "submit", class: "btn btn-primary btn-lg", text: "Check" });

  const body = () => ({
    title: title.value, first_name: first.value, middle_names: middle.value, last_name: last.value,
    date_of_birth: dob.value, gender: gender.value, emails: emails.values(), phones: phones.values(),
    address: { line1: line1.value, line2: line2.value, town: town.value, county: county.value, postcode: postcode.value },
    note: note.value,
  });

  const result = h("div", { class: "add-result-body" },
    h("p", { class: "hint", text: "Check shows what saving would do: the cleaned-up values, and whether the record joins an existing customer or creates a new one. Nothing is saved until you confirm." }));

  const form = h("form", { class: "add-form", "aria-labelledby": "add-h", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    err.hidden = true;
    checkBtn.disabled = true;
    try {
      const res = await api("/records/check", { method: "POST", body: body() });
      checked = true;
      showCheck(res);
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    } finally {
      checkBtn.disabled = false;
    }
  }, oninput: (e) => {
    if (!checked || e.target === note) return;
    checked = false;
    result.replaceChildren(h("div", { class: "callout callout-warn" }, icon(ICONS.warn, 22),
      h("p", { text: "The details have changed since the check. Check again before saving." })));
    checkBtn.textContent = "Check";
  } },
  h("fieldset", { class: "panel pad" }, h("legend", { text: "Name" }),
    h("div", { class: "grid-name" }, field("title", "Title", title), field("first_name", "First name", first),
      field("middle_names", h("span", {}, "Middle names ", h("span", { class: "optional", text: "(optional)" })), middle),
      field("last_name", h("span", {}, "Last name ", h("span", { class: "req", text: "(required)" })), last))),
  h("fieldset", { class: "panel pad" }, h("legend", { text: "Date of birth and gender" }),
    h("div", { class: "grid-2" }, field("date_of_birth", "Date of birth", dob), field("gender", "Gender", gender))),
  h("fieldset", { class: "panel pad" }, h("legend", { text: "Contact" }),
    h("div", { class: "grid-2" }, emails.el, phones.el)),
  h("fieldset", { class: "panel pad" }, h("legend", { text: "Current address" }),
    h("div", { class: "grid-2" }, field("line1", "Address line 1", line1), field("line2", "Address line 2", line2),
      field("town", "Town or city", town), field("county", "County", county), field("postcode", "Postcode", postcode))),
  h("fieldset", { class: "panel pad" }, h("legend", { text: "Why are you adding this?" }),
    field("note", h("span", {}, "Note ", h("span", { class: "optional", text: "(optional)" })), note,
      "Kept with the record and shown on the customer's page.")),
  h("div", { class: "add-actions" }, checkBtn, err,
    h("p", { class: "hint", text: "Needs a last name and at least one of: date of birth, email, phone or postcode." })));

  function showCheck(res) {
    const save = h("button", { type: "button", class: "btn btn-primary btn-lg btn-block", text: "Save record",
      onclick: async () => {
        save.disabled = true;
        try {
          const r = await api("/records", { method: "POST", body: { ...body(), submit_token: token } });
          const cases = r.review_cases?.length ? ` Review ${plural(r.review_cases.length, "case")} raised.` : "";
          toast(r.outcome === "already_saved" ? `That record was already saved as MANUAL/${r.source_record_id}.`
            : `Added MANUAL/${r.source_record_id}.${cases}`);
          location.hash = `#/customer/${r.customer_id}`;
        } catch (ex) {
          toast(ex.message);
          save.disabled = false;
        }
      } });
    const lines = cleansedLines(res.cleansed);
    result.replaceChildren(...[
      ...outcome(res),
      h("div", { class: "rec-box" }, h("h4", { text: "Will be saved as" }),
        h("dl", {}, lines.map(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })]))),
      res.data_quality.length
        ? h("div", { class: "tags dq" }, res.data_quality.map((i) => h("span", { class: `tag tag-dq tag-dq-${i.severity}`,
          text: `${i.field}: ${i.code.replace(/_/g, " ")}` })))
        : null,
      save,
      h("p", { class: "hint", text: "Saved as source MANUAL with your name. It is matched like a record from any other system." }),
    ].filter(Boolean));
    checkBtn.textContent = "Check again";
    result.closest("aside").scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  show(
    h("div", { class: "page-head" }, h("div", {},
      h("h1", { id: "add-h", text: "Add a customer record" }),
      h("p", { class: "sub", text: "For details that reached you directly, such as on a call. They are cleaned up and matched exactly like a record from a source system." }))),
    h("div", { class: "add" }, form,
      h("aside", { class: "panel pad add-result", "aria-live": "polite", "aria-labelledby": "res-h" },
        h("h2", { id: "res-h", text: "What saving will do" }), result)));
  (first.value ? last : first).focus();
}
