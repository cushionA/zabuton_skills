# Plan Output Format

This template is not a rigid schema. The goal is to let readers move from **L0 → L1 → L2** only as deep as needed, while making the overall flow and important checks understandable from L0 alone.

# L0: Overview

```markdown
# <Plan Name>

## 0. Overview

### 0.1 Objective
<What becomes possible or what changes after the work is complete>

### 0.2 Work Map

| Phase | Main Work | Checks |
|---|---|---|
| Current-state review & impact analysis | Map the current user flow | Are there any overlooked entry points? |
| Current-state review & impact analysis | Map related dependencies | Are there hidden dependencies? |
| ... | ... | ... |

### 0.3 Cautions & Human Decisions
- 🔴 ...
- 🟡 ...
- If there are none, write `None`

### 0.4 Key Dependencies & Branches
- ...
- If there are no noteworthy dependencies or branches, write `None`
```

`0.4 Key Dependencies & Branches` must be the final section in the overview.

## Work Map Principles

### One main work item per row

Do not pack several independent work items into one table row.

Good:

```markdown
| Current-state review & impact analysis | Map the current user flow | Are there any overlooked entry points? |
| Current-state review & impact analysis | Map dependencies and shared data | Are there hidden dependencies? |
```

Avoid:

```markdown
| Current-state review & impact analysis | Map the current flow / dependencies / shared data / impact | ... |
```

Phase names do not need to be unique. Repeat the same phase name for multiple work items in the same phase.

### Make Checks the main review surface

The purpose of the table is not merely to explain implementation. It is to help a human notice **missing work, invalid ordering, and overlooked conditions for success**.

Prefer logical checks such as:

- Are prerequisites settled?
- Does the work connect correctly to downstream phases?
- Is behavior defined for each important branch?
- Can partial success or mid-process failure leave inconsistent state?
- Can retries or duplicate execution corrupt the result?
- Is impact on existing paths checked?
- Are migration, cutover, and rollback ordered correctly?
- Will obsolete or temporary assets remain afterward?

Do not remove logically necessary checks just to shorten the table.

### When there is nothing to check

Do not leave the field blank. Write `None` explicitly.

This distinguishes “reviewed and nothing noteworthy” from “not reviewed.”

### Granularity for large plans

If L0 becomes too large, do not pack several work items into one cell. **Raise the abstraction level of the work shown in L0.**

For example, L1 might contain:

```text
Accept file
Validate file format
Read contents
Generate preview
```

while L0 may show:

```text
Prepare input intake
Prepare pre-registration review
```

However, do not hide independent high-risk branches, migrations, cutovers, recovery work, or other work that deserves separate human supervision.

# L1: Phase Detail

```markdown
# Phase N: <Phase Name>

## Objective
<The state this phase must achieve>

## Preconditions / Start Conditions
- ...
- If there is nothing noteworthy, write `None`

## Work Steps
1. ...
2. ...
3. ...

## Branches / Exceptions
- ...
- If there is nothing noteworthy, write `None`

## Outputs for the Next Phase
- ...

## Checks
- [ ] ...
- [ ] ...
- If there is nothing noteworthy, write `None`

## Completion Criteria
<The state that makes this phase complete>

## Detailed Tasks
...
```

Completion criteria belong in L1. Do not add a standalone completion-criteria section to L0. Required end-state checks should be reflected in the Work Map Checks.

Low-risk phases may omit unnecessary subsections. If a subsection is present, do not leave it blank; write `None` when appropriate.

# L2: Task Detail

```markdown
### Task N.M: <Name>

**Before**
- Pre-checks

**Action**
- What to do

**Touch**
- What is changed or inspected

**Connect**
- How it connects to another phase or existing path

**Verify**
- How to verify it
```

Use only the fields that are needed.

# Plan Review Summary

Do not re-explain the entire plan in the audit result. Direct the reader to the problems.

```markdown
# Plan Review

## Result
PASS / NEEDS REVISION

## BLOCKERS
### Phase N — <Short title>
Issue:
Why it matters:
Where to add or revise:

## WARNINGS
...

## MISSING WORK
- ...

## ORDER / DEPENDENCY ISSUES
- ...

## UNVERIFIED CONDITIONS
- ...

## HIDDEN ASSUMPTIONS
- ...

## HUMAN DECISIONS REQUIRED
- ...
```
