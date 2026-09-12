#!/usr/bin/env python3
"""L0 overview structure validator for simple-plan-auditor-en.

This intentionally performs only coarse structural validation. Logical correctness
is reviewed by the agent using AUDIT_RULES.md.

Design:
- Section numbering such as 0.1 and bold styling are optional; names and order are normative.
- Continue table and field checks even if the section hierarchy is wrong, so one run reports multiple structural problems.
- Ignore code fences during structure detection so examples do not trigger false matches.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

OVERVIEW_HEADING = "## 0. Overview"
EXPECTED_HEADERS = ["Phase", "Main Work", "Checks"]

SECTION_PATTERNS: list[tuple[str, str]] = [
    ("Objective", r"Objective"),
    ("Work Map", r"Work\s+Map"),
    ("Cautions & Human Decisions", r"Cautions\s*(?:&|and|/)\s*Human\s+Decisions"),
    ("Key Dependencies & Branches", r"Key\s+Dependencies\s*(?:&|and|/)\s*Branches"),
]
EXPECTED_SECTIONS = [name for name, _ in SECTION_PATTERNS]

H3_RE = re.compile(r"(?m)^###\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*```")
FENCE_MASK = "⟪code⟫"


def mask_fences(text: str) -> str:
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            inside = not inside
            out.append(FENCE_MASK)
            continue
        out.append(FENCE_MASK if inside else line)
    return "\n".join(out)


def normalize_heading(title: str) -> str:
    t = title.strip()
    t = re.sub(r"^\**\s*", "", t)
    t = re.sub(r"\s*\**$", "", t)
    t = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", t)
    t = re.sub(r"^\**\s*", "", t).strip()
    return t


def canonical_name(title: str) -> str | None:
    normalized = normalize_heading(title)
    for name, pattern in SECTION_PATTERNS:
        if re.fullmatch(pattern, normalized, flags=re.IGNORECASE):
            return name
    return None


def overview_only(text: str) -> str | None:
    match = re.search(r"(?im)^##\s+0\.?\s*Overview\s*$", text)
    if not match:
        return None
    start = match.start()
    next_h1 = re.search(r"(?m)^# (?!#)", text[match.end():])
    end = match.end() + next_h1.start() if next_h1 else len(text)
    return text[start:end].strip()


def split_sections(overview: str) -> list[dict[str, str | None]]:
    matches = list(H3_RE.finditer(overview))
    sections: list[dict[str, str | None]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(overview)
        sections.append(
            {
                "raw": match.group(1).strip(),
                "name": canonical_name(match.group(1)),
                "body": overview[match.end():end].strip(),
            }
        )
    return sections


def parse_table(map_body: str) -> tuple[list[list[str]], list[str]]:
    errors: list[str] = []
    lines = [line.strip() for line in map_body.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return [], ["Work Map does not contain a Markdown table."]

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    headers = [re.sub(r"\*", "", cell).strip() for cell in cells(lines[0])]
    if headers != EXPECTED_HEADERS:
        errors.append(f"Table headers must be {EXPECTED_HEADERS}. Current: {headers}")

    separator = cells(lines[1])
    if len(separator) != 3 or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
        errors.append("The table separator row is not a valid three-column Markdown separator.")

    rows: list[list[str]] = []
    for index, line in enumerate(lines[2:], start=1):
        row = cells(line)
        if len(row) != 3:
            errors.append(f"Work Map row {index} does not contain exactly three columns.")
            continue
        rows.append(row)

    if not rows:
        errors.append("Work Map contains no work rows.")

    return rows, errors


def packing_reason(work: str) -> str | None:
    """Coarsely detect obvious packing of several work items into one cell.

    A single slash such as A/B testing or CSV/TSV is allowed to avoid noisy false positives.
    """
    if re.search(r"<br\s*/?>", work, flags=re.IGNORECASE):
        return "multiple work items appear to be separated with an HTML line break"
    if re.search(r"[;；]", work):
        return "multiple work items appear to be separated with semicolons"
    if len(re.findall(r"[/／]", work)) >= 2 or re.search(r"\s[/／]|[/／]\s", work):
        return "multiple work items appear to be separated with slashes"
    return None


def validate(text: str) -> list[str]:
    errors: list[str] = []
    raw_overview = overview_only(text)
    if raw_overview is None:
        return [f"Missing {OVERVIEW_HEADING}."]

    overview = mask_fences(raw_overview)
    sections = split_sections(overview)

    actual = [section["name"] or f"?{section['raw']}" for section in sections]
    if actual != EXPECTED_SECTIONS:
        errors.append(
            "L0 must contain only these four sections, in this order: "
            + " → ".join(EXPECTED_SECTIONS)
            + f". Current: {actual}"
        )

    by_name = {section["name"]: section["body"] for section in sections if section["name"]}

    objective = by_name.get("Objective")
    if objective is not None and not objective.strip():
        errors.append("Objective is blank.")

    map_body = by_name.get("Work Map")
    if map_body is not None:
        rows, table_errors = parse_table(map_body)
        errors.extend(table_errors)

        for index, row in enumerate(rows, start=1):
            phase, work, checks = row
            if not phase:
                errors.append(f"Work Map row {index}: Phase is blank.")
            if not work:
                errors.append(f"Work Map row {index}: Main Work is blank.")
            if not checks:
                errors.append(
                    f"Work Map row {index}: Checks is blank. Write `None` if there is nothing to check."
                )

            reason = packing_reason(work)
            if reason:
                errors.append(
                    f"Work Map row {index}: {reason}. Split into one work item per row or raise the L0 abstraction level: `{work}`"
                )

        if len(rows) > 30:
            errors.append(
                f"L0 contains {len(rows)} work rows. Raise the L0 work granularity instead of compressing detail into cells (guideline: 30 rows or fewer)."
            )

    cautions = by_name.get("Cautions & Human Decisions")
    if cautions is not None and not cautions.strip():
        errors.append("Cautions & Human Decisions is blank. Write `None` if there are no items.")

    dependencies = by_name.get("Key Dependencies & Branches")
    if dependencies is not None and not dependencies.strip():
        errors.append("Key Dependencies & Branches is blank. Write `None` if there are no items.")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Simple Plan Auditor English L0 overview structure")
    parser.add_argument("plan", type=Path, help="Markdown plan file")
    args = parser.parse_args()

    text = args.plan.read_text(encoding="utf-8")
    errors = validate(text)

    if errors:
        print("OVERVIEW VALIDATION: FAIL")
        for index, error in enumerate(errors, start=1):
            print(f"{index}. {error}")
        print("\nRevise L0 and rerun validation. Do not finalize L1/L2 until L0 passes.")
        return 1

    print("OVERVIEW VALIDATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
