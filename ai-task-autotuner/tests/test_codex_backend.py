"""Codex's CLI transport is an isolated, metered substitute for one API call."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import codex_backend  # noqa: E402
import llm_call  # noqa: E402


USAGE = {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 20}


def events(*rows):
    return "\n".join(json.dumps(row) for row in rows) + "\n"


def success(text='{"choice":"C1"}', usage=None):
    return events(
        {"type": "thread.started", "thread_id": "test-thread"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": text}},
        {"type": "turn.completed", "usage": USAGE if usage is None else usage},
    )


def proc(stdout, rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class CodexBackendTest(unittest.TestCase):
    def setUp(self):
        self.which = mock.patch.object(codex_backend.shutil, "which", return_value="/bin/codex")
        self.which.start()
        self.sleep = mock.patch.object(llm_call.time, "sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.which.stop()

    def attempt(self, **kwargs):
        args = dict(prompt="untrusted payload", system="review only this payload", model="gpt-6-luna",
                    effort="low", json_schema=None, timeout=3, extra_args=None)
        args.update(kwargs)
        return codex_backend.attempt(**args)

    def test_one_turn_normalizes_usage_without_inventing_unreported_metrics(self):
        with mock.patch.object(codex_backend.subprocess, "run", return_value=proc(success())):
            record = self.attempt()
        self.assertTrue(record["ok"])
        self.assertEqual(record["text"], '{"choice":"C1"}')
        self.assertEqual(record["usage"], {"input": 40, "output": 20, "cache_read": 60,
                                            "cache_write": None, "thinking": None})
        for key in ("api_ms", "cost_usd", "model_served"):
            self.assertIsNone(record.get(key), key)

    def test_missing_cache_counter_does_not_turn_total_input_into_uncached_input(self):
        output = success(usage={"input_tokens": 100, "output_tokens": 20})
        with mock.patch.object(codex_backend.subprocess, "run", return_value=proc(output)):
            record = self.attempt()
        self.assertTrue(record["ok"])
        self.assertEqual(record["usage"], {"input": None, "output": 20, "cache_read": None,
                                            "cache_write": None, "thinking": None})

    def test_stdin_schema_and_model_are_isolated_from_project_context(self):
        schema = {"type": "object", "properties": {"choice": {"type": "string"}},
                  "required": ["choice"], "additionalProperties": False}
        observed = {}

        def run(command, **kwargs):
            observed.update(command=command, kwargs=kwargs)
            self.assertEqual(command[:2], ["/bin/codex", "exec"])
            self.assertEqual(command[-1], "-")
            for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral",
                         "--skip-git-repo-check", "--json"):
                self.assertIn(flag, command)
            self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
            self.assertEqual(command[command.index("--model") + 1], "gpt-6-luna")
            self.assertNotIn("untrusted payload", command)
            self.assertEqual(kwargs["input"], "untrusted payload")
            self.assertEqual(kwargs["timeout"], 3)
            self.assertNotEqual(Path(kwargs["cwd"]).resolve(), Path.cwd().resolve())
            schema_path = Path(command[command.index("--output-schema") + 1])
            self.assertEqual(json.loads(schema_path.read_text(encoding="utf-8")), schema)
            instructions = [command[i + 1] for i, value in enumerate(command[:-1])
                            if value in ("-c", "--config")
                            and command[i + 1].startswith("model_instructions_file=")]
            self.assertEqual(len(instructions), 1)
            instructions_path = Path(json.loads(instructions[0].split("=", 1)[1]))
            self.assertIn("review only this payload", instructions_path.read_text(encoding="utf-8"))
            observed["temporary_files"] = (schema_path, instructions_path)
            return proc(success())

        with mock.patch.object(codex_backend.subprocess, "run", side_effect=run):
            record = self.attempt(json_schema=schema)
        self.assertTrue(record["ok"])
        for path in observed["temporary_files"]:
            self.assertFalse(path.exists(), "request files must be removed after the CLI exits")

    def test_extra_arguments_cannot_override_isolation_or_requested_model(self):
        with mock.patch.object(codex_backend.subprocess, "run") as run:
            record = self.attempt(extra_args=["--model", "another-model"])
        self.assertFalse(record["ok"])
        self.assertFalse(record.get("retryable"))
        run.assert_not_called()

    def test_missing_binary_and_launch_failure_do_not_retry(self):
        with mock.patch.object(codex_backend.shutil, "which", return_value=None), \
                mock.patch.object(codex_backend.subprocess, "run") as run:
            record = self.attempt()
        self.assertFalse(record["ok"])
        self.assertEqual(record["error_kind"], "harness")
        self.assertFalse(record.get("retryable"))
        run.assert_not_called()
        with mock.patch.object(codex_backend.subprocess, "run", side_effect=OSError("cannot launch")):
            record = self.attempt()
        self.assertFalse(record["ok"])
        self.assertFalse(record.get("retryable"))

    def test_timeout_is_a_retryable_transport_failure(self):
        with mock.patch.object(codex_backend.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("codex", 3)):
            record = self.attempt()
        self.assertFalse(record["ok"])
        self.assertEqual(record["error_kind"], "timeout")
        self.assertTrue(record.get("retryable"))

    def test_malformed_or_incomplete_event_stream_never_becomes_an_answer(self):
        cases = [
            "not JSON\n",
            "[]\n",
            events({"type": "item.completed", "item": {"type": "agent_message", "text": "partial"}}),
            success() + "trailing garbage\n",
        ]
        for output in cases:
            with self.subTest(output=output), \
                    mock.patch.object(codex_backend.subprocess, "run", return_value=proc(output)):
                record = self.attempt()
            self.assertFalse(record["ok"])
            self.assertEqual(record["error_kind"], "harness")

    def test_tool_events_fail_closed_even_if_an_answer_follows(self):
        for kind in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
            output = events({"type": "item.started", "item": {"id": "tool", "type": kind}}) + success()
            with self.subTest(kind=kind), \
                    mock.patch.object(codex_backend.subprocess, "run", return_value=proc(output)):
                record = self.attempt()
            self.assertFalse(record["ok"])
            self.assertFalse(record.get("retryable"))

    def test_failed_turn_error_event_and_nonzero_exit_are_failures(self):
        cases = [
            proc(events({"type": "turn.failed", "error": {"message": "invalid model"}})),
            proc(events({"type": "error", "message": "invalid request"})),
            proc(success(), rc=1, stderr="child failed"),
        ]
        for output in cases:
            with self.subTest(output=output), \
                    mock.patch.object(codex_backend.subprocess, "run", return_value=output):
                record = self.attempt()
            self.assertFalse(record["ok"])

    def test_error_items_keep_provider_diagnostics_instead_of_claiming_tool_use(self):
        message = "model does not support low effort"
        cases = [
            proc(events({"type": "item.completed", "item": {"type": "error", "message": message}},
                        {"type": "turn.failed", "error": {"message": "request failed"}}), rc=1),
            proc(success() + events({"type": "item.completed", "item": {"type": "error", "text": message}})),
            proc(events({"type": "item.completed", "item": {"type": "error", "error": {"message": message}}}), rc=1),
        ]
        for output in cases:
            with self.subTest(output=output), \
                    mock.patch.object(codex_backend.subprocess, "run", return_value=output):
                record = self.attempt()
            self.assertFalse(record["ok"])
            self.assertEqual(record["error_kind"], "harness")
            self.assertIn(message, record["error"])
            self.assertNotIn("tool item", record["error"])
            self.assertFalse(record.get("retryable"))

    def test_error_items_preserve_transient_and_quota_retry_classification(self):
        cases = [("temporarily unavailable", "api", True),
                 ("You have hit your usage limit. Try later.", "quota", False)]
        for message, kind, retryable in cases:
            output = events({"type": "item.completed", "item": {"type": "error", "message": message}})
            with self.subTest(message=message), \
                    mock.patch.object(codex_backend.subprocess, "run", return_value=proc(output, rc=1)):
                record = self.attempt()
            self.assertFalse(record["ok"])
            self.assertEqual(record["error_kind"], kind)
            self.assertEqual(record.get("retryable"), retryable)
            self.assertIn(message, record["error"])
            self.assertNotIn("tool item", record["error"])

    def test_call_parses_final_json_and_keeps_unknown_cost_and_usage_unknown(self):
        with mock.patch.object(codex_backend.subprocess, "run", return_value=proc(success())):
            record = llm_call.call("payload", "instructions", backend="codex", model="gpt-6-luna",
                                   expect_json=True, retries=0)
        self.assertTrue(record["ok"])
        self.assertEqual(record["backend"], "codex")
        self.assertEqual(record["model_requested"], "gpt-6-luna")
        self.assertEqual(record["json"], {"choice": "C1"})
        self.assertEqual(record["usage"]["input"], 40)
        self.assertIsNone(record["usage"]["cache_write"])
        self.assertIsNone(record["usage"]["thinking"])
        self.assertIsNone(record["cost_usd"])
        self.assertIsNone(record["model_served"])

    def test_quota_error_stops_retries(self):
        output = events({"type": "error", "message": "You have hit your usage limit. Try later."})
        with mock.patch.object(codex_backend.subprocess, "run", return_value=proc(output, rc=1)) as run:
            record = llm_call.call("payload", backend="codex", model="gpt-6-luna", retries=3)
        self.assertFalse(record["ok"])
        self.assertEqual(record["error_kind"], "quota")
        self.assertEqual(record["attempts"], 1)
        self.assertEqual(run.call_count, 1)

    def test_transient_failure_retries_and_retains_both_attempts_usage(self):
        failed = proc(success() + events({"type": "error", "message": "temporarily unavailable"}), rc=1)
        with mock.patch.object(codex_backend.subprocess, "run", side_effect=[failed, proc(success())]) as run:
            record = llm_call.call("payload", backend="codex", model="gpt-6-luna", retries=2)
        self.assertTrue(record["ok"])
        self.assertEqual(record["attempts"], 2)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(record["usage"]["input"], 80)
        self.assertEqual(record["usage"]["cache_read"], 120)
        self.assertEqual(record["usage"]["output"], 40)
        self.assertIsNone(record["usage"]["cache_write"])
        self.assertIsNone(record["cost_usd"])

    def test_completed_but_empty_reply_gets_only_one_retry(self):
        with mock.patch.object(codex_backend.subprocess, "run", return_value=proc(success(""))) as run:
            record = llm_call.call("payload", backend="codex", model="gpt-6-luna", retries=3)
        self.assertFalse(record["ok"])
        self.assertEqual(record["error_kind"], "empty")
        self.assertEqual(record["attempts"], 2)
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
