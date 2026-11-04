# Customer Identity Resolution

A FastAPI + PostgreSQL service that accepts customer records from any upstream
system, cleanses them to UK standards, generates deterministic match and
blocking keys, and returns a persistent customer ID: an existing one when the
record matches, a new one when it doesn't.

See [docs/MATCHING.md](docs/MATCHING.md) for the full cleansing rules, key
catalogue, rule hierarchy and design guarantees.

## Run the demo

Needs Docker with Compose v2. Nothing else to install or configure.

```bash
git clone <this repo> customer-identity-resolution
cd customer-identity-resolution
docker compose up --build          # or: make demo
```

On first start this:

1. starts PostgreSQL 16 and the API (migrations run automatically),
2. generates 20,000 synthetic UK customer records (typos, nicknames, moves,
   surname changes, shared family emails, twins, Jr/Snr…) and resolves every
   one through the real cleansing and matching pipeline, which takes about
   two minutes,
3. loads 17 curated scenarios first, so the front of the review queue holds 18
   realistic hard cases, about half same person and half different people
   (see below),
4. labels every review case with its true answer (the generator knows it),
   giving an evaluation set for a future AI reviewer,
5. creates two reviewer accounts and prints a summary like:

```
Demo ready.
  20,040 source records resolved to 10,487 customers; 1,136 cases waiting for review.
  The first 18 cases in the queue are curated hard cases; every case has a known answer.

  custIDR      http://localhost:8000/app
               ana@demo.local (admin), mo@demo.local (manager) or tom@demo.local, password: review-demo-2026
  API docs     http://localhost:8000/docs   (X-API-Key: demo-key)
```

Then:

- **Look up and review:** open http://localhost:8000/app and sign in. As mo@demo.local (manager), search for a customer by email or phone; as tom@demo.local, take a case from the review queue.
  The demo sends name + address matches that lack a DOB or personal identifier
  to review (`AUTO_LINK_TIERS=exact,strong,probable`), so the queue has plenty in it.
  In demo mode (`DEMO_MODE=true`) the app shows the known answer and why
  **after** you submit each decision, never before.
- **Try the API:** open http://localhost:8000/docs, click *Authorize*, enter
  `demo-key`, and post a record to `/v1/resolve`, or use the curl example below.
  `/v1/cleanse` shows the cleansing and keys without storing anything.
- **Query the data:** PostgreSQL is on `localhost:5433`
  (`psql postgresql://identity:identity@localhost:5433/identity`).

| Command | What it does |
|---|---|
| `make demo` | Build, start, load demo data, follow the loader's output |
| `make down` | Stop, keeping the data |
| `make reset` | Delete all data and start again |
| `make logs` | Follow the API logs |
| `make users` | List reviewer accounts |

Ports, the number of demo records, the reviewer password and every service
setting can be overridden in a `.env` file (see `.env.example`). The demo
defaults (key secret, API key, passwords, plain-http cookies) are for a laptop
only.

### The curated cases

Each one is a short sequence of records loaded through the real engine, so
the rule, tier and comparison you see are what the engine produced. They come
first in the queue, alternating between same person and different people:

| # | Case | True answer |
|---|---|---|
| 1 | Father and son, Jr/Sr missing | Same person as the son; not the father |
| 2 | Nickname on a billing record | Same person |
| 3 | Mother and daughter share a name and a family email | Different people |
| 4 | Surname misspelt by the call centre | Same person |
| 5 | Twins at the same address | Different people |
| 6 | Forename misspelt at checkout | Same person |
| 7 | Twins with similar-sounding names | Different people |
| 8 | New record has no postcode | Same person |
| 9 | Neighbours with the same name | Different people |
| 10 | Flat number left off | Same person |
| 11 | Same name, different flats | Different people |
| 12 | Same property reference, house name abbreviated | Same person |
| 13 | Son marked Jr; father's record has no suffix | Different people |
| 14 | Moved house, old address given | Same person |
| 15 | Couple share a mobile; caller gave only an initial | Different people |
| 16 | Double-barrelled surname without the hyphen | Same person |
| 17 | Mother and daughter; the clue is in the email | Different people |

The records are in `benchmark/demo_scenarios.py`. Each case's explanation is
stored with its label, so the set also works as a hard evaluation set for an
AI reviewer.

### Without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/identity KEY_SECRET=... API_KEYS=dev-key
uvicorn app.main:app --reload
python -m benchmark.seed_demo        # optional demo data
```

Migrations run automatically on start (`AUTO_MIGRATE=true`), or manually with
`python -m app.db`.

## API

All `/v1` endpoints require `X-API-Key` when `API_KEYS` is set.

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/resolve` | Cleanse, match and return a customer ID |
| POST | `/v1/resolve/batch` | Up to `MAX_BATCH_SIZE` records, each in its own transaction |
| POST | `/v1/cleanse` | Dry run: cleansed record, quality issues and key counts; stores nothing |
| GET | `/v1/customers/{id}` | Customer and linked source records (follows merges) |
| GET | `/v1/customers?email=` / `?mobile=` / `?postcode=` | Customers holding that value; the input is cleansed first, so formatting differences still match. Optional `limit` (default 20, max 100) |
| GET | `/v1/source-records/{system}/{id}` | Customer ID for a source record |
| POST | `/v1/customers/merge` | Merge two customers |
| GET | `/health`, `/ready` | Liveness / database readiness |
| GET | `/app` | custIDR: customer lookup and merge review (see below); `/review` redirects here |

### Example

```bash
curl -s localhost:8000/v1/resolve -H 'X-API-Key: demo-key' -H 'Content-Type: application/json' -d '{
  "source_system": "web-shop",
  "source_record_id": "991",
  "full_name": "BILL SMITH",
  "date_of_birth": "14/02/1979",
  "email": "billsmith+promo@googlemail.com",
  "mobile_phone": "+44 (0)7712 345678",
  "address": {"lines": ["12 Acacia Ave", "Leeds LS62AB"]}
}'
```

```jsonc
{
  "customer_id": "79532f8d-d251-4a4a-b570-f4ff98ec1e92",
  "outcome": "matched",                     // matched | new_customer | existing_source_record | relinked
  "record_id": 2,
  "match": {"rule": "EMAIL_NAME_DOB", "tier": "exact", "priority": 10,
            "matched_record_ids": [1], "auto_linked": true,
            "suggested_customer_id": "79532f8d-d251-4a4a-b570-f4ff98ec1e92",
            "competing_customer_ids": []},
  "candidates_considered": 1,
  "skipped_blocks": [],
  "shared_identifiers": [],                 // email | phone | postcode held by too many customers to match on
  "match_keys_generated": 17,
  "data_quality": [{"field": "address.postcode", "code": "postcode_found_in_lines", "severity": "info"}],
  "cleansed": { "...": "standardised record" },
  "processing_ms": 6.1
}
```

### Inbound record

Only `source_system` and `source_record_id` are required. Supply whatever
else you have: name parts or `full_name`, `title`, `suffix`, `gender`,
`date_of_birth`, `email`/`emails`, `phone`/`mobile_phone`/`home_phone`/
`work_phone`/`phones`, `address` and `previous_addresses` (each with
`line1`–`line4` or `lines`, `town`, `county`, `postcode`, `country`, `uprn`).
Unknown fields are kept in the stored raw record.

Re-sending the same `source_system` + `source_record_id` updates that record
and its keys and returns the same customer (links are sticky). The exception is
a record that is its customer's only one: it is matched again, so a corrected
typo can join the right customer. If it auto-links, its old customer is merged
into the new one (the old ID keeps resolving) and the outcome is `relinked`.
A pair a reviewer kept apart is never re-linked.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | local postgres | |
| `KEY_SECRET` | `change-me` | HMAC secret for keys. Changing it requires re-keying all records. |
| `API_KEYS` | empty | Comma-separated. Empty disables auth. |
| `MAX_BLOCK_SIZE` | 5000 | Blocking keys larger than this are skipped. |
| `MAX_CANDIDATES` | 2000 | Candidate pool ceiling per record; candidates sharing the most blocking keys are kept. |
| `SHARED_CONTACT_CUSTOMERS` | 10 | An email or phone held by more customers than this is shared (a company inbox, a switchboard) and not matched on. |
| `SHARED_POSTCODE_CUSTOMERS` | 250 | The same for a postcode (a big-user postcode); the dwelling still matches. |
| `LOOKUP_MAX_PER_HOUR` | 300 | Customer lookups (searches and views) one manager may make per hour. |
| `LOCK_WAIT_WARN_MS` | 200 | Log a warning when a resolve waits longer than this for its advisory locks. |
| `MAX_BATCH_SIZE` | 500 | |
| `MAX_REQUEST_BYTES` | 2000000 | Larger request bodies are refused with 413 before parsing. Every field also has a length limit. |
| `AUTO_MIGRATE` | true | |
| `AUTO_LINK_TIERS` | exact,strong,probable,possible | Tiers that link automatically; others go to the merge-review queue. |
| `REVIEW_COOKIE_SECURE` | true | Review session cookie only over HTTPS (http://localhost still works in browsers). |
| `DB_POOL_MIN` / `DB_POOL_MAX` | 1 / 10 | |

## custIDR app

`/app` (custIDR) is the web app for people. Managers look customers up; reviewers
and managers decide merge cases: pairs of
customers that both matched an incoming record equally well, plus any matches
in tiers left out of `AUTO_LINK_TIERS`.

1. **Sign in.** Accounts are created from the command line:

   ```bash
   docker compose exec api python -m app.review.users add --email you@example.com --name "Your Name" --role admin
   docker compose exec api python -m app.review.users password --email ana@demo.local
   ```

   Roles: `reviewer` decides cases; `manager` can also look up customers;
   `admin` can also export training data.

2. **Take next case.** Cases are handed out oldest first. A claimed case is
   held for one reviewer for 30 minutes, then returns to the queue; two
   reviewers never get the same case.
3. **Compare.** Both customers side by side, every value aggregated across
   their source records. Each row gets a verdict (match, close, differs,
   conflict, missing), values held by both are underlined, and the record
   that raised the case is identified.
4. **Decide.** *Same person* (choose which customer ID to keep; the older one is
   the default) or *Different people*, with at least one reason ("same date
   of birth", "different generations (Jr/Sr)"…), a confidence level and a note.
5. **Submit.** In one transaction:
   - Merge: source records move to the kept customer, the other customer is
     marked merged and redirects to it, other open cases naming it are repointed
     (or closed if nothing is left to decide).
   - Keep apart: the pair is recorded as rejected and never suggested again.
   - Both write the reviewer, time, reasons and note to `merge_candidate` and an
     event to `resolution_event`.
   - Both save a labelled training example for a future AI reviewer, with a
     snapshot of the case as shown (see [docs/AI_REVIEWER.md](docs/AI_REVIEWER.md)).

### Customer lookup (managers)

Managers (and admins) can find a customer by email, phone number (any
format, landlines included), postcode, name with date of birth, source record
(`CRM/C-1042`) or customer ID. The value is cleansed as an inbound record
would be and found through the matcher's own blocking keys, so
`Bill.Smith+promo@googlemail.com` finds `billsmith@gmail.com`. A search on an
email, phone or postcode held by more customers than the shared limits (see
`docs/MATCHING.md`) is flagged `shared`.

The customer view shows everything held: names, dates of birth, emails and
phones (each with the sources that gave it, and whether it is shared),
addresses, every source record as received and as cleansed with how it was
linked, resolution history, merged-in customers, open review cases, and who
has looked the customer up recently.

Every search and every customer view is logged to `lookup_access` (user, time,
cleansed value searched, customers shown, optional reason), and each manager
is limited to `LOOKUP_MAX_PER_HOUR` lookups.

Security: scrypt password hashes, revocable server-side sessions in an
HttpOnly, SameSite=Strict cookie, a custom-header check on every change
(CSRF), a strict Content-Security-Policy, and sign-in throttling (5 failures
per email per 5 minutes, counted in the database so it holds across workers).

| Method | Path | Purpose |
|---|---|---|
| POST | `/review/api/login`, `/logout` | Sign in / out |
| GET | `/review/api/me`, `/stats` | Current user; queue counts |
| GET | `/review/api/overview` | Team-wide counts, outstanding cases by tier, and the next 50 cases |
| POST | `/review/api/cases/next` | Claim (or resume) a case |
| GET | `/review/api/cases/{id}` | Case with side-by-side comparison |
| POST | `/review/api/cases/{id}/decision` | `{decision: merge \| keep_apart, notes, survivor_customer_id}` |
| POST | `/review/api/cases/{id}/claim` | Claim a chosen case (opened from a customer's page) |
| POST | `/review/api/cases/{id}/release` | Return a case to the queue |
| GET | `/review/api/history` | Your decisions (`?everyone=true` for admins) |
| GET | `/review/api/lookup?q=&type=&dob=&reason=` | Customer search (managers); `type` defaults to `auto` |
| GET | `/review/api/lookup/recent` | Your latest distinct searches (managers; not itself logged) |
| GET | `/review/api/customers/{id}?reason=` | Everything held about one customer (managers) |
| POST | `/review/api/records/check` | What adding a record would do: cleansed values and the customer it would join (managers; stores nothing) |
| POST | `/review/api/records` | Add a record by hand as `MANUAL/M-000001`, matched like any other; `submit_token` makes a repeat save a no-op (managers) |
| GET | `/review/api/examples/summary`, `/examples/export` | Training data counts and downloads (admins) |

## AI reviewer data

Each decision becomes a labelled example: the case snapshot, decision, reason
codes, confidence, notes, time taken and a fixed train/dev/test split. The demo
also labels every queue case with synthetic ground truth for evaluation.

```bash
python -m app.review.export --format chat --split train --out train.jsonl   # PII masked (best effort)
python -m app.review.export --format prompt --shots 6 --out prompt.md
python -m benchmark.score_ai_reviewer --eval test.jsonl --predictions predictions.jsonl
```

Masking is best effort. Structured values are replaced exactly, but reviewer
notes are free text, so masked exports leave them out unless you pass
`--notes`. Check a sample before sending any export outside your organisation.
Details, the prompt format, PII masking and baselines:
[docs/AI_REVIEWER.md](docs/AI_REVIEWER.md).

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
pytest tests/test_cleansing.py tests/test_matching.py   # pure tests: no database needed
export TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/identity_test   # will be wiped
pytest
```

The tests cover cleansing (names, postcodes, addresses, phones, emails, DOB),
phonetics, key invariants, vetoes, the decision engine, and end-to-end API
tests against PostgreSQL including a 10-thread concurrency test, and the
review workflow (sign-in, exclusive claims, merge and keep-apart effects), and
the AI reviewer data (snapshots, masking, exports, scoring, demo labels), and
the curated demo cases (each raises its intended case through the engine). Database
tests skip if PostgreSQL isn't reachable.

## 200k benchmark

`benchmark/` generates a synthetic UK population with ground truth (typos,
nicknames, moves, surname changes, shared family emails and mobiles, twins,
Jr/Snr, doppelgangers, other people's PII keyed in by mistake) and scores the
service against it. Results are in [docs/BENCHMARK.md](docs/BENCHMARK.md).

```bash
python -m benchmark.generate --records 200000 --out data.jsonl
python -m benchmark.run --data data.jsonl --db postgresql://.../identity_bench --workers 3 --out results/
python -m benchmark.evaluate --data data.jsonl --results results/ --db postgresql://.../identity_bench --out report.json
```

`benchmark.run` wipes the target database first.

To time resolution against a database that already holds millions of
customers (seed 2.5m records, then measure 2.5m more), see "Scale benchmark"
in [docs/BENCHMARK.md](docs/BENCHMARK.md):

```bash
docker compose --profile bench run --rm bench --records 5000000 --seed-records 2500000 --workers 12
```

## Layout

```
app/
  main.py            FastAPI routes
  service.py         resolve orchestration and merge
  repository.py      all SQL
  db.py              connection pool + migration runner
  schemas.py         request/response models
  cleansing/         names, nicknames, postcode, address, phone, email, dob
  matching/          phonetics, keys, rules, engine (pure, no I/O)
  review/            review app: auth, cases, comparison, routes, static UI, user CLI
  migrations/        SQL migrations
benchmark/           synthetic data generator, demo loader, parallel loader, evaluator
tests/
docs/MATCHING.md     cleansing rules, key catalogue, rule hierarchy
docs/BENCHMARK.md    200k benchmark results
docs/AI_REVIEWER.md  training data for a future AI reviewer
docker-compose.yml   local demo stack
Makefile             demo shortcuts
.github/workflows/   CI: lint, tests against PostgreSQL, Docker build and smoke test
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md), including how to add a match rule and
prove it reachable, and [CHANGELOG.md](CHANGELOG.md).

## Licence

[MIT](LICENSE).

## Suggested next steps

- Re-link policy for updated source records with other records on their customer that now match a different customer.
- Re-key job for rule changes or `KEY_SECRET` rotation.
- Consolidated golden record and link reasons on `GET /v1/customers/{id}`.
- Royal Mail PAF / AddressBase lookup to fill UPRNs and validate premises.
- Metrics (rule hit rates, skipped blocks, latency) via Prometheus.
