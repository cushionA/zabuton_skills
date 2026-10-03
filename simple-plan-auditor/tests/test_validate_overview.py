import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_overview", SKILL_ROOT / "scripts" / "validate_overview.py"
)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def plan(work="正常系を検証", checks="なし", purpose="正常に登録できる状態にする。"):
    return f"""## 0. 全体概要

### 0.1 目的

{purpose}

### 0.2 全体作業マップ

| 工程 | 主な作業 | 確認事項 |
|---|---|---|
| 検証 | {work} | {checks} |

### 0.3 要注意事項・人間判断

なし

### 0.4 重要な依存・分岐

なし
"""


class ValidateOverviewTests(unittest.TestCase):
    def test_existing_samples_pass(self):
        for name in ("sample-plan.md", "sample-plan-revision.md"):
            with self.subTest(sample=name):
                sample = (SKILL_ROOT / "examples" / name).read_text(encoding="utf-8")
                self.assertEqual(validator.validate(sample), ([], []))

    def test_fenced_example_does_not_hide_invalid_real_overview(self):
        text = f"```markdown\n{plan()}```\n\n# 実際の計画\n\n{plan(checks='')}"
        errors, _ = validator.validate(text)
        self.assertTrue(any("確認事項が空" in error for error in errors), errors)

    def test_fenced_example_is_not_a_real_overview(self):
        errors, _ = validator.validate(f"```markdown\n{plan()}```\n")
        self.assertTrue(any("全体概要 がありません" in error for error in errors), errors)

    def test_fenced_top_level_heading_does_not_truncate_overview(self):
        text = plan(purpose="正常に登録できる状態にする。\n\n```markdown\n# 見出しの例\n```")
        self.assertEqual(validator.validate(text), ([], []))

    def test_real_top_level_heading_ends_overview(self):
        text = plan() + "\n# Phase 1\n\n### 詳細手順\n\n処理を確認する。\n"
        self.assertEqual(validator.validate(text), ([], []))

    def test_escaped_pipes_do_not_add_columns(self):
        for checks in (r"A\|Bを扱えるか", r"区切り文字は\|", r"バックスラッシュとパイプは\\\|を扱えるか"):
            with self.subTest(checks=checks):
                self.assertEqual(validator.validate(plan(checks=checks)), ([], []))

    def test_unescaped_pipe_adds_a_column(self):
        for checks in ("条件A | 条件B", r"バックスラッシュ\\|条件B"):
            with self.subTest(checks=checks):
                errors, _ = validator.validate(plan(checks=checks))
                self.assertTrue(any("3列ではありません" in error for error in errors), errors)

    def test_empty_checks_fail(self):
        errors, _ = validator.validate(plan(checks=""))
        self.assertTrue(any("確認事項が空" in error for error in errors), errors)

    def test_three_or_more_comma_separated_tasks_warn(self):
        for work in ("設計する、実装する、検証する", "調査する、設計する、実装する、検証する"):
            with self.subTest(work=work):
                errors, warnings = validator.validate(plan(work=work))
                self.assertEqual(errors, [])
                self.assertTrue(any("読点" in warning for warning in warnings), warnings)

    def test_two_comma_separated_items_remain_allowed(self):
        self.assertEqual(validator.validate(plan(work="入力を確認し、正常系を検証")), ([], []))

    def test_detail_link_destinations_do_not_count_as_tasks(self):
        for work in (
            "[正常系を検証](details/validation/normal.md)",
            "[API [v2] を検証](details/validation/api.md)",
            "[正常系を検証](details/(draft)/validation/normal.md)",
            r"[正常系を検証](details/\(draft\)/validation/normal.md)",
            "[正常系を検証](https://example.com/details/validation/normal.md)",
            '[正常系を検証](details/validation/normal.md "詳細手順")',
        ):
            with self.subTest(work=work):
                self.assertEqual(validator.validate(plan(work=work)), ([], []))

    def test_link_labels_are_still_checked_for_multiple_tasks(self):
        for label in ("設計する、実装する、検証する", "[旧版]を調査、実装する、検証する", "設計; 実装", "設計/実装/検証", "設計<br>実装"):
            with self.subTest(label=label):
                errors, warnings = validator.validate(plan(work=f"[{label}](details/normal.md)"))
                self.assertEqual(errors, [])
                self.assertTrue(any("疑い" in warning for warning in warnings), warnings)

    def test_existing_task_separators_warn(self):
        for work in ("設計; 実装", "設計；実装", "設計/実装/検証", "設計／実装／検証", "設計 / 実装", "設計<br>実装"):
            with self.subTest(work=work):
                errors, warnings = validator.validate(plan(work=work))
                self.assertEqual(errors, [])
                self.assertTrue(any("複数作業" in warning for warning in warnings), warnings)

    def test_single_slash_in_a_term_remains_allowed(self):
        for work in ("A/Bテストを実施", "CSV/TSVの入力を検証"):
            with self.subTest(work=work):
                self.assertEqual(validator.validate(plan(work=work)), ([], []))

    def test_more_than_thirty_rows_warn(self):
        row = "| 検証 | 正常系を検証 | なし |"
        for count in (30, 31):
            with self.subTest(count=count):
                text = plan().replace(row, "\n".join([row] * count))
                errors, warnings = validator.validate(text)
                self.assertEqual(errors, [])
                if count == 30:
                    self.assertEqual(warnings, [])
                else:
                    self.assertTrue(any("31 行" in warning for warning in warnings), warnings)

    def test_errors_and_warnings_are_both_returned(self):
        errors, warnings = validator.validate(plan(work="設計; 実装", checks=""))
        self.assertTrue(any("確認事項が空" in error for error in errors), errors)
        self.assertTrue(any("セミコロン" in warning for warning in warnings), warnings)

    def test_cli_exit_status_and_report(self):
        for text, expected_exit, expected_status, has_error, has_warning in (
            (plan(), 0, "PASS", False, False),
            (plan(work="設計; 実装"), 0, "PASS", False, True),
            (plan(checks=""), 1, "FAIL", True, False),
            (plan(work="設計; 実装", checks=""), 1, "FAIL", True, True),
        ):
            with self.subTest(expected_status=expected_status, has_warning=has_warning):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "plan.md"
                    path.write_text(text, encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, "-X", "utf8", "-B", str(SKILL_ROOT / "scripts" / "validate_overview.py"), str(path)],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                    )
                self.assertEqual(result.returncode, expected_exit, result.stderr)
                self.assertIn(f"OVERVIEW STRUCTURE: {expected_status}", result.stdout)
                self.assertEqual("ERROR 1." in result.stdout, has_error)
                self.assertEqual("WARNING 1." in result.stdout, has_warning)
                self.assertIn("計画の妥当性や着手可否は判定していません", result.stdout)
                self.assertNotIn("進まないでください", result.stdout)

    def test_cli_output_is_utf8_without_utf8_mode(self):
        env = {key: value for key, value in os.environ.items() if key not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.md"
            path.write_text(plan(work="✓設計; 実装"), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(SKILL_ROOT / "scripts" / "validate_overview.py"), str(path)],
                capture_output=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("✓設計; 実装", result.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
