# 200,000-record benchmark

Three rounds, run on 27 October 2026 against PostgreSQL 16 on a 2-CPU, 7 GB machine, three
loader processes resolving concurrently.

## Latest results (round 3)

After reviewing every wrong link from round 2, two matching bugs were fixed
and one flaw in the test data was corrected:

- **Bug: forename Soundex.** John, Jenny, Jean and Jim share a Soundex code, so
  the address-only typo rule joined fathers and daughters, and Patricia and
  Patrick. It now uses nickname forms plus *surname* Soundex
  (`NAME_LASTSOUNDEX_PREMISE_POSTCODE`) and a separate initial + surname rule
  for forename typos. "Sounds alike" no longer makes two forenames compatible,
  and gender is inferred from common UK forenames when no gender or title is
  given.
- **Bug: London streets.** Street + town matching treated 183 Gladstone Road
  SW3 and SW20 as one dwelling. A `postcode` veto now applies when both records
  have postcodes and none agree.
- **Test data:** the generator gave siblings the same forename and let children
  share a parent's forename outside the Jr path, which almost never happens in
  real families. Siblings now get distinct names; a child takes a parent's
  name only as a Jr (with the suffix recorded on 40% of their records).

| | Round 2 rules, original data | Round 3 rules, original data | Round 3 rules, corrected data |
|---|---:|---:|---:|
| Pairwise precision | 98.90% | 99.01% | **99.82%** |
| Pairwise recall | 91.79% | 91.69% | **91.99%** |
| Pairwise F1 | 0.952 | 0.952 | **0.957** |
| People perfectly resolved | 91.0% | 91.0% | **91.9%** |
| Customers holding >1 person | 399 | 355 | **87 (0.09%)** |
| Wrong links | 467 | 424 | **93** |
| Recall of findable links | 96.7% | 96.7% | **96.8%** |

The corrected dataset has 200,000 records for 87,848 people. Both round 3
runs shared the 2-CPU machine, so their throughput (~170 rec/s each) and
latency (p50 11 ms, p95 19 ms) are not comparable with earlier single runs.

**Where the 93 remaining wrong links come from** (corrected data):

| Wrong links | Tier | Cause |
|---:|---|---|
| 65 | possible | Father and son with the same name, Jr/Sr missing from a record |
| 21 | possible | Other same-household pairs with the same name and no DOB on one record |
| 2 | strong | Father and son, Jr/Sr missing |
| 2 | strong | Different households |
| 3 | mixed | Partners, house-sharers, other |

Tier precision on corrected data: exact 100% (45,575 links), strong 99.99%
(5 wrong in 49,266), probable 100% (516), possible 99.08% (88 wrong in 9,519).

**Operating choice** on corrected data (second row estimated from the same run):

| `AUTO_LINK_TIERS` | Wrong links | Arrival recall | Review queue |
|---|---:|---:|---:|
| `exact,strong,probable,possible` (default) | 93 (0.09%) | 93.4% | 0 |
| `exact,strong,probable` | ~5 (0.005%) | ~85.0% | ~9,500 suggestions, 99.1% correct |

### Round 4: twins with similar names

Building the curated demo cases showed that twins whose names sound alike
(Ella/Ellie, Jack/Jake, Mia/Maya) were auto-merged by the phonetic rules
`SOUNDEX_NAME_DOB_POSTCODE` and `NYSIIS_NAME_DOB_PREMISE_DISTRICT`: same
surname, DOB and address, and a matching Soundex/NYSIIS forename. Both rules
now also require compatible forenames (a shared nickname form, or at most one
keystroke apart), so typos like Wiliam/William still link.

On the corrected 200k dataset the cost is small:

| | Round 3 | Round 4 |
|---|---:|---:|
| Pairwise precision | 99.82% | 99.82% |
| Pairwise recall | 91.99% | 91.95% |
| Links made | 104,876 | 104,828 |
| Wrong links | 93 | 93 |
| Twins wrongly linked | 0.25% | 0.12% |
| Findable forename-typo links missed | 18.0% | 18.8% |

The generator gives twins random names that rarely sound alike, so this
understates the benefit for real families.

## Round 2 results

### Dataset

`python -m benchmark.generate --records 200000 --seed 7`: **200,000 records for
87,705 people** in synthetic UK households, 1–6 records per person across six
source systems (CRM, e-commerce, loyalty, billing, call centre, marketing),
each with its own formats and gaps. Every record carries the true person ID.

| Scenario | Records | Scenario | Records |
|---|---:|---|---:|
| Household member (shared address) | 170,606 | Shared family email | 8,275 |
| Mobile missing | 75,692 | Surname change (maiden name) | 8,157 |
| DOB missing | 65,856 | Shared couple mobile | 4,941 |
| Shared landline | 59,744 | Postcode missing / typo / in lines | 10,712 |
| Email missing | 57,173 | DOB typo / transposed / placeholder | 4,790 |
| Moved house | 20,571 | Email domain typo / variant | 6,328 |
| Unrelated house-sharers | 19,750 | Junior / Senior namesakes | 1,406 |
| Nickname used | 15,811 | Doppelganger (same name + DOB) | 1,018 |
| Old email | 9,033 | Twins | 901 |
| Surname typo | 8,158 | Another person's email or mobile keyed in | 1,586 |
| Forename typo | 8,139 | Partner's email used by mistake | 585 |

### Results

| | Baseline rules | Improved rules |
|---|---:|---:|
| Pairwise precision | 99.00% | 98.90% |
| Pairwise recall | 80.22% | **91.79%** |
| Pairwise F1 | 0.886 | **0.952** |
| People perfectly resolved | 80.9% | **91.0%** |
| People split across customers | 16,218 | **7,151** |
| Customers containing >1 person | 345 (0.33%) | 399 (0.37%) |
| Links made at arrival | 94,635 | 105,163 |
| Wrong links | 404 (0.43%) | 467 (0.44%) |
| Recall of links that were findable | 87.1% | **96.7%** |
| Throughput | 381 rec/s | 333 rec/s |
| Latency p50 / p95 / p99 | 7.1 / 13.2 / 17.4 ms | 7.7 / 15.6 / 24.4 ms |

"Findable" excludes 3,967 records that share no email, phone, postcode, UPRN or
DOB + surname with any earlier record of the same person; no deterministic
system could link them.

#### Precision by tier (improved rules)

| Tier | Links | Wrong | Precision |
|---|---:|---:|---:|
| exact | 45,463 | 0 | 100.00% |
| strong | 49,292 | 27 | 99.95% |
| probable | 526 | 0 | 100.00% |
| possible | 9,882 | 440 | 95.55% |

438 of the 440 possible-tier errors join same-named people in one household
where at least one record has no DOB (mother and daughter both "Mary Edwards", father
and son without Jr/Snr). Name + address cannot separate them.

**Operating choice** (estimated from the same run):

| `AUTO_LINK_TIERS` | Wrong links | Arrival recall | Review queue |
|---|---:|---:|---:|
| `exact,strong,probable,possible` (default) | 467 (0.44%) | 93.3% | 0 |
| `exact,strong,probable` | ~27 (0.03%) | ~84.9% | ~9,882 suggestions, 95.5% correct |

### What changed after the baseline

The baseline run's misses and wrong links drove these changes:

1. **DOB transposition never vetoes** (05/03 vs 03/05), and a *lenient* DOB check
   tolerates one wrong field where an email or mobile and a name already agree.
2. **Forename compatibility** in vetoes accepts nickname forms, Soundex
   matches and one-keystroke typos (Justin / Jusyin).
3. **New strong rules**: `EMAIL_MOBILE_FIRST`, `EMAIL_FIRST`, `MOBILE_FIRST`
   (surname typo or change), `EMAIL_LAST_INITIAL`, `MOBILE_LAST_INITIAL`
   (forename typo). All ≥ 99.8% precise.
4. **Dwelling without postcode**: a `premise_street` component (number/name +
   street + town) with `NAME_DOB_PREMISE_STREET` (exact, 100%) and a new
   blocking key.
5. **`NAME_POSTCODE` and `SOUNDEX_NAME_PREMISE_POSTCODE`** for name + address
   when the premise is recorded differently or a name is misspelt (possible tier).
6. **Removed `NAME_DOB_DISTRICT`**: 32% precise. Doppelgangers in the same
   postcode district fooled it; doppelganger wrong-link rate fell from 2.9% to 0.1%.
7. **New `possible` tier and `AUTO_LINK_TIERS`** so name + address-only matches
   can be routed to review.

### Weak spots at round 2

| Scenario | Wrong-link rate | Missed (findable) |
|---|---:|---:|
| Junior / Senior with suffix omitted | 6.8% | 3.3% |
| Forename typo | 0.15% | 19.2% |
| DOB typo | 0.22% | 10.8% |
| Surname typo | 0.14% | 9.1% |
| Surname change | 0.00% | 8.5% |
| Nickname | 0.26% | 7.2% |

In round 3 the forename-typo miss rate is 18.0% and Jr/Sr namesakes are wrongly
linked 4.8% of the time; both remain the main weak spots.

Forename typos are the biggest recall gap: a misspelt forename with no email or
mobile in common falls back to Soundex/NYSIIS keys, which need DOB.

## Caveats

- The data is synthetic. Same-name collisions inside households and the
  doppelganger rate are likely higher than in real UK data, so possible-tier
  precision is probably pessimistic. Real data will have failure modes this
  generator doesn't model.
- Records arrive in random order; later records are matched against whatever
  had arrived before them. No merges were applied during the run.
- PostgreSQL ran with `synchronous_commit=off`; throughput with durable commits
  depends on the disk.

## Reproduce

```bash
python -m benchmark.generate --records 200000 --seed 7 --out data.jsonl
python -m benchmark.run --data data.jsonl --db postgresql://.../identity_bench --workers 3 --out results/
python -m benchmark.evaluate --data data.jsonl --results results/ --db postgresql://.../identity_bench --out report.json
```

## Scale benchmark: seed, then measure

`benchmark/scale.py` answers a different question from the 200k run: how fast
is resolution when the database already holds millions of customers? It seeds
the database with the first half of a dataset, then times the second half
arriving against it.

```bash
docker compose --profile bench run --rm bench --records 5000000 --seed-records 2500000 --workers 12
```

This runs in its own PostgreSQL (`bench-db`, port 5434, tuned for a ~30 GB
machine: `shared_buffers=6GB`, `effective_cache_size=16GB`; override with
`BENCH_SHARED_BUFFERS` / `BENCH_EFFECTIVE_CACHE`), so the demo database is not
touched. The report goes to `benchmark/results/scale/report.md` and
`report.json`; the dataset and per-record results stay in the `benchfiles`
volume.

**Data.** Same generator, people and corruption as the 200k run, with two
additions that matter at scale and are off by default (so the 200k dataset is
unchanged):

- `--people-per-postcode 3.5`: addresses come from a fixed set of postcode
  units, each on one street with house numbers in sequence. A 6m-customer base
  across the UK's ~1.7m postcodes is about 3.5 customers per postcode. Without
  this, random postcodes leave nearly every postcode with one customer, and
  lookups look faster than they will be.
- `--large-buildings`: about 4% of dwellings are flats in towers and halls of
  80–600 flats sharing one postcode, which produces the large postcode blocks
  real data has.

**Phases.** Seed uses asynchronous commits (it is setup). The database is then
vacuumed, analysed and checkpointed. Measure uses durable commits
(`--async-measure` to compare with the 200k run). `--phase generate|seed|measure|report`
runs one step; measure needs a freshly seeded database.

**Report.** For each phase: throughput, latency p50/p95/p99, time spent
finding and scoring candidates (`matching_ms`), candidate pool sizes, records
whose blocks were skipped for size, and accuracy at arrival (wrong links, and
new customers created for a person already stored). Then latency by window of
250k arrivals as the database grows, the cache hit ratio of the key-lookup
indexes during measure, table and index sizes, and the largest blocks.

**Resources** for 5m records: generation ~3 minutes and ~3.5 GB; database
about 36 GB plus up to 8 GB of WAL; dataset ~2 GB. Free it afterwards by
removing only the benchmark containers and volumes:

```bash
docker compose --profile bench rm -sf bench-db bench
docker rm -f scale-bench    # if it was started with run --name
docker volume rm identity-resolution_benchpg identity-resolution_benchfiles
```

Do **not** use `docker compose --profile bench down -v`: `down -v` removes
every named volume in the project, including `pgdata`, which deletes the demo
database and its review decisions.

The pairwise and cluster scoring in `benchmark.evaluate` holds the whole
dataset in memory and is not used at this scale; accuracy here is judged at
arrival only.
