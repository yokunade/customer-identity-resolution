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
