"""Email address standardisation."""

from __future__ import annotations

import re

from app.schemas import CleansedEmail, Issue

_SYNTAX = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~.\-]+@[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)+$")

DOMAIN_TYPOS = {
    "gmial.com": "gmail.com", "gmal.com": "gmail.com", "gmail.con": "gmail.com", "gmai.com": "gmail.com",
    "gmail.co": "gmail.com", "gamil.com": "gmail.com", "gnail.com": "gmail.com", "gmail.cm": "gmail.com",
    "hotmial.com": "hotmail.com", "hotmal.com": "hotmail.com", "hotmail.con": "hotmail.com",
    "hotmial.co.uk": "hotmail.co.uk", "hotmail.co.k": "hotmail.co.uk", "hotmail.couk": "hotmail.co.uk",
    "yahoo.co.k": "yahoo.co.uk", "yahoo.couk": "yahoo.co.uk", "yaho.co.uk": "yahoo.co.uk",
    "yahoo.con": "yahoo.com", "outlok.com": "outlook.com", "outlook.con": "outlook.com",
    "btinternet.co": "btinternet.com", "btinterent.com": "btinternet.com", "btinternet.con": "btinternet.com",
    "live.co.k": "live.co.uk", "icloud.con": "icloud.com", "sky.con": "sky.com",
}
DOMAIN_ALIASES = {"googlemail.com": "gmail.com"}
# Providers where a "+tag" suffix is delivered to the base mailbox.
PLUS_ADDRESSING = {
    "gmail.com", "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "live.co.uk",
    "icloud.com", "me.com", "protonmail.com", "proton.me", "fastmail.com",
}
DOTS_IGNORED = {"gmail.com"}
PLACEHOLDER_DOMAINS = {
    "example.com", "example.co.uk", "example.org", "test.com", "test.co.uk", "none.com",
    "noemail.com", "no-email.com", "nomail.com", "email.com", "domain.com", "mailinator.com",
}
PLACEHOLDER_LOCALS = {
    "none", "noemail", "no.email", "noreply", "no-reply", "donotreply", "do-not-reply", "test",
    "na", "n.a", "unknown", "nobody", "null", "x", "xx", "xxx", "asdf", "abc",
}
ROLE_LOCALS = {
    "info", "admin", "sales", "enquiries", "enquiry", "office", "accounts", "contact", "hello",
    "support", "reception", "mail", "post", "team", "hr", "billing", "help", "service",
}


def cleanse_email(raw: str | None, field: str = "email") -> tuple[CleansedEmail | None, list[Issue]]:
    issues: list[Issue] = []
    if not raw or not str(raw).strip():
        return None, issues
    s = str(raw).strip().lower()
    s = re.sub(r"^mailto:", "", s)
    s = s.strip("<>\"' ;,")
    s = re.sub(r"\s+", "", s)
    s = s.replace(",", ".")
    s = re.sub(r"\.{2,}", ".", s)
    if not _SYNTAX.match(s) or s.count("@") != 1:
        issues.append(Issue(field=field, code="invalid_email", detail=str(raw)))
        return None, issues

    local, domain = s.split("@")
    local = local.strip(".")
    if domain in DOMAIN_TYPOS:
        issues.append(Issue(field=field, code="domain_typo_corrected", severity="info",
                            detail=f"{domain} -> {DOMAIN_TYPOS[domain]}"))
        domain = DOMAIN_TYPOS[domain]
    address = f"{local}@{domain}"

    canon_domain = DOMAIN_ALIASES.get(domain, domain)
    canon_local = local
    if canon_domain in PLUS_ADDRESSING:
        canon_local = canon_local.split("+", 1)[0]
    if canon_domain in DOTS_IGNORED:
        canon_local = canon_local.replace(".", "")

    usable = True
    if domain in PLACEHOLDER_DOMAINS or local in PLACEHOLDER_LOCALS:
        issues.append(Issue(field=field, code="placeholder_email", detail=address))
        usable = False
    elif local in ROLE_LOCALS:
        issues.append(Issue(field=field, code="role_account", severity="info", detail=address))
        usable = False

    return CleansedEmail(
        address=address, canonical=f"{canon_local}@{canon_domain}", domain=domain, usable_for_matching=usable
    ), issues
