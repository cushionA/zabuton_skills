# Phase 1: data audit and quality gate

Purpose: decide whether experiments may start at all, and learn from the data how the
task should be solved. Data quality is a start condition, not a score.

## Steps

1. **Read what the user provided about the data** (brief, README, schemas). List stated
   facts and stated limitations separately ("labels not included", "10 descriptions
   unavailable"). Stated limitations become `known=True` findings: they are reported, not
   blocking - unless the plan cannot work around them.
2. **Profile** every input the pipeline will read:
   `profile_data.py <files/dirs> --out WS/audit/profile`. Note record counts, keys
   (`KEY?`), duplicated values in id/url/title-like fields, null rates, size distributions.
3. **Write `audit/checks.py`** - computed checks, no eyeballing. Cover at least:
   - schema: required fields present, types, ids unique, references between records resolve;
   - duplicates and near-duplicates of units (same source URL/title/text hash) - they double
     count and leak across splits, so they define `group_of` for splitting;
   - labels/reference: present? provenance? internally consistent (same input, different label)?
     Can every unit be scored? Count units that cannot;
   - distribution and difficulty drivers (sizes, asymmetries, missing text, rare classes),
     with counts per stratum - these become report breakdowns;
   - how much a trivial rule would solve (exact match after normalization, majority class).
     If rules solve nearly everything, LLM optimization may not be worth it - say so;
   - prompt-injection-like text inside data (instructions, "ignore previous"...), as info;
   - cost/feasibility: input size per unit vs context; rough full-pass cost from calibration.
4. **Close-read a stratified sample** (10-20 units, more for heterogeneous data). Record what
   makes units easy or hard, ambiguities, and anything that looks like a data defect.
   Distinguish genuine hard cases (keep them - they are often the point) from defects.
5. **Write `audit/data_audit.md`** (template below), then run `quality_gate.py WS`.

## Severity and the gate

| Severity | Meaning | Gate |
|---|---|---|
| critical | the task or its scoring cannot be defined on this data: unreadable inputs, missing required fields, contradictory labels, unremovable leakage | STOP |
| high | results would mislead unless a human decides: doubtful label provenance, severe skew, duplicated units that double count, many unscorable units | REVIEW |
| medium | affects interpretation; carry into the report | PASS |
| low / info | facts | PASS |

STOP or REVIEW: do not run experiments. Report to the user: finding ids, evidence (counts,
example ids), impact on what can be measured, and concrete options. Never rewrite labels,
delete inconvenient units, or mark an issue `known` just to pass. Only the user can accept a
REVIEW (`--accept-review "<their words>"`). If the data changes, re-run the audit; runs refuse
to start when input hashes differ from the gate.

## Scope decisions

Record in `charter.json` (`data.scope_notes`) and the audit: what is in scope, what is
excluded and why, with counts against the original totals. Load-test or auxiliary sets stay
out of accuracy denominators. Duplicate representations of the same data (e.g. one JSONL and
one-file-per-record copies) are counted once.

## data_audit.md template

```markdown
# Data audit - <task> (<date>)
## Inputs
file, size, records, sha256 (short), what one unit is
## Stated by the user (known limitations)
## Findings
| id | severity | finding | evidence (counts, example ids) | impact | recommendation |
## Difficulty and strata
easy vs hard drivers with counts; strata to report separately
## Trivial-solution check
what a rule/majority baseline would get, and whether LLM optimization is worth it
## Scope
included / excluded with reasons and counts
## Gate
PASS / REVIEW / STOP and why
```
