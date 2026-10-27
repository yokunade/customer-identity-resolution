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
3. labels every review case with its true answer (the generator knows it),
   giving an evaluation set for a future AI reviewer,
4. creates two reviewer accounts and prints a summary like:

```
Demo ready.
  20,000 source records resolved to 10,450 customers; 1,019 cases waiting for review.
  1,019 cases carry synthetic ground-truth labels for AI reviewer evaluation.

  Review app   http://localhost:8000/review
               ana@demo.local (admin) or tom@demo.local, password: review-demo-2026
  API docs     http://localhost:8000/docs   (X-API-Key: demo-key)
```

Then:

- **Review cases:** open http://localhost:8000/review, sign in, and take a case.
  The demo sends name + address matches that lack a DOB or personal identifier
  to review (`AUTO_LINK_TIERS=exact,strong,probable`), so the queue has plenty in it.
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
| GET | `/v1/source-records/{system}/{id}` | Customer ID for a source record |
| POST | `/v1/customers/merge` | Merge two customers |
| GET | `/health`, `/ready` | Liveness / database readiness |
| GET | `/review` | Review app for merge decisions (see below) |

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
  "outcome": "matched",                     // matched | new_customer | existing_source_record
  "record_id": 2,
  "match": {"rule": "EMAIL_NAME_DOB", "tier": "exact", "priority": 10,
            "matched_record_ids": [1], "auto_linked": true,
            "suggested_customer_id": "79532f8d-d251-4a4a-b570-f4ff98ec1e92",
            "competing_customer_ids": []},
  "candidates_considered": 1,
  "skipped_blocks": [],
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
and its keys and returns the same customer (links are sticky).

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | local postgres | |
| `KEY_SECRET` | `change-me` | HMAC secret for keys. Changing it requires re-keying all records. |
| `API_KEYS` | empty | Comma-separated. Empty disables auth. |
| `MAX_BLOCK_SIZE` | 5000 | Blocking keys larger than this are skipped. |
| `MAX_CANDIDATES` | 2000 | Candidate pool ceiling per record. |
| `MAX_BATCH_SIZE` | 500 | |
| `AUTO_MIGRATE` | true | |
| `AUTO_LINK_TIERS` | exact,strong,probable,possible | Tiers that link automatically; others go to the merge-review queue. |
| `REVIEW_COOKIE_SECURE` | true | Review session cookie only over HTTPS (http://localhost still works in browsers). |
| `DB_POOL_MIN` / `DB_POOL_MAX` | 1 / 10 | |

## Review app

`/review` is a web app where data stewards decide merge cases: pairs of
customers that both matched an incoming record equally well, plus any matches
in tiers left out of `AUTO_LINK_TIERS`.

1. **Sign in.** Accounts are created from the command line:

   ```bash
   python -m app.review.users add --email ana@example.co.uk --name "Ana Silva" --admin
   python -m app.review.users list | disable --email … | password --email …
   ```

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

Security: scrypt password hashes, revocable server-side sessions in an
HttpOnly, SameSite=Strict cookie, a custom-header check on every change
(CSRF), and sign-in throttling (5 failures per email per 5 minutes).

| Method | Path | Purpose |
|---|---|---|
| POST | `/review/api/login`, `/logout` | Sign in / out |
| GET | `/review/api/me`, `/stats` | Current user; queue counts |
| POST | `/review/api/cases/next` | Claim (or resume) a case |
| GET | `/review/api/cases/{id}` | Case with side-by-side comparison |
| POST | `/review/api/cases/{id}/decision` | `{decision: merge \| keep_apart, notes, survivor_customer_id}` |
| POST | `/review/api/cases/{id}/release` | Return a case to the queue |
| GET | `/review/api/history` | Your decisions (`?everyone=true` for admins) |
| GET | `/review/api/examples/summary`, `/examples/export` | Training data counts and downloads (admins) |

## AI reviewer data

Each decision becomes a labelled example: the case snapshot, decision, reason
codes, confidence, notes, time taken and a fixed train/dev/test split. The demo
also labels every queue case with synthetic ground truth for evaluation.

```bash
python -m app.review.export --format chat --split train --out train.jsonl   # PII masked by default
python -m app.review.export --format prompt --shots 6 --out prompt.md
python -m benchmark.score_ai_reviewer --eval test.jsonl --predictions predictions.jsonl
```

Details, the prompt format, PII masking and baselines:
[docs/AI_REVIEWER.md](docs/AI_REVIEWER.md).

## Tests

```bash
export TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/identity_test   # will be wiped
pytest
```

144 tests: cleansing (names, postcodes, addresses, phones, emails, DOB),
phonetics, key invariants, vetoes, the decision engine, and end-to-end API
tests against PostgreSQL including a 10-thread concurrency test, and the
review workflow (sign-in, exclusive claims, merge and keep-apart effects), and
the AI reviewer data (snapshots, masking, exports, scoring, demo labels). Database
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
.github/workflows/   CI: tests against PostgreSQL
```

## Suggested next steps

- Re-link policy for updated source records that now match a different customer.
- Re-key job for rule changes or `KEY_SECRET` rotation.
- Consolidated golden record and link reasons on `GET /v1/customers/{id}`.
- Royal Mail PAF / AddressBase lookup to fill UPRNs and validate premises.
- Metrics (rule hit rates, skipped blocks, latency) via Prometheus.
