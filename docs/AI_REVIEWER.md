# AI reviewer data

Every review decision is saved as a labelled example, so that a future AI
reviewer can be prompted, fine-tuned and evaluated on real cases. This page
covers what is captured, how to export it, and how to score a model.

## What is captured

When a reviewer submits a decision, the same transaction writes a row to
`review_example`:

| Column | What it holds |
|---|---|
| `snapshot` | The case exactly as it was shown: both customers, every source record, the side-by-side comparison with verdicts, the rule and tier that raised it, the record that triggered it. Taken **before** a merge moves any records, so it never changes afterwards. |
| `decision`, `survivor` | `merge` or `keep_apart`; for a merge, which customer was kept (`A` = the older one) |
| `reason_codes` | Structured reasons (below); at least one is required |
| `confidence` | `certain`, `likely` or `unsure`; required |
| `notes` | The reviewer's free-text rationale; required |
| `seconds_to_decide` | From claiming the case to submitting |
| `reviewer_id` | Who decided |
| `split` | `train` / `dev` / `test`, assigned from a hash of the case ID (80/10/10). A case never changes split, so a test case can't leak into training later. |
| `label_source` | `reviewer` for human decisions; `synthetic_truth` for demo ground truth |
| `schema_version` | Version of the snapshot format (currently 1) |

The decision, reasons and confidence are also kept on the case itself
(`merge_candidate`) and in the `resolution_event` audit log.

### Reason codes

Stable identifiers in `app/review/reasons.py`. Add new codes freely but never
rename or reuse one, or old labels change meaning.

| Merge | Keep apart |
|---|---|
| `same_dob` Same date of birth | `different_dob` Different dates of birth |
| `same_email` Same email | `different_generation` Different generations (Jr/Sr) |
| `same_phone` Same phone | `different_gender` Different gender |
| `same_address` Same address | `household_member` Different people in one household |
| `nickname_or_typo` Nickname or spelling difference | `shared_contact` Shared family email or phone |
| `surname_change` Surname changed (e.g. marriage) | `name_coincidence` Same name, different people |
| `moved_address` Moved address | `insufficient_evidence` Not enough evidence to merge |
| `dob_entry_error` DOB typo or day/month swapped | |
| `data_entry_error` Other data entry error | |

### Synthetic ground truth (demo)

The demo generator knows which real person every record belongs to, so the
demo loader labels every queue case with the true answer (`label_source =
synthetic_truth`, no reviewer, no reasons or rationale). In the 20,000-record
demo that is 1,019 cases: 1,008 same person, 11 different people. Use these
for **evaluation**, not fine-tuning: they have no rationale to learn from, and
they come from a generator, not your customers. Real reviewer decisions are
the training data.

## Prompt format

`app/review/prompting.py` holds the system prompt (`SYSTEM_PROMPT`), the case
renderer and the target format. Rendering is deterministic, so a prompt change
is a code change you can diff and re-evaluate; exports carry `prompt_version`.

The model is asked to answer with JSON only:

```json
{"decision": "merge", "survivor": "A", "reason_codes": ["same_address"],
 "confidence": "likely", "rationale": "Same name and dwelling; nothing conflicts."}
```

A rendered case, with PII masked:

```
Case raised by rule NAME_PREMISE_POSTCODE (Forename + surname + dwelling), possible tier.
Customer A: 1 source record(s), the older customer.
Customer B: 1 source record(s).

Comparison (values across each customer's records; [both] = held by A and B):

Name: MATCH
  A: [Name 1] (1 source) [both]
  B: [Name 1] (1 source) [both]

Date of birth: MISSING
  A: not recorded
  B: [DOB 1] (1 source)
...
Source records:
  A | BILLING | name [Name 1] | gender F | address [Address 1]
  B | CRM | name [Name 1] | born [DOB 1] | gender F | address [Address 1]
```

## PII

Exports mask PII by default. Names, dates of birth, emails, phone numbers,
addresses and postcodes become placeholders that are consistent within one
example (`[Name 1]` everywhere it appears), including inside the reviewer's
notes. What the model needs is kept: which values are shared, each row's
verdict, and the comparison's notes such as "Nickname or spelling difference".

What masking costs: the model can't see *why* two values are close (that
"Jon" and "John" differ by a letter) beyond the verdict, or a person's age.
Scrubbing free-text notes is best-effort (known values and phone numbers in
other formats); check a sample before sending data outside your organisation.

`--pii full` keeps real values. Use it only for models you host yourself,
under your data protection assessment.

## Exporting

From the command line (uses `DATABASE_URL`):

```bash
# fine-tuning or few-shot pool: chat messages (system, user, assistant)
python -m app.review.export --format chat --split train --out train.jsonl

# evaluation set: model input + expected decision, no rationale
python -m app.review.export --format eval --split test --out test.jsonl

# a ready-to-use few-shot prompt: instructions, 6 worked examples, then {case}
python -m app.review.export --format prompt --shots 6 --out prompt.md

# everything as stored, for your own tooling (requires --pii full)
python -m app.review.export --format raw --pii full --out raw.jsonl
```

Add `--source synthetic_truth` (or `all`) to include the demo's ground-truth
labels. In Docker: `docker compose exec api python -m app.review.export ...`.

Admins can also download chat, eval and few-shot exports (masked) from the
*History* page of the review app, or from
`GET /review/api/examples/export?format=chat|eval|prompt&split=…&source=…`.

## Scoring a model

```bash
python -m app.review.export --format eval --source all --split test --out eval.jsonl
# run your model on each line's "input"; write predictions.jsonl lines as
#   {"id": "...", "output": "<raw model text>"}  or  {"id": "...", "decision": "...", "survivor": "..."}
python -m benchmark.score_ai_reviewer --eval eval.jsonl --predictions predictions.jsonl
```

Most cases in the queue are the same person, so **accuracy is misleading**.
The scorer leads with:

- **wrong merges**: the model said merge, the truth was keep apart. This is
  the costly error, because it mixes two people's data.
- **keep-apart recall**: the share of true keep-apart cases caught.
- **merge recall**: the share of true merges caught, which is the review
  workload the model would remove.

It also reports invalid outputs, survivor disagreements and a per-tier
breakdown.

Baselines on the 1,019 demo cases, which any model has to beat:

| Baseline | Accuracy | Wrong merges | Keep-apart recall | Merge recall |
|---|---:|---:|---:|---:|
| Always merge | 98.9% | 11 | 0% | 100% |
| Merge unless the comparison shows a conflict | 99.8% | 2 | 82% | 100% |

```bash
python -m benchmark.score_ai_reviewer --eval eval.jsonl --baseline no_conflict
```

## Building the AI reviewer: suggested path

1. **Collect.** Let reviewers work the queue. Every decision adds an example;
   a few hundred human decisions with notes give a useful few-shot pool and a
   first test set.
2. **Prompt.** Start from `--format prompt`. Iterate on `SYSTEM_PROMPT` and
   few-shot selection; score each change on the `dev` split.
3. **Gate.** Before any automation, require the model to beat the
   `no_conflict` baseline on wrong merges on the `test` split, and look at
   every wrong merge by hand.
4. **Assist before automating.** Show the model's answer and rationale to
   reviewers as a suggestion and record agreement. Auto-decide only the
   `certain` cases in tiers where it has proven itself.
5. **Keep labels clean.** Have a second reviewer independently decide a sample
   of cases; disagreements show where the guidelines (and the prompt) are
   unclear.
