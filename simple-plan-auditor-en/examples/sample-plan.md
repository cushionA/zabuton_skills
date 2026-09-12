# CSV Bulk Registration Implementation Plan

## 0. Overview

### 0.1 Objective

Allow administrators to import a CSV file, validate its contents before registration, and bulk-register customer data.

If invalid data exists, registration must not start. The user should be able to identify the problematic rows, correct them, and rerun the process.

### 0.2 Work Map

| Phase | Main Work | Checks |
|---|---|---|
| **Current-state review & impact analysis** | Map the current customer-registration flow | Are there any overlooked registration paths outside the admin UI? |
| **Current-state review & impact analysis** | Map related processing and dependencies | Have affected callers, shared processing, and external dependencies been covered? |
| **Current-state review & impact analysis** | Map existing data constraints | Do required fields, duplicate rules, and save-time behavior need to remain consistent on the new path? |
| **Registration policy decisions** | Define CSV fields | Are there missing or semantically mismatched mappings to existing registration fields? |
| **Registration policy decisions** | Define duplicate handling | Is the choice between error, skip, and update consistent with retry behavior? |
| **Registration policy decisions** | Define the success unit for bulk registration | Is the plan avoiding implementation before deciding between all-or-nothing and partial success? |
| **CSV intake & preview** | Prepare CSV intake | Are empty, unsupported, or unreadable files blocked before downstream processing? |
| **CSV intake & preview** | Prepare pre-registration preview | Does the content the user confirms match the content that will actually be validated and registered? |
| **Full-row validation** | Validate every row against registration rules | Does validation continue far enough to surface all relevant issues rather than stopping at the first error? |
| **Full-row validation** | Return validation results to the user | Are row numbers and reasons specific enough to fix, and is registration blocked while issues remain? |
| **Bulk registration** | Register validated data in bulk | Can unvalidated data enter the registration path? |
| **Bulk registration** | Define state after mid-process failure | Can an unintended partial registration or inconsistent intermediate state remain? |
| **Bulk registration** | Apply retry and duplicate-execution rules | Can rerunning the same operation cause unintended duplicate registrations? |
| **Admin UI integration** | Connect CSV selection through registration result to the real user flow | Are all independently implemented pieces reachable from the real usage path without missing wiring? |
| **Admin UI integration** | Return validation and registration errors to the UI | Can errors be lost before reaching the point where the user decides the next action? |
| **Integrated & regression validation** | Validate the happy path end to end | Does the flow reach the expected final state from CSV selection through registration result? |
| **Integrated & regression validation** | Validate failure, interruption, and retry | After a problem, does the system avoid bad registrations and return to a safe retryable state? |
| **Integrated & regression validation** | Revalidate existing single-record registration and editing | Does the CSV feature introduce regression in existing paths? |
| **Production rollout & recovery preparation** | Define the production rollout procedure | Are rollout prerequisites and first-run checks explicit? |
| **Production rollout & recovery preparation** | Define stop and rollback procedures | Can the system return to a safe state if a production issue occurs? |
| **Operational handoff & cleanup** | Update usage and incident-response procedures | Can operators determine how to handle errors and retries? |
| **Operational handoff & cleanup** | Remove temporary settings and verification assets | Are temporary assets removed only after production verification, with nothing unnecessary left behind? |

### 0.3 Cautions & Human Decisions

- 🔴 **Handling of existing email-address duplicates is undecided.** Error, skip, and update lead to different registration and retry behavior.
- 🔴 **Whether partial success is allowed is undecided.** Recovery design changes depending on whether the operation is all-or-nothing or row-level partial success is allowed.
- 🟡 **Maximum row count is undecided.** Validation conditions for large inputs and rollout constraints are not fixed.
- 🟡 **A human decision is required on limited initial rollout.** This affects rollout steps and first-run verification scope.

### 0.4 Key Dependencies & Branches

```text
Current-state review & impact analysis
        ↓
Registration policy decisions
        ↓
CSV intake & preview ──────────┐
        ↓                     │
Full-row validation           │
        ↓                     │
Bulk registration ────────────┤
                              ↓
                    Admin UI integration
                              ↓
                 Integrated & regression validation
                              ↓
              Production rollout & recovery preparation
                              ↓
                  Operational handoff & cleanup
```

- Do not finalize bulk-registration behavior until duplicate handling is decided.
- CSV intake and registration implementation may proceed partly in parallel after shared policy decisions are fixed.
- Integrated validation starts only after admin UI integration is complete.
- Remove temporary assets only after production rollout and first-run verification succeed.

---

# Phase 1: Current-State Review & Impact Analysis

## Objective

Understand the current customer-registration flow, dependencies, and change impact before adding CSV registration.

## Preconditions / Start Conditions

None.

## Work Steps

1. Run the existing single-record registration flow from the admin UI.
2. Trace the processing path from the UI to successful persistence.
3. Map required customer fields, duplicate rules, and shared save-time behavior.
4. Identify callers, related screens, shared data, and existing tests.
5. Separate reusable registration behavior from new behavior required specifically for CSV registration.
6. Pass the impact analysis and unresolved decisions to Phase 2.

## Outputs for the Next Phase

- Current flow
- Impact list
- Reusable processing list
- Unresolved decisions

## Checks

- [ ] Checked for registration paths outside the admin UI
- [ ] Understood constraints applied to existing customer data
- [ ] Distinguished reusable registration behavior from CSV-specific implementation
- [ ] Identified paths outside the change scope that still need regression validation

## Completion Criteria

The team can explain the change targets, reusable behavior, regression targets, and unresolved decisions.

---

# Phase 2: Registration Policy Decisions

## Objective

Define business rules that remain consistent from CSV intake through final registration results.

## Preconditions / Start Conditions

Phase 1 current-state behavior and change impact have been documented.

## Work Steps

1. Define required and optional CSV columns.
2. Define value formats and handling for invalid and empty values.
3. Define behavior for duplicates inside the CSV and duplicates against existing customers.
4. Decide between all-or-nothing success and partial success.
5. Define expected behavior on retry.
6. Define file-size and row-count limits.
7. Prepare representative valid and invalid examples.

## Checks

- [ ] The same rules can be applied consistently in intake, validation, registration, and result display
- [ ] Duplicate, retry, and partial-failure behavior is internally consistent
- [ ] Expected results can be explained without the implementer inventing missing policy

## Completion Criteria

For major input patterns, registration eligibility and expected outcomes can be explained before implementation.

---

# Phase 3: CSV Intake & Preview

## Objective

Provide an entry point where users can select a CSV file and review its contents before registration.

## Work Steps

1. Add CSV intake to the admin UI.
2. Reject unsupported formats, empty files, and unreadable files during intake.
3. Parse CSV content into the column structure defined in Phase 2.
4. Show record count and key fields before registration.
5. Pass accepted input into full-row validation.

## Branches / Exceptions

- If the file itself cannot be processed, do not continue to full-row validation.
- If the user cancels during preview, do not begin registration.

## Checks

- [ ] Basic file-format errors do not reach downstream processing
- [ ] Previewed data matches the data that will actually be validated
- [ ] Cancellation produces no registration side effects

## Completion Criteria

A valid CSV can proceed to full-row validation, while invalid files cannot reach registration logic.

---

# Phase 4: Full-Row Validation

## Objective

Validate the entire CSV before registration and return enough information for the user to correct all relevant issues.

## Work Steps

1. Scan every row.
2. Validate required values and value formats.
3. Detect duplicates within the CSV.
4. Validate conflicts against existing data.
5. Build an issue list containing row number, field, and reason.
6. Enter a registrable state only when there are zero validation issues.

## Branches / Exceptions

- Do not stop the whole validation process after the first invalid row.
- If a system error occurs during validation, do not enter a registrable state.

## Checks

- [ ] Validation reaches the final row
- [ ] Multiple issues can be reviewed at once
- [ ] Registration cannot start while validation issues exist
- [ ] The same CSV can be revalidated after correction
- [ ] Validation rules and registration-time rules do not diverge

## Completion Criteria

A CSV containing multiple issues returns an issue list without registering any data.

---

# Phase 5: Bulk Registration

## Objective

Register validated data according to the agreed bulk-processing policy while preserving consistency during failure and retry.

## Work Steps

1. Accept only data that passed validation.
2. Register according to the success unit defined in Phase 2.
3. Define the state that remains after mid-process failure.
4. Apply duplicate-prevention behavior for retry and duplicate execution.
5. Return success count, failure details, and final state.

## Checks

- [ ] Unvalidated data cannot be registered
- [ ] Mid-process failure cannot leave disallowed partial registration
- [ ] Retrying the same operation produces the defined result
- [ ] Reported registration results match persisted state

## Completion Criteria

Expected final state is confirmed for successful registration, mid-process failure, and retry.

---

# Phase 6: Admin UI Integration & Integrated Validation

## Objective

Connect each implemented component to the existing admin workflow and validate the path from entry point through result display.

## Work Steps

1. Connect CSV selection to intake processing.
2. Connect preview to full-row validation.
3. Proceed to bulk registration only after validation succeeds.
4. Return validation and registration errors to the admin UI.
5. Run the full happy path from entry point through result display.
6. Run invalid-input, mid-process failure, and retry cases.
7. Revalidate existing single-record registration and editing.

## Checks

- [ ] All independently implemented pieces are connected to the real usage path
- [ ] Error causes propagate back to the user action point
- [ ] UI state remains coherent in both success and failure cases
- [ ] Existing registration behavior has no regression
- [ ] The integrated final state matches the objective

## Completion Criteria

Representative success, failure, and retry cases can be executed end to end from the admin UI with the expected results.

---

# Phase 7: Production Rollout, Operational Handoff & Cleanup

## Objective

Move the feature into production with a recovery path, then complete the work by handing off operations and removing temporary assets.

## Work Steps

1. Confirm production rollout prerequisites.
2. Define rollout steps and first-run checks.
3. Define stop conditions and rollback procedure.
4. Deploy to production.
5. Verify first-run behavior with a valid CSV.
6. Update administrator documentation for CSV format, error handling, and retry behavior.
7. Remove logs, flags, settings, and verification assets that were only needed during implementation.
8. Reconfirm completion criteria and record remaining issues.

## Checks

- [ ] Problems can be detected immediately after cutover
- [ ] The feature can be disabled or rolled back when needed
- [ ] Post-rollback verification steps exist
- [ ] Temporary assets are not removed before production verification
- [ ] Operators understand normal usage and incident handling
- [ ] Unresolved work is not hidden behind a “complete” status

## Completion Criteria

The feature is usable in production and rollout, rollback, operations, documentation, and cleanup obligations are complete.

---

# Plan Review Example

## Result

NEEDS REVISION

## BLOCKERS

### Phase 2 / 5 — Duplicate handling is undecided

**Issue:** Behavior for rows whose email already exists is not decided between error, skip, and update.

**Why it matters:** Registration results, retry behavior, and user-facing result reporting cannot be finalized.

**Where to add or revise:** Decide the policy in Phase 2 and reflect it in Phase 5 registration and retry behavior.

### Phase 2 / 5 — Partial-success policy is undecided

**Issue:** It is undecided whether valid rows may be registered when other rows fail, or whether the operation requires all rows to succeed.

**Why it matters:** Final state after failure, recovery, and retry behavior all depend on this decision.

**Where to add or revise:** Decide the policy in Phase 2 and propagate it into Phases 5, 6, and 7.

## WARNINGS

### Phase 7 — Large-input conditions are undecided

Maximum row count is not defined, so representative high-load validation conditions cannot be finalized before production rollout.
