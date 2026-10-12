import copy
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import llm


SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["deep_dive_value", "large_effect"]},
        "evidence": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "scores": {
                        "type": "array",
                        "items": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
                "required": ["quote", "scores"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["category", "evidence"],
    "additionalProperties": False,
}
VALID = {
    "category": "deep_dive_value",
    "evidence": [{"quote": "原文の根拠", "scores": [0, 0.75, 1]}],
}


class VerdictValidationTests(unittest.TestCase):
    def test_valid_nested_payload(self):
        self.assertTrue(llm._valid(VALID, SCHEMA))

    def test_invalid_payloads_are_rejected(self):
        cases = {}

        value = copy.deepcopy(VALID)
        value["category"] = "unreviewed"
        cases["enum"] = value

        value = copy.deepcopy(VALID)
        value["evidence"][0]["scores"] = ["0.75"]
        cases["nested_array_type"] = value

        value = copy.deepcopy(VALID)
        value["evidence"][0]["scores"] = [True]
        cases["boolean_is_not_number"] = value

        value = copy.deepcopy(VALID)
        value["evidence"][0]["scores"] = [1.01]
        cases["numeric_bound"] = value

        value = copy.deepcopy(VALID)
        value["evidence"] = []
        cases["min_items"] = value

        value = copy.deepcopy(VALID)
        del value["category"]
        cases["required"] = value

        value = copy.deepcopy(VALID)
        del value["evidence"][0]["quote"]
        cases["nested_required"] = value

        value = copy.deepcopy(VALID)
        value["extra"] = "unexpected"
        cases["extra_key"] = value

        value = copy.deepcopy(VALID)
        value["evidence"][0]["extra"] = "unexpected"
        cases["nested_extra_key"] = value

        for name, value in cases.items():
            with self.subTest(name=name):
                self.assertFalse(llm._valid(value, SCHEMA))

    def test_verdict_must_remain_an_object(self):
        self.assertFalse(llm._valid(None, {"type": "null"}))
        self.assertFalse(llm._valid(["result"], {"type": "array"}))

    def test_invalid_schema_fails_before_backend_selection(self):
        for schema in ({"type": "not-a-json-schema-type"}, []):
            with self.subTest(schema=schema), patch.object(llm, "backend") as backend:
                with self.assertRaises(llm.jsonschema.SchemaError):
                    llm.judge("instruction", "payload", schema)
                backend.assert_not_called()

    def test_both_backends_use_full_validation(self):
        invalid = copy.deepcopy(VALID)
        invalid["evidence"][0]["scores"] = ["not a number"]
        for backend_name in ("claude", "codex"):
            for verdict, expected in ((VALID, VALID), (invalid, None), (None, None)):
                with self.subTest(backend=backend_name, verdict=verdict):
                    with patch.object(llm, "backend", return_value=backend_name), patch.object(
                        llm, "_judge_claude", return_value=verdict
                    ) as claude, patch.object(llm, "_judge_codex", return_value=verdict) as codex:
                        actual = llm.judge("instruction", "payload", SCHEMA)
                    self.assertEqual(actual, expected)
                    selected, other = (claude, codex) if backend_name == "claude" else (codex, claude)
                    selected.assert_called_once()
                    other.assert_not_called()


class ClaudeTransportTests(unittest.TestCase):
    def test_full_schema_and_payload_use_stdin_with_tools_disabled(self):
        schema = copy.deepcopy(SCHEMA)
        schema["description"] = "全文保持の確認\n<>&|^ %PATH%"
        payload = "[data]\n原文 <>&|^ %PATH%\n[/data]"
        response = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"result": json.dumps(VALID)})
        )
        with patch.object(llm, "claude_path", return_value="claude.CMD"), patch.object(
            llm.subprocess, "run", return_value=response
        ) as run:
            actual = llm._judge_claude("判定\n<>&|^", payload, schema, "test-model", 17)

        self.assertEqual(actual, VALID)
        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--model") + 1], "test-model")
        system_prompt = command[command.index("--system-prompt") + 1]
        self.assertIsNone(llm.UNSAFE_ARGV.search(system_prompt))
        self.assertNotIn(schema["description"], system_prompt)
        self.assertNotIn(payload, command)
        self.assertEqual(json.loads(options["input"]), {
            "output_schema": schema,
            "judgment_input": payload,
        })
        self.assertEqual(options["timeout"], 17)


class DependencyTests(unittest.TestCase):
    def test_missing_dependency_stops_import_with_installation_hint(self):
        spec = importlib.util.spec_from_file_location("radar_llm_without_dependency", SCRIPTS / "llm.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"jsonschema": None}):
            with self.assertRaisesRegex(ImportError, "pip install -r research-repro-radar/requirements.txt"):
                spec.loader.exec_module(module)


if __name__ == "__main__":
    unittest.main()
