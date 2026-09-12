# Plan Audit Rules

The purpose of this audit is not to score the technical design itself. It is to verify that the plan includes the **work, decisions, wiring, and validation required to complete the job end to end**.

Low-level technical detail may be delegated to L2 when appropriate. Logically necessary checks must not be omitted. If there is genuinely nothing to check, write `None` instead of leaving the field blank.

# 1. Current-State Review & Impact Analysis

Check whether the plan:

- Includes work to understand the current user and processing flows
- Reviews related code, configuration, data, operations, and existing tests
- Maps callers, downstream dependencies, shared data, and external dependencies
- Identifies existing paths affected by the change
- Evaluates impact on existing users and data
- Checks for similar functionality or reusable mechanisms

# 2. Requirements & Policy Decisions

Check whether the plan:

- Lists decisions that must be made before implementation
- Clarifies whether the work adds a new path or replaces an existing one
- Clarifies whether cutover is all-at-once or staged
- Defines treatment of existing data and existing users
- Defines behavior for errors, retries, and duplicate execution
- Marks undecided items explicitly as assumptions or human decisions

# 3. Preparation

Check whether the plan:

- Prepares required permissions, environments, configuration, and test data
- Defines expected results that can be checked before implementation
- Includes backup or protection measures when needed
- Confirms readiness of external dependencies

# 4. Major Changes

Check whether the plan:

- Covers all major change units required to reach the goal
- Includes input, core processing, output, persistence, and consumption where relevant
- Includes necessary surrounding changes that must move with the core change

Do not require low-level library choices or individual API-call details here.

# 5. Wiring & Integration

This is one of the highest-priority audit areas.

Check whether:

- New behavior is actually invoked from the real usage path
- Outputs are passed into the next phase
- Old and new data formats or interfaces are connected correctly
- Configuration, routing, or references are switched to the intended path
- Error results propagate back to users or downstream work

# 6. Work Order & Dependencies

Check whether:

- Implementation starts only after required discovery is complete
- Dependent work waits for required policy decisions
- End-to-end validation is not scheduled before integration is complete
- Cutover is not scheduled before required migration completes
- Old paths are not removed before the new path is proven
- Parallelizable work and strictly sequential work are distinguished

# 7. Happy-Path Validation

Check whether the plan:

- Validates the full path from the real entry point to the final result
- Confirms important intermediate phases were actually traversed
- Confirms the result matches the original objective
- Covers the primary usage patterns

# 8. Failure Paths & Boundaries

Check whether the plan considers:

- Invalid input
- External dependency failure
- Failure during processing
- Partial success and partial failure
- Timeout or interruption when relevant
- Boundary values, empty input, and large input where relevant

# 9. Retry & Duplicate Execution

Check whether:

- The result of repeating the same operation is defined
- Duplicate submission or concurrent execution needs handling
- Restart position after interruption is clear
- Retry cannot create duplicated data or duplicated side effects unintentionally

# 10. Regression Impact

Check whether the plan:

- Re-validates major existing behavior outside the changed path
- Runs existing tests where applicable
- Re-checks major existing user flows
- Compares before/after behavior when necessary

# 11. Data & State Migration

Required when applicable.

Check whether:

- Existing data or state is assessed before migration
- A conversion or migration procedure exists
- Migration results are validated
- Partial failure behavior is defined
- The migration is retryable where needed
- Pre- and post-migration consistency is verified

# 12. Cutover

When old and new paths coexist, check whether:

- Cutover prerequisites are explicit
- Cutover actions are explicit
- Immediate post-cutover checks are defined
- Staged rollout has criteria for moving to the next stage
- Conditions for stopping the old path are explicit

# 13. Rollback & Recovery

Required for significant changes.

Check whether:

- Rollback conditions are defined
- A rollback procedure exists
- Data changes can be restored where necessary
- Cases requiring manual recovery are understood
- Recovery is followed by re-validation

# 14. Removal of Obsolete & Temporary Assets

Check whether the plan:

- Stops or removes obsolete code and paths
- Removes temporary flags and one-off settings
- Removes temporary files, debug output, and temporary data
- Updates or retires outdated documentation
- Removes old assets only after the new path is confirmed

# 15. Operational Handoff & Documentation

Check whether the plan:

- Updates user or operational procedures
- Shares monitoring and verification methods
- Records incident-check and recovery procedures
- Includes communication to operators or related teams when needed
- Records important decisions and reasons for the change

# 16. Final-State Verification

Check whether the plan:

- Re-validates the real usage flow at the end
- Makes unresolved issues and remaining work explicit
- Completes migration, cutover, cleanup, and documentation where applicable
- Confirms the post-work state matches the original objective
- Does not label an unfinished or waiting state as complete

# 17. Hidden Assumptions

Check whether the plan silently assumes:

- Required permissions already exist
- External services are reachable
- Existing data already matches the expected format
- Other teams or phases will complete work on time
- Validation environments and observation methods are available

Promote important assumptions into prerequisites, dependencies, or explicit unresolved items.

# 18. Work Granularity

Signs a work item is too large:

- Several independent decisions, branches, or deliverables are mixed together
- There is no intermediate review point
- It is unclear where to restart after failure

Signs a work item is too small:

- One file edit or one function change appears as an independent L0 row
- The plan reads like implementation narration
- The overall flow is buried in low-level detail

Use L0 for supervision units, L1 for phase units, and L2 for execution units.

# 19. L0 Supervisability

After reading only L0, a reviewer should be able to answer:

- What is this plan trying to achieve?
- What work must happen before completion?
- Where is current-state impact analyzed?
- What is being changed or implemented?
- Where is the work wired into the real usage path?
- Which phases contain important branches or failure conditions?
- Where are happy path, failure path, and retry behavior validated?
- Are migration, cutover, or rollback needed?
- What needs cleanup or documentation afterward?
- Which items need caution or human decisions?
- What are the key dependencies and branches?
- Which high-risk phases deserve deeper review?

If these cannot be answered, the plan artifact is inadequate even if the detailed implementation is technically correct.

# 20. Audit Check Set

This is not a reduction template. Evaluate every applicable item and reflect it in L0 Checks or L1.

```text
[Current state & impact]
□ Understand current flows and existing behavior
□ Map callers, downstream dependencies, shared data, and external dependencies
□ Identify affected paths, users, and data

[Decisions & preparation]
□ Surface decisions that must be made before implementation
□ Surface unresolved items and external dependencies
□ Prepare required permissions, environments, validation data, and protection measures

[Change & wiring]
□ Cover major changes required for the goal
□ Cover required surrounding changes
□ Wire new deliverables into real usage paths
□ Maintain consistency with existing processing and data

[Validation]
□ Validate the happy path from entry point to final result
□ Validate invalid input, external failure, and mid-process failure
□ Validate partial success, interruption, retry, and duplicate execution when needed
□ Validate regression impact on existing behavior
□ Confirm the final result matches the original objective

[Migration & cutover]
□ Plan required data or state migration
□ Validate migration results
□ Define cutover prerequisites, steps, and immediate checks
□ Define rollback conditions and recovery procedures

[Completion]
□ Plan removal of obsolete and temporary assets
□ Update operations, documentation, and handoff
□ Surface unresolved issues and remaining work
□ Reconfirm that the final state matches the original objective
```

# 21. L0 Structural Completeness

Check whether:

- Each Work Map row contains one main work item
- Repeated phase names are allowed instead of packing unrelated work into one cell
- Every work item has explicit Checks
- `None` is written when there is genuinely nothing to check
- `Cautions & Human Decisions` appears immediately after the Work Map
- `None` is written when there are no cautions or human decisions
- `Key Dependencies & Branches` is the final section in L0
- `None` is written when there are no noteworthy dependencies or branches
- If L0 is too large, abstraction is raised instead of compressing cells

`scripts/validate_overview.py` performs a coarse structural check of these rules. A plan that fails the structural check should be sent back for L0 revision before detailed planning is finalized.
