# Contributing

Thanks for helping. This service decides whether two records are the same
person, so a change that looks small can merge strangers or split one
customer in two. The process below exists to catch that before it ships.

## Set up

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
ruff check .                                       # CI runs the same pinned ruff
pytest tests/test_cleansing.py tests/test_matching.py   # pure tests: no database needed
```

The database, API and review tests need a throwaway PostgreSQL 16. The test
run drops and recreates the `public` schema, so never point it at real data:

```bash
docker run -d --rm --name cir-test-pg -p 55432:5432 \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=identity_test postgres:16
export TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/identity_test
pytest
```

Database tests skip, rather than fail, when PostgreSQL isn't reachable, so
check the skip count before trusting a green run.

## Style

- `ruff check .` must pass. There is no formatter: keep the surrounding
  layout, including compact data tables (abbreviations, nicknames, rules).
- Lines up to 120 characters. Comments say *why*, not what.
- Matching logic stays pure (`app/cleansing`, `app/matching`): no I/O, so it
  can be unit-tested. SQL lives in `app/repository.py`.

## Changing matching behaviour

Every change to cleansing, keys, rules or vetoes needs:

1. **A test that fails without the change**, using a realistic UK record pair
   (see `tests/test_matching.py`). For a veto or a new rule, test both
   directions: the pair that should now link and a household pair that must
   stay apart (parent and child, twins, partners sharing a mobile).
2. **A benchmark run**, before and after, on the same data:

   ```bash
   python -m benchmark.generate --records 200000 --seed 7 --out data.jsonl
   python -m benchmark.run --data data.jsonl --db postgresql://.../bench --workers 3 --out results/
   python -m benchmark.evaluate --data data.jsonl --results results/ --db postgresql://.../bench --out report.json
   ```

   Report precision, recall, wrong links, review-queue size, and per-tier
   precision in the pull request, and add a round to `docs/BENCHMARK.md` if
   the numbers move. The generator and the rules were written together, so
   also say what the benchmark *cannot* show about your change.

### Adding a rule

1. Add a `KeyDef` to `MATCH_KEYS` in `app/matching/keys.py`, and a component
   in `extract_components` if it needs a new one.
2. Add a `Rule` to `RULES` in `app/matching/rules.py`, in priority order and
   inside its tier's range: exact < 52, strong 52–149, probable 150–199,
   possible 200+. Import-time checks refuse a rule out of order or a weaker
   tier that outranks a stronger one.
3. Pick vetoes. Start from the defaults (DOB, gender, generation) and add the
   name vetoes unless both name parts are already in the key.
4. Run `pytest tests/test_matching.py`. **Reachability:**
   `test_every_rule_key_is_reachable_by_a_blocking_key` checks that any two
   records sharing the new match key also share a blocking key. If it fails,
   the rule could never fire (blocking would never bring the pair together),
   and concurrent arrivals would not be serialised by the advisory locks.
   Fix it by adding or widening a blocking key in `BLOCKING_KEYS`, not by
   weakening the test. A new blocking key needs existing records re-keyed.
5. Add the rule to the table in `docs/MATCHING.md`.

### Dependencies

`requirements.txt` holds version floors, for installing the service as a
library. The Docker image installs `requirements.lock`: exact versions with
hashes, resolved on the image's Python and platform. After changing
`requirements.txt`, regenerate the lock in the same base image:

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim sh -c \
  "pip install -q pip-tools && pip-compile --generate-hashes --strip-extras --allow-unsafe \
   --output-file requirements.lock requirements.txt"
```

### Database changes

Add a new numbered file to `app/migrations/`; never edit one that has
shipped. Migrations run at start-up in order, inside one transaction.

## Pull requests

- One concern per pull request, with tests, and the docs it affects.
- Add a line to `CHANGELOG.md` under *Unreleased*.
- Never commit real customer data, even in a test fixture. Make up records.

## Reporting a security or privacy problem

Don't open a public issue for a vulnerability or a way to extract personal
data. Use GitHub's private vulnerability reporting on this repository
(*Security* → *Report a vulnerability*).

## Licence

By contributing you agree that your contribution is licensed under the
[MIT licence](LICENSE).
