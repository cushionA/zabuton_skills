import copy
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import llm
import native_judge


SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["deep_dive_value", "large_effect"]},
        "evidence": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "scores": {"type": "array", "items": {
                        "type": "number", "minimum": 0, "maximum": 1,
                    }},
                },
                "required": ["quote", "scores"], "additionalProperties": False,
            },
        },
    },
    "required": ["category", "evidence"], "additionalProperties": False,
}
VALID = {"category": "deep_dive_value",
         "evidence": [{"quote": "原文の根拠", "scores": [0, 0.75, 1]}]}
INSTRUCTION = "判定\n<>&|^ %PATH% `literal` $(literal)"
PAYLOAD = "[data]\r\n原文 <>&|^ %PATH%\n[/data]"


def response_for(request, verdict=VALID):
    return {"request_id": request["request_id"], "task_sha256": request["task_sha256"],
            "verdict": copy.deepcopy(verdict)}


class NativeEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.request = native_judge.prepare_request(INSTRUCTION, PAYLOAD, SCHEMA)

    def assert_failure(self, result, stage=None, failure=None):
        self.assertEqual(set(result), {"verdict", "diagnostics"})
        self.assertIsNone(result["verdict"])
        self.assertEqual(set(result["diagnostics"]), {"stage", "failure", "backend"})
        self.assertEqual(result["diagnostics"]["backend"], "native")
        self.assertIsNotNone(result["diagnostics"]["failure"])
        if stage is not None:
            self.assertEqual(result["diagnostics"]["stage"], stage)
        if failure is not None:
            self.assertEqual(result["diagnostics"]["failure"], failure)
        # These are adversarial inputs; diagnostics must not echo any of them.
        self.assertNotIn("literal", json.dumps(result))
        self.assertNotIn("原文", json.dumps(result, ensure_ascii=False))

    def test_full_content_and_fresh_uuid_with_stable_canonical_hash(self):
        other = native_judge.prepare_request(INSTRUCTION, PAYLOAD, copy.deepcopy(SCHEMA))
        self.assertEqual(set(self.request), native_judge.REQUEST_KEYS)
        self.assertEqual(self.request["instruction"], INSTRUCTION)
        self.assertEqual(self.request["payload"], PAYLOAD)
        self.assertEqual(self.request["schema"], SCHEMA)
        self.assertEqual(str(uuid.UUID(self.request["request_id"])), self.request["request_id"])
        self.assertNotEqual(self.request["request_id"], other["request_id"])
        self.assertEqual(self.request["task_sha256"], other["task_sha256"])
        task = {key: self.request[key] for key in native_judge.TASK_KEYS}
        canonical = json.dumps(task, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.assertEqual(self.request["task_sha256"], hashlib.sha256(canonical).hexdigest())
        reordered = dict(reversed(list(SCHEMA.items())))
        self.assertEqual(native_judge.prepare_request(INSTRUCTION, PAYLOAD, reordered)["task_sha256"],
                         self.request["task_sha256"])

    def test_request_owns_schema_and_changed_task_changes_hash(self):
        schema = copy.deepcopy(SCHEMA)
        request = native_judge.prepare_request(INSTRUCTION, PAYLOAD, schema)
        schema["description"] = "later mutation"
        self.assertEqual(request["schema"], SCHEMA)
        self.assertNotEqual(native_judge.prepare_request(INSTRUCTION + " ", PAYLOAD, SCHEMA)["task_sha256"],
                            self.request["task_sha256"])
        self.assertNotEqual(native_judge.prepare_request(INSTRUCTION, PAYLOAD + " ", SCHEMA)["task_sha256"],
                            self.request["task_sha256"])
        self.assertNotEqual(native_judge.prepare_request(INSTRUCTION, PAYLOAD, schema)["task_sha256"],
                            self.request["task_sha256"])

    def test_valid_nested_verdict_calls_full_llm_validator(self):
        with patch.object(llm, "_valid", wraps=llm._valid) as valid:
            result = native_judge.validate_response(self.request, response_for(self.request))
        self.assertEqual(result, {"verdict": VALID, "diagnostics": {
            "stage": "complete", "failure": None, "backend": "native",
        }})
        valid.assert_called_once_with(VALID, SCHEMA)

    def test_schema_violations_and_extra_nested_fields_fail(self):
        cases = []
        for category in ("unknown", None, 3):
            verdict = copy.deepcopy(VALID)
            verdict["category"] = category
            cases.append(verdict)
        for score in ("0.5", True, -0.1, 1.1):
            verdict = copy.deepcopy(VALID)
            verdict["evidence"][0]["scores"] = [score]
            cases.append(verdict)
        for location in ("outer", "nested"):
            verdict = copy.deepcopy(VALID)
            target = verdict if location == "outer" else verdict["evidence"][0]
            target["unexpected"] = "literal"
            cases.append(verdict)
        for missing in ("category", "quote"):
            verdict = copy.deepcopy(VALID)
            target = verdict if missing == "category" else verdict["evidence"][0]
            del target[missing]
            cases.append(verdict)
        empty = copy.deepcopy(VALID)
        empty["evidence"] = []
        cases.extend([empty, {}, [], None, True, "literal"])
        for verdict in cases:
            with self.subTest(verdict=verdict):
                self.assert_failure(native_judge.validate_response(
                    self.request, response_for(self.request, verdict)),
                    "schema_validation", "schema_violation")

    def test_nonfinite_verdicts_and_python_only_values_fail(self):
        for invalid in (float("nan"), float("inf"), float("-inf"), (1,), {1: "literal"}):
            verdict = copy.deepcopy(VALID)
            verdict["evidence"][0]["scores"] = [invalid]
            with self.subTest(value=invalid):
                self.assert_failure(native_judge.validate_response(
                    self.request, response_for(self.request, verdict)),
                    "response_validation", "invalid_response")

    def test_boolean_schema_retains_object_verdict_contract(self):
        for schema, verdict, expected in ((True, {"any": 1}, {"any": 1}),
                                          (True, [], None), (False, {}, None)):
            with self.subTest(schema=schema, verdict=verdict):
                request = native_judge.prepare_request(INSTRUCTION, PAYLOAD, schema)
                result = native_judge.validate_response(request, response_for(request, verdict))
                self.assertEqual(result["verdict"], expected)

    def test_invalid_schemas_fail_before_uuid_allocation(self):
        schemas = (["literal"], None, 1, {"type": "invalid-type"},
                   {"type": "number", "minimum": math.nan},
                   {"type": "object", "description": ("literal",)},
                   {"$schema": ["literal"]}, {1: "literal"},
                   {"$ref": "https://literal.invalid/schema"})
        for schema in schemas:
            with self.subTest(schema=schema), patch.object(native_judge.uuid, "uuid4") as make_uuid:
                with self.assertRaises(llm.jsonschema.SchemaError):
                    native_judge.prepare_request(INSTRUCTION, PAYLOAD, schema)
                make_uuid.assert_not_called()

    def test_invalid_inputs_and_purpose_fail(self):
        for instruction, payload, purpose in (([], PAYLOAD, "candidate"),
                                               (INSTRUCTION, {}, "candidate"),
                                               (INSTRUCTION, PAYLOAD, "literal"),
                                               (INSTRUCTION, PAYLOAD, None),
                                               ("\ud800", PAYLOAD, "candidate")):
            with self.subTest(purpose=purpose), self.assertRaises(ValueError):
                native_judge.prepare_request(instruction, payload, SCHEMA, purpose)

    def test_malformed_requests_fail_closed(self):
        cases = [None, [], "literal", {}, {**self.request, "extra": "literal"}]
        for key in self.request:
            incomplete = copy.deepcopy(self.request)
            del incomplete[key]
            cases.append(incomplete)
        bad_values = {"version": (True, 1.0, 0, 2, "1"),
                      "request_id": (None, 123, "literal", self.request["request_id"].upper()),
                      "instruction": (None, []), "payload": (None, {}),
                      "purpose": (None, "literal"), "task_sha256": (None, "literal", "0" * 65)}
        for key, values in bad_values.items():
            for value in values:
                cases.append({**self.request, key: value})
        for request in cases:
            with self.subTest(request=request):
                self.assert_failure(native_judge.validate_response(request, response_for(self.request)),
                                    "request_validation", "invalid_request")

    def test_tampered_content_and_hash_fail_closed(self):
        cases = [{**self.request, "instruction": INSTRUCTION + "literal"},
                 {**self.request, "payload": PAYLOAD + "literal"},
                 {**self.request, "schema": True},
                 {**self.request, "task_sha256": "0" * 64}]
        for request in cases:
            with self.subTest(request=request):
                self.assert_failure(native_judge.validate_response(request, response_for(self.request)),
                                    "request_validation", "task_hash_mismatch")

    def test_bad_request_schema_fails_masked(self):
        for schema in (["literal"], {"type": "literal"}, {"$ref": "https://literal.invalid"}):
            request = {**self.request, "schema": schema}
            with self.subTest(schema=schema):
                self.assert_failure(native_judge.validate_response(request, response_for(self.request)),
                                    "schema_validation", "invalid_schema")

    def test_request_nonfinite_and_surrogates_fail_masked(self):
        for request in ({**self.request, "payload": "\ud800"},
                        {**self.request, "schema": {"minimum": float("inf")}}):
            self.assert_failure(native_judge.validate_response(request, response_for(self.request)),
                                "request_validation", "invalid_request")

    def test_malformed_responses_fail_closed(self):
        response = response_for(self.request)
        cases = [None, [], "literal", {}, {**response, "extra": "literal"}]
        for key in response:
            partial = copy.deepcopy(response)
            del partial[key]
            cases.append(partial)
        for value in cases:
            with self.subTest(response=value):
                self.assert_failure(native_judge.validate_response(self.request, value),
                                    "response_validation", "invalid_response")

    def test_stale_or_different_response_identity_fails(self):
        fresh = native_judge.prepare_request(INSTRUCTION, PAYLOAD, SCHEMA)
        different = native_judge.prepare_request(INSTRUCTION, PAYLOAD + "x", SCHEMA)
        cases = [response_for(fresh), response_for(different)]
        for key in ("request_id", "task_sha256"):
            for value in (None, 5, "literal", "非ASCII", "0" * 64):
                cases.append({**response_for(self.request), key: value})
        for response in cases:
            with self.subTest(response=response):
                self.assert_failure(native_judge.validate_response(self.request, response),
                                    "identity_validation", "identity_mismatch")

    def test_internal_schema_ref_validates_without_network(self):
        schema = {"$defs": {"answer": SCHEMA}, "$ref": "#/$defs/answer"}
        request = native_judge.prepare_request(INSTRUCTION, PAYLOAD, schema)
        self.assertEqual(native_judge.validate_response(request, response_for(request))["verdict"], VALID)

    def test_schema_ref_names_and_literal_data_remain_arbitrary_json(self):
        literal = {"$ref": "https://literal.invalid"}
        schemas = [
            {"type": "object", "properties": {"$ref": {"type": "string"}}},
            {"type": "object", "const": literal},
            {"type": "object", "enum": [literal]},
            {"type": "object", "default": literal, "examples": [literal]},
            {"$defs": {"$ref": {"type": "object"}}, "$ref": "#/$defs/$ref"},
        ]
        for schema in schemas:
            with self.subTest(schema=schema):
                request = native_judge.prepare_request(INSTRUCTION, PAYLOAD, schema)
                self.assertEqual(native_judge.validate_response(request, response_for(request, literal))["verdict"],
                                 literal)

    def test_external_refs_in_nested_schema_positions_are_rejected(self):
        external = {"$ref": "https://literal.invalid"}
        schemas = [
            {"$defs": {"answer": external}},
            {"properties": {"answer": external}},
            {"additionalProperties": external},
            {"if": external}, {"items": external},
            {"allOf": [external]}, {"prefixItems": [external]},
            {"dependencies": {"answer": external}},
            {"dependentSchemas": {"answer": external}},
        ]
        for schema in schemas:
            with self.subTest(schema=schema), self.assertRaises(llm.jsonschema.SchemaError):
                native_judge.prepare_request(INSTRUCTION, PAYLOAD, schema)

    def test_local_refs_targeting_external_schema_in_annotation_never_fetch(self):
        for annotation in ("default", "examples", "const"):
            external = {"$ref": "https://literal.invalid/schema"}
            value = [external] if annotation == "examples" else external
            target = "#/examples/0" if annotation == "examples" else "#/" + annotation
            schema = {annotation: value, "$ref": target}
            request = native_judge.prepare_request(INSTRUCTION, PAYLOAD, schema)
            with self.subTest(annotation=annotation), \
                    patch.object(urllib.request, "urlopen", side_effect=AssertionError("network forbidden")) as network, \
                    patch.object(llm, "_valid") as valid:
                self.assert_failure(native_judge.validate_response(request, response_for(request)),
                                    "schema_validation", "schema_violation")
            network.assert_not_called()
            valid.assert_not_called()

    def test_unresolved_refs_and_validator_errors_are_masked(self):
        request = native_judge.prepare_request(INSTRUCTION, PAYLOAD, {"$ref": "#/$defs/missing"})
        self.assert_failure(native_judge.validate_response(request, response_for(request)),
                            "schema_validation", "schema_violation")
        for exc in (RuntimeError("literal secret path"), RecursionError("literal secret")):
            with patch.object(llm, "_valid", side_effect=exc):
                self.assert_failure(native_judge.validate_response(self.request, response_for(self.request)),
                                    "schema_validation", "schema_violation")

    def test_probe_schema_and_semantic_values_are_exact(self):
        request = native_judge.prepare_request(native_judge.PROBE_INSTRUCTION,
                                              native_judge.PROBE_PAYLOAD, llm.PROBE_SCHEMA, "probe")
        valid = {"ok": True, "note": "ready"}
        self.assertEqual(native_judge.validate_response(request, response_for(request, valid))["verdict"], valid)
        for verdict in ({"ok": False, "note": "ready"}, {"ok": True, "note": "literal"}):
            self.assert_failure(native_judge.validate_response(request, response_for(request, verdict)),
                                "probe_validation", "probe_mismatch")
        with self.assertRaises(llm.jsonschema.SchemaError):
            native_judge.prepare_request(INSTRUCTION, PAYLOAD, True, "probe")
        tampered = {**request, "schema": True}
        self.assert_failure(native_judge.validate_response(tampered, response_for(request, valid)),
                            "schema_validation", "invalid_probe_schema")

    def test_apis_do_not_launch_processes_or_touch_state_or_environment(self):
        state_dir = SCRIPTS.parent / "state"
        before = {path.name: path.read_bytes() for path in state_dir.iterdir() if path.is_file()}
        environment = dict(os.environ)
        with patch.object(llm.subprocess, "run", side_effect=AssertionError("process forbidden")), \
                patch.object(llm.state, "_read", side_effect=AssertionError("state forbidden")), \
                patch.object(llm.state, "_write", side_effect=AssertionError("state forbidden")):
            request = native_judge.prepare_request(INSTRUCTION, PAYLOAD, SCHEMA)
            self.assertEqual(native_judge.validate_response(request, response_for(request))["verdict"], VALID)
            self.assert_failure(native_judge.validate_response(request, None))
        self.assertEqual(dict(os.environ), environment)
        self.assertEqual({path.name: path.read_bytes() for path in state_dir.iterdir() if path.is_file()}, before)


class NativeCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.instruction = self.directory / "instruction.txt"
        self.payload = self.directory / "payload.txt"
        self.schema = self.directory / "schema.json"
        self.request = self.directory / "request.json"
        self.response = self.directory / "response.json"
        self.instruction.write_bytes(INSTRUCTION.encode("utf-8"))
        self.payload.write_bytes(PAYLOAD.encode("utf-8"))
        self.schema.write_text(json.dumps(SCHEMA), encoding="utf-8")

    def prepare_args(self):
        return ["prepare", "--instruction-file", str(self.instruction), "--payload-file", str(self.payload),
                "--schema-file", str(self.schema), "--request-file", str(self.request)]

    def validate_args(self):
        return ["validate", "--request-file", str(self.request), "--response-file", str(self.response)]

    def call_main(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = native_judge.main(args)
        self.assertEqual(stderr.getvalue(), "")
        return code, json.loads(stdout.getvalue())

    def assert_masked_failure(self, args, expected=None):
        code, result = self.call_main(args)
        self.assertEqual(code, 1)
        self.assertIsNone(result["verdict"])
        self.assertEqual(result["diagnostics"]["backend"], "native")
        if expected:
            self.assertEqual(result["diagnostics"]["failure"], expected)
        output = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(str(self.directory), output)
        self.assertNotIn("literal", output)
        self.assertNotIn("原文", output)
        return result

    def write_request_and_response(self, verdict=VALID):
        request = native_judge.prepare_request(INSTRUCTION, PAYLOAD, SCHEMA)
        self.request.write_text(json.dumps(request), encoding="utf-8")
        self.response.write_text(json.dumps(response_for(request, verdict)), encoding="utf-8")
        return request

    def test_prepare_writes_exclusive_private_request_and_masked_summary(self):
        code, result = self.call_main(self.prepare_args())
        self.assertEqual(code, 0)
        request = native_judge.load_json_file(self.request)
        self.assertEqual(request["instruction"], INSTRUCTION)
        self.assertEqual(request["payload"], PAYLOAD)
        self.assertEqual(result["request_id"], request["request_id"])
        self.assertEqual(result["task_sha256"], request["task_sha256"])
        self.assertEqual(result["diagnostics"]["stage"], "request_prepared")
        self.assertNotIn("literal", json.dumps(result))
        if os.name != "nt":
            self.assertEqual(self.request.stat().st_mode & 0o777, 0o600)
        content = self.request.read_bytes()
        self.assert_masked_failure(self.prepare_args(), "request_exists")
        self.assertEqual(self.request.read_bytes(), content)

    def test_prepare_probe_uses_built_in_content_without_input_paths(self):
        code, result = self.call_main(["prepare", "--purpose", "probe", "--request-file", str(self.request)])
        self.assertEqual(code, 0)
        request = native_judge.load_json_file(self.request)
        self.assertEqual(request["purpose"], "probe")
        self.assertEqual(request["instruction"], native_judge.PROBE_INSTRUCTION)
        self.assertEqual(request["payload"], native_judge.PROBE_PAYLOAD)
        self.assertEqual(request["schema"], llm.PROBE_SCHEMA)
        self.assertEqual(result["diagnostics"]["backend"], "native")

    def test_argument_errors_are_masked_and_return_one(self):
        for args in ([], ["literal"], ["prepare"],
                     ["prepare", "--request-file", str(self.request)],
                     ["prepare", "--request-file", str(self.request), "--purpose", "literal"],
                     ["prepare", "--request-file", str(self.request), "--instruction-file", str(self.instruction),
                      "--purpose", "probe"], ["validate", "--request-file", str(self.request)],
                     ["validate", "--literal", str(self.directory)]):
            with self.subTest(args=args):
                self.assert_masked_failure(args, "invalid_arguments")
        self.assertFalse(self.request.exists())

    def test_missing_dependency_is_masked_for_both_commands(self):
        commands = (self.prepare_args(),
                    ["prepare", "--purpose", "probe", "--request-file", str(self.request)],
                    self.validate_args())
        for args in commands:
            with self.subTest(args=args), \
                    patch.object(native_judge, "_llm", side_effect=ImportError("literal private path")):
                self.assert_masked_failure(args, "backend_unavailable")
        self.assertFalse(self.request.exists())

    def test_missing_invalid_instruction_payload_or_schema_creates_no_output(self):
        for path in (self.instruction, self.payload, self.schema):
            content = path.read_bytes()
            with self.subTest(path=path, kind="missing"):
                path.unlink()
                self.assert_masked_failure(self.prepare_args(), "input_unavailable")
                self.assertFalse(self.request.exists())
            with self.subTest(path=path, kind="utf8"):
                path.write_bytes(b"\xffliteral")
                self.assert_masked_failure(self.prepare_args(), "input_unavailable")
                self.assertFalse(self.request.exists())
            path.write_bytes(content)
        for content in ('{"literal":', '{"minimum":NaN}', '{"minimum":1e999}',
                        '{"type":"object","type":"string"}'):
            with self.subTest(schema=content):
                self.schema.write_text(content, encoding="utf-8")
                self.assert_masked_failure(self.prepare_args(), "input_unavailable")
                self.assertFalse(self.request.exists())

    def test_invalid_schema_and_unwritable_output_are_masked(self):
        self.schema.write_text('{"type":"literal"}', encoding="utf-8")
        self.assert_masked_failure(self.prepare_args(), "invalid_schema")
        self.assertFalse(self.request.exists())
        self.schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        args = self.prepare_args()
        args[-1] = str(self.directory / "literal" / "request.json")
        self.assert_masked_failure(args, "output_unavailable")

    def test_validate_normal_response_and_schema_failure(self):
        self.write_request_and_response()
        code, result = self.call_main(self.validate_args())
        self.assertEqual(code, 0)
        self.assertEqual(result["verdict"], VALID)
        self.write_request_and_response({"category": "literal"})
        self.assert_masked_failure(self.validate_args(), "schema_violation")

    def test_validate_missing_invalid_and_nonfinite_files_are_masked(self):
        self.assert_masked_failure(self.validate_args(), "input_unavailable")
        self.write_request_and_response()
        self.response.unlink()
        result = self.assert_masked_failure(self.validate_args(), "input_unavailable")
        self.assertEqual(result["diagnostics"]["stage"], "response_input")
        for path in (self.request, self.response):
            for content in (b"literal", b"\xffliteral", b'{"x":NaN}', b'{"x":1e999}',
                            b'{"x":1,"x":2}', b'{"x":{"nested":1,"nested":2}}'):
                self.write_request_and_response()
                path.write_bytes(content)
                with self.subTest(path=path, content=content):
                    self.assert_masked_failure(self.validate_args(), "input_unavailable")

    def test_validate_tampered_stale_and_wrong_envelope_fail(self):
        request = self.write_request_and_response()
        self.response.write_text(json.dumps(response_for(native_judge.prepare_request(INSTRUCTION, PAYLOAD, SCHEMA))),
                                 encoding="utf-8")
        self.assert_masked_failure(self.validate_args(), "identity_mismatch")
        self.request.write_text(json.dumps({**request, "payload": "literal"}), encoding="utf-8")
        self.assert_masked_failure(self.validate_args(), "task_hash_mismatch")
        self.write_request_and_response()
        self.response.write_text(json.dumps(VALID), encoding="utf-8")
        self.assert_masked_failure(self.validate_args(), "invalid_response")

    def test_cli_changes_only_explicit_request_file(self):
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        with patch.object(llm.subprocess, "run", side_effect=AssertionError("process forbidden")), \
                patch.object(llm.state, "_read", side_effect=AssertionError("state forbidden")), \
                patch.object(llm.state, "_write", side_effect=AssertionError("state forbidden")):
            self.assertEqual(self.call_main(self.prepare_args())[0], 0)
        after = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        self.assertEqual(set(after) - set(before), {"request.json"})
        self.assertEqual({key: value for key, value in after.items() if key in before}, before)

    def test_executable_entrypoint_exit_codes(self):
        command = [sys.executable, str(SCRIPTS / "native_judge.py")]
        prepare = subprocess.run(command + self.prepare_args(), capture_output=True, text=True, cwd=self.directory)
        self.assertEqual(prepare.returncode, 0, prepare.stdout + prepare.stderr)
        self.assertEqual(prepare.stderr, "")
        request = native_judge.load_json_file(self.request)
        self.response.write_text(json.dumps(response_for(request)), encoding="utf-8")
        valid = subprocess.run(command + self.validate_args(), capture_output=True, text=True, cwd=self.directory)
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        self.assertEqual(json.loads(valid.stdout)["verdict"], VALID)
        self.assertEqual(valid.stderr, "")
        missing = subprocess.run(command + ["validate", "--request-file", "literal-private-path"],
                                 capture_output=True, text=True, cwd=self.directory)
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(missing.stderr, "")
        self.assertNotIn("literal", missing.stdout)


class NativeJudgeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.request = native_judge.prepare_request(INSTRUCTION, PAYLOAD, SCHEMA)
        self.response = response_for(self.request)

    def call_judge(self, instruction=INSTRUCTION, payload=PAYLOAD, schema=SCHEMA, **kwargs):
        with patch.object(llm, "backend", return_value="native"), \
                patch.object(llm.subprocess, "run", side_effect=AssertionError("process forbidden")) as run, \
                patch.object(llm, "_judge_claude") as claude, patch.object(llm, "_judge_codex") as codex:
            result = llm.judge_with_diagnostics(instruction, payload, schema, **kwargs)
        run.assert_not_called()
        claude.assert_not_called()
        codex.assert_not_called()
        self.assertEqual(result["diagnostics"]["backend"], "native")
        return result

    def test_schema_valid_native_envelope_returns_dict(self):
        self.assertEqual(self.call_judge(native_request=self.request, native_response=self.response)["verdict"], VALID)
        with patch.object(llm, "backend", return_value="native"):
            self.assertEqual(llm.judge(INSTRUCTION, PAYLOAD, SCHEMA,
                                      native_request=self.request, native_response=self.response), VALID)

    def test_missing_envelopes_and_schema_invalid_response_return_none(self):
        for kwargs, failure in (({}, "native_request_required"),
                                ({"native_request": self.request}, "invalid_response"),
                                ({"native_response": self.response}, "native_request_required"),
                                ({"native_request": self.request, "native_response": response_for(self.request, {})},
                                 "schema_violation")):
            with self.subTest(kwargs=kwargs):
                result = self.call_judge(**kwargs)
                self.assertIsNone(result["verdict"])
                self.assertEqual(result["diagnostics"]["failure"], failure)

    def test_native_caller_task_mismatch_returns_none(self):
        for caller in ({"instruction": INSTRUCTION + "literal"}, {"payload": PAYLOAD + "literal"},
                       {"schema": True}):
            with self.subTest(caller=caller):
                result = self.call_judge(**caller, native_request=self.request, native_response=self.response)
                self.assertIsNone(result["verdict"])
                self.assertEqual(result["diagnostics"]["failure"], "native_task_mismatch")

    def test_native_schema_identity_distinguishes_boolean_and_number(self):
        for prepared, caller in ((True, 1), (1, True)):
            schema = {"type": "object", "properties": {"x": {"const": prepared}},
                      "required": ["x"], "additionalProperties": False}
            caller_schema = copy.deepcopy(schema)
            caller_schema["properties"]["x"]["const"] = caller
            request = native_judge.prepare_request(INSTRUCTION, PAYLOAD, schema)
            with self.subTest(prepared=prepared, caller=caller):
                result = self.call_judge(schema=caller_schema, native_request=request,
                                         native_response=response_for(request, {"x": prepared}))
                self.assertIsNone(result["verdict"])
                self.assertEqual(result["diagnostics"]["failure"], "native_task_mismatch")

    def test_native_model_selection_is_host_owned(self):
        result = self.call_judge(model="literal", native_request=self.request, native_response=self.response)
        self.assertIsNone(result["verdict"])
        self.assertEqual(result["diagnostics"]["failure"], "native_model_selection_external")

    def test_native_backend_selection_does_not_discover_executables(self):
        with patch.dict(os.environ, {"RADAR_LLM": "native"}), \
                patch.object(llm, "claude_path") as claude, patch.object(llm, "codex_path") as codex:
            self.assertEqual(llm.backend(), "native")
        claude.assert_not_called()
        codex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
