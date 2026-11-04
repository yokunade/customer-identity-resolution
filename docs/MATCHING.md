# Matching design

## Pipeline

```
inbound record
  └─ cleanse (UK standards)                  app/cleansing/
       └─ extract components                 app/matching/keys.py
            ├─ match keys   (30 types, compared in memory, never stored)
            └─ blocking keys (7 types, HMAC'd and stored)
                 └─ advisory-lock every blocking key        app/service.py
                      └─ candidate pool  = records sharing a blocking key
                           └─ shared match keys per candidate, from its stored components
                                └─ rules, strongest first, with vetoes   app/matching/rules.py
                                     └─ link to winning customer, or create one
```

Everything is deterministic: the same inputs always give the same keys and the
same decision. There is no scoring or threshold tuning.

## Cleansing (UK standards)

| Field | What happens |
|---|---|
| Names | Titles, generation suffixes (Jr/Snr/III) and post-nominals (OBE, PhD…) separated, including a title or suffix typed into the forename ("Mr John", "John Jr") when the title and suffix fields are also sent. "Surname, Forename" handled. Surname particles (van, de, al…) kept with the surname. Accents folded. UK casing (O'Brien, McDonald, Smith-Jones). Placeholder names (TEST, CUSTOMER…) removed. Null tokens that are also real names (Na, Nil) are kept in name fields. |
| Forename forms | Own name plus formal forms from a UK nickname table (Bill→William, Liz→Elizabeth, Mohamed→Mohammed…). |
| Gender | From the field (M, F, or X for NB/non-binary/other), else inferred from title. An explicit X is never overridden by forename inference. |
| Date of birth | ISO, UK day-first (DD/MM/YYYY, D-M-YY), YYYYMMDD, "3rd April 1985". Ambiguous numeric dates are read day-first; unambiguous US dates are detected. Future, implausible (>120 years) and placeholder dates (1900-01-01, 1970-01-01…) are kept but excluded from keys. |
| Email | Lower-cased, typo domains fixed (gmial.com, hotmial.co.uk…), googlemail→gmail, Gmail dots removed, +tags removed for providers that support them. Placeholder and role accounts (info@, noemail@) excluded from keys. |
| Phone | Ofcom numbering plan: E.164 output, national display format, type (mobile, landline, non-geographic…). Restores lost leading zeros, strips extensions and "(0)". Ofcom drama ranges and junk numbers excluded from keys; non-personal ranges (03/08/09) and work landlines excluded. |
| Postcode | BS 7666 formats validated, formatted "SW1A 1AA", split into outward/inward/sector/district/area. Inward digit typos repaired (O→0). Postcodes typed into address lines are found. |
| Address | Royal Mail style: upper case, no punctuation, abbreviations expanded (RD→ROAD; ST→SAINT before a name, STREET at the end or after a street name when a direction or the town follows: MAIN ST NORTH, HIGH ST LEEDS), commas inside a line split it into lines, sub-building (FLAT 3, GROUND FLOOR FLAT), building number, building name, street, localities, post town. Country normalised (England/UK/GB → GB; Jersey/Guernsey/IoM kept). UPRN accepted. |

## Components

| Component | Source |
|---|---|
| `first_forms` | forename + nickname formal forms |
| `first_initial`, `last_key`, `last_parts` | letters-only name keys; `last_parts` only for multi-part surnames |
| `first_soundex`, `last_soundex`, `first_nysiis`, `last_nysiis` | phonetic codes |
| `dob`, `dob_unordered` | ISO date; day/month-order-insensitive form |
| `email`, `phone`, `mobile` | canonical email; E.164 numbers usable for matching |
| `postcode`, `district`, `uprn` | from current and previous addresses |
| `premise_street` | dwelling bound to street and post town, so a record without a postcode can still match |
| `premise_postcode`, `premise_district` | dwelling (sub-building + number or name) bound to its own postcode/district |

Previous addresses contribute components, so a record that lists an old
address still matches records held at that address.

## Match rules

| Priority | Rule | Tier | Components | Vetoes |
|---|---|---|---|---|
| 10 | `EMAIL_NAME_DOB` | exact | email + first_forms + last_key + dob | dob, generation |
| 20 | `PHONE_NAME_DOB` | exact | phone + first_forms + last_key + dob | dob, generation |
| 30 | `UPRN_NAME_DOB` | exact | uprn + first_forms + last_key + dob | dob, generation |
| 40 | `NAME_DOB_PREMISE_POSTCODE` | exact | first_forms + last_key + dob + premise_postcode | dob, generation |
| 45 | `NAME_DOB_PREMISE_STREET` | exact | first_forms + last_key + dob + premise_street | dob, generation, postcode |
| 50 | `NAME_DOB_POSTCODE` | exact | first_forms + last_key + dob + postcode | dob, generation |
| 52 | `EMAIL_MOBILE_LAST_DOB` | strong | email + mobile + last_key + dob | dob, first_name (letters), gender, generation, middle_initial |
| 55 | `EMAIL_MOBILE_FIRST` | strong | email + mobile + first_forms | dob (lenient), gender, generation, middle_initial |
| 60 | `EMAIL_NAME` | strong | email + first_forms + last_key | dob (lenient), gender, generation, middle_initial |
| 70 | `MOBILE_NAME` | strong | mobile + first_forms + last_key | dob (lenient), gender, generation, middle_initial |
| 100 | `INITIAL_DOB_PREMISE_POSTCODE` | strong | first_initial + last_key + dob + premise_postcode | dob, first_name, gender, generation |
| 110 | `SOUNDEX_NAME_DOB_POSTCODE` | strong | first_soundex + last_soundex + dob + postcode | dob, gender, generation |
| 120 | `NYSIIS_NAME_DOB_PREMISE_DISTRICT` | strong | first_nysiis + last_nysiis + dob + premise_district | dob, gender, generation |
| 125 | `EMAIL_FIRST` | strong | email + first_forms | dob (lenient), gender, generation, middle_initial |
| 126 | `MOBILE_FIRST` | strong | mobile + first_forms | dob (lenient), gender, generation, middle_initial |
| 127 | `EMAIL_LAST_INITIAL` | strong | email + last_key + first_initial | dob (lenient), first_name, gender, generation, middle_initial |
| 128 | `MOBILE_LAST_INITIAL` | strong | mobile + last_key + first_initial | dob (lenient), first_name, gender, generation, middle_initial |
| 130 | `NAME_DOBSWAP_POSTCODE` | strong | first_forms + last_key + dob_unordered + postcode | dob, gender, generation |
| 140 | `ALTSURNAME_DOB_PREMISE_POSTCODE` | strong | first_forms + last_parts + dob + premise_postcode | dob, gender, generation |
| 150 | `FIRST_DOB_PREMISE_POSTCODE` | probable | first_forms + dob + premise_postcode | dob, gender, generation |
| 160 | `FIRST_DOB_EMAIL` | probable | first_forms + dob + email | dob, gender, generation |
| 170 | `FIRST_DOB_MOBILE` | probable | first_forms + dob + mobile | dob, gender, generation |
| 180 | `EMAIL_DOB` | probable | email + dob | dob, first_name, gender, generation |
| 190 | `MOBILE_DOB` | probable | mobile + dob | dob, first_name, gender, generation |
| 200 | `UPRN_NAME` | possible | uprn + first_forms + last_key | dob, gender, generation, middle_initial |
| 210 | `NAME_PREMISE_POSTCODE` | possible | first_forms + last_key + premise_postcode | dob, gender, generation, middle_initial |
| 220 | `NAME_PREMISE_STREET` | possible | first_forms + last_key + premise_street | dob, gender, generation, middle_initial, postcode |
| 230 | `NAME_LASTSOUNDEX_PREMISE_POSTCODE` | possible | first_forms + last_soundex + premise_postcode | dob, gender, generation, middle_initial |
| 240 | `NAME_POSTCODE` | possible | first_forms + last_key + postcode | dob, gender, generation, middle_initial |
| 250 | `INITIAL_LAST_PREMISE_POSTCODE` | possible | first_initial + last_key + premise_postcode | dob, first_name, gender, generation, middle_initial |

A veto applies only when **both** records hold the attribute and they differ.
DOBs never conflict when day and month are transposed. Under `dob (lenient)`,
used only where an email or mobile and a name already agree, a DOB with a
single wrong field (e.g. the year keyed one off) is also tolerated. Forenames
conflict only if they share no nickname form and are more than one keystroke
apart (two for names of 8+ letters). Sounding alike is not enough: John, Jenny,
Jean and Jim share a Soundex code. `first_name (letters)` also accepts the same
letters in another order with the same initial (Yime/Yemi), and is used only
where email, mobile, surname and DOB all agree; twins registered on a parent's
email and mobile (Ella/Ellie, Jack/Jake) still conflict. Where a record has no gender or gendered
title, gender is inferred from a list of common UK forenames (unisex names
excluded) so the gender veto can still separate John and Jenny. `postcode`
vetoes a street-based match when both records have postcodes and none agree, so
183 Gladstone Road SW3 and SW20 stay apart.

### Auto-link tiers

Tiers rank exact, strong, probable, possible. The names describe the evidence
each rests on; measured precision on the 200k benchmark (round 5,
`docs/BENCHMARK.md`) is:

| Tier | Evidence | Links | Precision |
|---|---|---:|---:|
| exact | strong identifier or dwelling + full name + DOB | 45,572 | 100% |
| strong | email or mobile + name, or DOB + phonetic name + address | 49,229 | 99.99% |
| probable | DOB + forename, email or mobile; no surname needed | 585 | 100% |
| possible | name + address, no DOB or personal identifier | 9,446 | 99.06% |

`probable` scores 100% on few links; it ranks below `strong` because its
evidence is weaker, not because it was measured to be. Synthetic data
flatters every tier; see the caveats in `docs/BENCHMARK.md`.

`AUTO_LINK_TIERS` (default `exact,strong,probable,possible`) decides which tiers link
automatically. A match in a tier left out creates a new customer and is
written to `merge_candidate` for review, with `match.auto_linked = false` and
`match.suggested_customer_id` in the response. Set `AUTO_LINK_TIERS=exact,strong,probable`
when over-merging is costlier than duplicates: the `possible` tier (name +
address with no DOB or personal identifier) is where same-named relatives at
one address get confused. The 200k benchmark
(`docs/BENCHMARK.md`) quantifies the trade-off. A wrong link can be undone
with `POST /v1/customers/split` (or **Move records out** in custIDR), which
moves the records to a customer of their own and keeps the two apart.
Vetoes exist because UK households share addresses, landlines and sometimes
email, and fathers and sons share names.

### Decision

1. For each candidate record, the first (lowest priority number) rule whose
   key is shared and that is not vetoed is that record's result.
2. Each customer takes its best record result.
3. The customer with the best priority wins. Ties are broken by number of
   corroborating records, then oldest customer, then customer ID. Priorities
   follow the tiers (exact, strong, probable, possible), so a weaker tier never
   outranks a stronger one; an import-time check enforces this.
4. Other customers tied at the winning rule, and customers that matched lower
   down at a rule in an auto-linkable tier, are returned as
   `competing_customer_ids` and written to `merge_candidate` for review (each
   under the rule it matched). A customer the record would have linked to on
   its own is likely a duplicate of the winner.
   `POST /v1/customers/merge` resolves them. Either ID may already have been
   merged; the merge applies to the customers they now resolve to.

## Blocking

| Blocking key | Components |
|---|---|
| `B_EMAIL` | email |
| `B_PHONE` | phone |
| `B_UPRN` | uprn |
| `B_POSTCODE` | postcode |
| `B_PREMISE_STREET` | premise_street |
| `B_DOB_LAST_SOUNDEX` | dob + last_soundex |
| `B_DOB_LAST_NYSIIS` | dob + last_nysiis |

**Coverage invariant** (enforced by `test_every_rule_key_is_reachable_by_a_blocking_key`):
any two records that share a rule's match key also share a blocking key. This
gives two guarantees:

- No match is lost to blocking (unless a block is skipped as oversized).
- Locking every blocking key with `pg_advisory_xact_lock` serialises
  concurrent arrivals of the same person, so simultaneous requests cannot
  create duplicate customers. `test_concurrent_arrivals_get_one_customer`
  fails without the locks.

Blocks larger than `MAX_BLOCK_SIZE` are skipped and reported in
`skipped_blocks` (e.g. a university hall sharing one postcode). That is the
one place a match can be missed; monitor it. Block sizes are counted only up
to `MAX_BLOCK_SIZE + 1`, so a 400k-record block (a shared number the junk
lists miss) costs no more to skip than a small one.

The candidate pool is capped at `MAX_CANDIDATES`. Candidates are ranked by
how many blocking keys they share with the inbound record (email, phone,
postcode, dwelling, DOB + surname), so when a busy block fills the pool, the
records dropped are those sharing the fewest keys, not the newest.

Every resolve reports `lock_wait_ms`, the time spent waiting for its advisory
locks, in the response and in `resolution_event.details`, and logs a warning
above `LOCK_WAIT_WARN_MS`. Long waits point at a hot blocking key. Locks are
taken on oversized blocks too: skipping them would break the concurrency
guarantee for records near the size limit, where one arrival still uses a
block that another has started to skip.

## Shared identifiers

The junk lists (`info@`, `noreply@`, drama-range numbers, ...) catch values
that are never personal. They cannot catch a value that only real data shows
is shared: a retailer's order address added to every customer's record, an
office switchboard keyed as the home phone, a hospital mail-room postcode.
Treated as personal, these link strangers: two Sarahs carrying the same
company email match on `EMAIL_FIRST`; two John Smiths at one big-user
postcode match on `NAME_POSTCODE`.

So each resolve checks how many distinct customers already hold each of the
inbound record's emails, phone numbers and postcodes:

| Identifier | Shared when held by more than | Setting |
|---|---:|---|
| Email, phone | 10 customers | `SHARED_CONTACT_CUSTOMERS` |
| Postcode | 250 customers | `SHARED_POSTCODE_CUSTOMERS` |

A shared value is left out of the inbound record's components for matching,
so no rule fires on it. It still gathers candidates through its blocking key,
and everything else on the record matches as usual: the dwelling
(`premise_postcode`) still counts at a big-user postcode. Values in a block
skipped as oversized are always shared. The response and
`resolution_event.details` list the kinds dropped in `shared_identifiers`.

Customers are counted, not records, so one customer with many records does
not make an email shared, and a household of four sharing one email stays
under the limit. The count is cheap: a block with no more records than the
limit cannot hold more customers, so only larger blocks are counted, and
counting stops at the limit.

The first customers to carry a shared value arrive before it reaches the
limit, so a few early links on it can still be wrong. Lower the limits if
your data has many small shared values (a family business email shared by
six staff).

## Adding a rule

1. Add a `KeyDef` to `MATCH_KEYS` (and a component in `extract_components` if needed).
2. Add a `Rule` to `RULES` with a unique priority inside its tier's range, in order.
3. Run the tests. If the coverage test fails, add a blocking key that covers it.
4. Nothing to back-fill for a new match key: shared keys are worked out from
   each candidate's stored components (`source_record.match_components`) at
   match time. A new *component* or *blocking key* does need existing records
   re-posted or re-keyed.

## Privacy

**The database holds plain personal data.** Only the `blocking_key` table is
pseudonymised; protect the whole database (access control, encryption at rest,
backups) as you would any store of customer PII.

Blocking key values are `HMAC-SHA256(KEY_SECRET, type ‖ values)` truncated
to 16 bytes, so `blocking_key` holds no readable PII. Match keys are not
stored: until migration 005 they were (`match_key`, ~20 rows per record and
over half the database), but they are only ever compared against the few
candidates blocking finds, so they are now worked out in memory from the
candidate's components. That is identical to comparing the hashes (same
combinations, same 64-value cap; proved in `tests/test_matching.py` and on
the 200k benchmark). The components sit on `source_record` next to the
cleansed record they come from, in plain JSON: emails, phone numbers, dates of
birth and name forms (`match_components`). The raw and cleansed records are
stored there too, for audit and re-keying. Encrypting these columns is planned
but not done.
