#!/usr/bin/env python3
"""L0 overview structure validator for simple-plan-auditor.

This intentionally performs only rough structural validation. Logical correctness
is reviewed by the agent using AUDIT_RULES.md.

設計方針:
- 見出しの番号(0.1 等)と装飾(**)は任意。名称と順序のみを規範とする。
- 階層エラーが出ても表・確認事項の検査は続行する(エラーの取りこぼしを防ぐ)。
- コードフェンス内は構造検査の対象外とする(良い例/避ける例の併記を許容するため)。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

OVERVIEW_HEADING = "## 0. 全体概要"
EXPECTED_HEADERS = ["工程", "主な作業", "確認事項"]

# 正式名称 -> 見出し本文の許容パターン(番号・装飾除去後に照合)
SECTION_PATTERNS: list[tuple[str, str]] = [
    ("目的", r"目的"),
    ("全体作業マップ", r"全体作業マップ"),
    ("要注意事項・人間判断", r"要注意事項\s*[・･/]?\s*人間判断"),
    ("重要な依存・分岐", r"重要な依存\s*[・･/]?\s*分岐"),
]
EXPECTED_SECTIONS = [name for name, _ in SECTION_PATTERNS]

H3_RE = re.compile(r"(?m)^###\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*```")
FENCE_MASK = "⟪code⟫"


def mask_fences(text: str) -> str:
    """コードフェンス内の行をプレースホルダへ置換する。

    行数と「空でないこと」は保持したまま、見出し・表の誤検出だけを防ぐ。
    """
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
    """見出し本文から番号と装飾を取り除く。"""
    t = title.strip()
    t = re.sub(r"^\**\s*", "", t)
    t = re.sub(r"\s*\**$", "", t)
    t = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", t)
    t = re.sub(r"^\**\s*", "", t).strip()
    return t


def canonical_name(title: str) -> str | None:
    normalized = normalize_heading(title)
    for name, pattern in SECTION_PATTERNS:
        if re.fullmatch(pattern, normalized):
            return name
    return None


def overview_only(text: str) -> str | None:
    m = re.search(r"(?m)^##\s+0\.?\s*全体概要\s*$", text)
    if not m:
        return None
    start = m.start()
    n = re.search(r"(?m)^# (?!#)", text[m.end():])
    end = m.end() + n.start() if n else len(text)
    return text[start:end].strip()


def split_sections(ov: str) -> list[dict[str, str | None]]:
    """L0をH3単位へ分割する。bodyはフェンスマスク済みテキストから切り出す。"""
    matches = list(H3_RE.finditer(ov))
    sections: list[dict[str, str | None]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(ov)
        sections.append(
            {
                "raw": m.group(1).strip(),
                "name": canonical_name(m.group(1)),
                "body": ov[m.end():end].strip(),
            }
        )
    return sections


def parse_table(map_body: str) -> tuple[list[list[str]], list[str]]:
    errors: list[str] = []
    lines = [line.strip() for line in map_body.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return [], ["全体作業マップにMarkdown表がありません。"]

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    headers = [re.sub(r"\*", "", c).strip() for c in cells(lines[0])]
    if headers != EXPECTED_HEADERS:
        errors.append(f"表ヘッダーは {EXPECTED_HEADERS} にしてください。現在: {headers}")

    sep = cells(lines[1])
    if len(sep) != 3 or not all(re.fullmatch(r":?-{3,}:?", c) for c in sep):
        errors.append("表の区切り行が3列のMarkdown形式になっていません。")

    rows: list[list[str]] = []
    for i, line in enumerate(lines[2:], start=1):
        row = cells(line)
        if len(row) != 3:
            errors.append(f"作業マップ {i} 行目が3列ではありません。")
            continue
        rows.append(row)

    if not rows:
        errors.append("全体作業マップに作業行がありません。")

    return rows, errors


def packing_reason(work: str) -> str | None:
    """1セルへ複数作業を詰め込んでいる疑いを粗く判定する。

    誤検出を避けるため、単独のスラッシュ(A/Bテスト, CSV/TSV 等)は許容する。
    """
    if re.search(r"<br\s*/?>", work):
        return "改行タグで複数作業を並べています"
    if re.search(r"[;；]", work):
        return "セミコロン区切りで複数作業を並べています"
    if len(re.findall(r"[/／]", work)) >= 2 or re.search(r"\s[/／]|[/／]\s", work):
        return "スラッシュ区切りで複数作業を並べています"
    return None


def validate(text: str) -> list[str]:
    errors: list[str] = []
    ov_raw = overview_only(text)
    if ov_raw is None:
        return [f"{OVERVIEW_HEADING} がありません。"]

    ov = mask_fences(ov_raw)
    sections = split_sections(ov)

    # 階層チェック(番号・装飾は任意、名称と順序は必須)
    actual = [s["name"] or f"?{s['raw']}" for s in sections]
    if actual != EXPECTED_SECTIONS:
        errors.append(
            "L0の階層は次の4セクションだけを、この順序で配置してください: "
            + " → ".join(EXPECTED_SECTIONS)
            + f"。現在: {actual}"
        )

    # 階層エラーがあっても、見つかった節の内容検査は続行する。
    by_name = {s["name"]: s["body"] for s in sections if s["name"]}

    purpose = by_name.get("目的")
    if purpose is not None and not purpose.strip():
        errors.append("目的が空です。")

    map_body = by_name.get("全体作業マップ")
    if map_body is not None:
        rows, table_errors = parse_table(map_body)
        errors.extend(table_errors)

        for idx, row in enumerate(rows, start=1):
            phase, work, checks = row
            if not phase:
                errors.append(f"作業マップ {idx} 行目: 工程が空です。")
            if not work:
                errors.append(f"作業マップ {idx} 行目: 主な作業が空です。")
            if not checks:
                errors.append(
                    f"作業マップ {idx} 行目: 確認事項が空です。確認事項がなければ `なし` と記載してください。"
                )

            reason = packing_reason(work)
            if reason:
                errors.append(
                    f"作業マップ {idx} 行目: {reason}。1作業1行へ分割するか、L0の粒度を一段上位化してください: `{work}`"
                )

        if len(rows) > 30:
            errors.append(
                f"L0の作業行が {len(rows)} 行あります。詳細を1セルへ詰めず、L0へ載せる作業粒度を一段上げてください（目安30行以内）。"
            )

    caution = by_name.get("要注意事項・人間判断")
    if caution is not None and not caution.strip():
        errors.append("要注意事項・人間判断が空です。該当事項がなければ `なし` と明記してください。")

    dependency = by_name.get("重要な依存・分岐")
    if dependency is not None and not dependency.strip():
        errors.append("重要な依存・分岐が空です。特記事項がなければ `なし` と明記してください。")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate simple-plan-auditor L0 overview structure")
    parser.add_argument("plan", type=Path, help="Markdown plan file")
    args = parser.parse_args()

    text = args.plan.read_text(encoding="utf-8")
    errors = validate(text)

    if errors:
        print("OVERVIEW VALIDATION: FAIL")
        for i, error in enumerate(errors, start=1):
            print(f"{i}. {error}")
        print("\nL0を修正し、再度バリデーションしてください。L1/L2の確定へ進まないでください。")
        return 1

    print("OVERVIEW VALIDATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
