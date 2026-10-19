"""Host selection and persisted backend settings; no real CLI or model calls."""
import contextlib
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common  # noqa: E402
import export_deliverables  # noqa: E402
import init_workspace  # noqa: E402
import llm_call  # noqa: E402
import run_experiment  # noqa: E402


def installed(*names):
    return lambda name: f"/test/bin/{name}" if name in names else None


class BackendSelectionTest(unittest.TestCase):
    def resolve(self, backend="auto", env=None, executables=("claude", "codex")):
        with mock.patch.dict(os.environ, env or {}, clear=True), \
                mock.patch.object(llm_call.shutil, "which", side_effect=installed(*executables)):
            return llm_call.resolve_backend(backend)

    def test_parent_session_selects_host_when_both_clis_are_installed(self):
        for marker in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_CAAS_SESSION_ID"):
            with self.subTest(marker=marker):
                self.assertEqual(self.resolve(env={marker: "session"}), "codex")
        for marker in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
            with self.subTest(marker=marker):
                self.assertEqual(self.resolve(env={marker: "session"}), "claude")

    def test_explicit_selection_and_environment_override_take_precedence(self):
        env = {"CLAUDECODE": "1", "AITT_BACKEND": "claude"}
        self.assertEqual(self.resolve("codex", env), "codex")
        self.assertEqual(self.resolve("api", env), "api")
        self.assertEqual(self.resolve("cli", {"CODEX_THREAD_ID": "x"}), "claude")
        self.assertEqual(self.resolve(env={"CLAUDECODE": "1", "AITT_BACKEND": "codex"}), "codex")
        self.assertEqual(self.resolve(env={"AITT_BACKEND": "cli"}), "claude")

    def test_sole_installed_cli_is_used_outside_parent_sessions(self):
        self.assertEqual(self.resolve(executables=("codex",)), "codex")
        self.assertEqual(self.resolve(executables=("claude",)), "claude")

    def test_ambiguous_or_unavailable_hosts_require_explicit_configuration(self):
        for env, executables in (({}, ("claude", "codex")),
                                 ({"CLAUDECODE": "1", "CODEX_THREAD_ID": "x"}, ("claude", "codex")),
                                 ({}, ())):
            with self.subTest(env=env, executables=executables):
                with self.assertRaises(ValueError):
                    self.resolve(env=env, executables=executables)

    def test_invalid_selection_is_rejected_without_fallback(self):
        with self.assertRaises(ValueError):
            self.resolve("typo")
        with self.assertRaises(ValueError):
            self.resolve(env={"AITT_BACKEND": "typo", "CLAUDECODE": "1"})

    def test_model_defaults_depend_on_backend_and_explicit_model_is_preserved(self):
        for automatic in (None, "auto"):
            self.assertEqual(llm_call.resolve_model(automatic, "codex"), "gpt-6-luna")
            self.assertEqual(llm_call.resolve_model(automatic, "claude"), "haiku")
            self.assertEqual(llm_call.resolve_model(automatic, "api"), "haiku")
        self.assertEqual(llm_call.resolve_model("custom-model", "codex"), "custom-model")

    def test_bad_call_configuration_is_a_harness_record_without_subprocess(self):
        with mock.patch.object(llm_call.subprocess, "run") as run:
            result = llm_call.call("hi", backend="typo")
        run.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "harness")
        self.assertIsNone(result["cost_usd"])

    def test_default_call_dispatches_to_host_and_records_effective_model(self):
        answer = {"ok": True, "text": "yes", "usage": {"input": 2, "output": 1},
                  "model_served": "gpt-6-luna", "stop_reason": "completed", "cost_usd": None}
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session"}, clear=True), \
                mock.patch.object(llm_call.shutil, "which", side_effect=installed("claude", "codex")), \
                mock.patch.object(llm_call, "_codex_attempt", return_value=answer) as codex, \
                mock.patch.object(llm_call, "_cli_attempt") as claude:
            result = llm_call.call("hi")
        codex.assert_called_once()
        claude.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["backend"], "codex")
        self.assertEqual(result["model_requested"], "gpt-6-luna")
        self.assertIsNone(result["cost_usd"])


class PersistedBackendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = self.root / "ws"
        self.data = self.root / "data.jsonl"
        self.data.write_text('{"id":"u1","item_keys":["x"]}\n', encoding="utf-8")

    def initialize(self, *options, env=None, executables=("claude", "codex")):
        argv = ["init_workspace.py", str(self.ws), "--task", "toy", "--data", str(self.data), *options]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.dict(os.environ, env or {}, clear=True), \
                mock.patch.object(llm_call.shutil, "which", side_effect=installed(*executables)), \
                contextlib.redirect_stdout(io.StringIO()):
            init_workspace.main()
        return common.read_json(self.ws / "charter.json")

    def smoke_candidate(self, name, config=None, pipeline=None):
        candidate = self.ws / "candidates" / name
        candidate.mkdir(parents=True, exist_ok=True)
        common.write_json(candidate / "config.json", config or {})
        (candidate / "pipeline.py").write_text(pipeline or
            'def run(unit, ctx):\n    return ctx.llm("label", expect_json=True)["json"]\n', encoding="utf-8")
        (self.ws / "eval" / "dataset.py").write_text(
            'def load_units(split):\n    return [] if split == "holdout" else [{"id": "u1", "input": {}}]\n',
            encoding="utf-8")
        (self.ws / "eval" / "score.py").write_text(
            'def score(units, outputs, split):\n'
            '    return {"primary": float(outputs["u1"] is not None), "items": []}\n', encoding="utf-8")
        return name

    def test_new_codex_workspace_pins_luna_for_calls_and_reference(self):
        ch = self.initialize(env={"CODEX_THREAD_ID": "session"})
        self.assertEqual(ch["backend"]["backend"], "codex")
        self.assertEqual(ch["backend"]["model"], "gpt-6-luna")
        self.assertEqual(ch["reference_model"], "gpt-6-luna")
        self.assertEqual(ch["efficiency_metric"], "calls_per_unit")
        candidate = common.read_json(self.ws / "candidates" / "c000-baseline" / "config.json")
        self.assertNotEqual(candidate.get("model"), "haiku")

    def test_explicit_init_configuration_is_kept(self):
        ch = self.initialize("--backend", "claude", "--model", "sonnet", "--reference-model", "opus",
                             env={"CODEX_THREAD_ID": "session"})
        self.assertEqual(ch["backend"]["backend"], "claude")
        self.assertEqual(ch["backend"]["model"], "sonnet")
        self.assertEqual(ch["reference_model"], "opus")

    def test_reinitialization_does_not_switch_existing_workspace_to_current_host(self):
        original = self.initialize("--backend", "cli", "--model", "haiku", "--reference-model", "opus")
        reread = self.initialize(env={"CODEX_THREAD_ID": "session"})
        self.assertEqual(reread["backend"], original["backend"])
        self.assertEqual(reread["reference_model"], original["reference_model"])
        # Reopening needs no executable: settings are already fixed in the charter.
        reread = self.initialize(executables=())
        self.assertEqual(reread["backend"], original["backend"])

    def test_migration_keeps_legacy_backend_and_reference_model(self):
        self.initialize("--backend", "claude")
        charter = common.read_json(self.ws / "charter.json")
        charter["backend"].update(backend="cli", model="haiku")
        charter["reference_model"] = "opus"
        common.write_json(self.ws / "charter.json", charter)
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session"}, clear=True), \
                contextlib.redirect_stdout(io.StringIO()):
            init_workspace.migrate(self.ws)
        migrated = common.read_json(self.ws / "charter.json")
        self.assertEqual(migrated["backend"]["backend"], "cli")
        self.assertEqual(migrated["backend"]["model"], "haiku")
        self.assertEqual(migrated["reference_model"], "opus")

    def test_runner_pins_auto_settings_for_calls_metadata_and_export(self):
        charter = self.initialize(env={"CODEX_THREAD_ID": "session"})
        charter["backend"].update(backend="auto", model="auto", concurrency=1)
        common.write_json(self.ws / "charter.json", charter)
        candidate = self.ws / "candidates" / "c000-baseline"
        (candidate / "config.json").write_text('{}\n', encoding="utf-8")
        (candidate / "pipeline.py").write_text(
            'def run(unit, ctx):\n    return ctx.llm("label", expect_json=True)["json"]\n', encoding="utf-8")
        (self.ws / "eval" / "dataset.py").write_text(
            'def load_units(split):\n    return [] if split == "holdout" else [{"id": "u1", "input": {}}]\n',
            encoding="utf-8")
        (self.ws / "eval" / "score.py").write_text(
            'def score(units, outputs, split):\n'
            '    return {"primary": 1.0, "metrics": {}, "items": [{"id":"u1","score":1.0}]}\n',
            encoding="utf-8")
        answer = {"ok": True, "json": {"label": "yes"}, "error_kind": None, "text": '{}',
                  "backend": "codex", "model_requested": "gpt-6-luna", "model_served": "gpt-6-luna",
                  "usage": {"input": 2, "output": 3}, "cost_usd": None, "wall_ms": 25000,
                  "attempts": 1, "stop_reason": "completed"}
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session"}, clear=True), \
                mock.patch.object(llm_call.shutil, "which", side_effect=installed("claude", "codex")), \
                mock.patch.object(run_experiment, "gate_allows_runs", return_value=(True, "")), \
                mock.patch.object(llm_call, "call", return_value=answer) as call, \
                mock.patch.object(run_experiment.subprocess, "run",
                                  return_value=SimpleNamespace(stdout="test-cli", returncode=0)), \
                contextlib.redirect_stdout(io.StringIO()):
            run_dir = run_experiment.run(self.ws, "c000-baseline", "dev", "smoke")
        self.assertEqual(call.call_args.kwargs["backend"], "codex")
        self.assertEqual(call.call_args.kwargs["model"], "gpt-6-luna")
        config = common.read_json(run_dir / "meta.json")["config"]
        self.assertEqual(config["backend"], "codex")
        self.assertEqual(config["model"], "gpt-6-luna")
        summary = common.read_json(run_dir / "summary.json")
        self.assertEqual(summary["tokens"]["input"], 2)
        self.assertEqual(summary["tokens"]["output"], 3)
        for key in ("cache_read", "cache_write", "thinking"):
            self.assertIsNone(summary["tokens"][key])
        self.assertFalse(summary["cost_complete"])
        for key in ("cost_usd", "cost_usd_per_unit", "cost_uncached_list_usd", "api_ms_per_unit"):
            self.assertIsNone(summary[key])
        rec = export_deliverables.recommend(self.ws, "c000-baseline", [run_dir])
        self.assertEqual(rec["settings"]["backend_measured"], "codex")
        self.assertEqual(rec["settings"]["model"]["value"], "gpt-6-luna")
        self.assertIsNone(rec["settings"]["max_tokens"]["value"])
        self.assertEqual(rec["settings"]["thinking"]["value"], "backend managed")
        self.assertEqual(rec["settings"]["timeout_s"]["value"], 80)
        self.assertNotIn("cost_usd_per_unit_uncached_list", rec["expectations"])

    def test_candidate_backend_change_defaults_model_but_same_provider_preserves_custom_model(self):
        charter = self.initialize(env={"CODEX_THREAD_ID": "session"})
        cases = (("codex", "gpt-6-luna", "claude", "haiku"),
                 ("cli", "haiku", "codex", "gpt-6-luna"),
                 ("cli", "custom-claude", "claude", "custom-claude"),
                 ("codex", "custom-codex", "codex", "custom-codex"))
        for index, (original, model, override, expected) in enumerate(cases):
            with self.subTest(original=original, model=model, override=override):
                charter["backend"].update(backend=original, model=model, concurrency=1)
                common.write_json(self.ws / "charter.json", charter)
                name = self.smoke_candidate(f"c00{index + 1}-override", {"backend": override})
                with mock.patch.object(run_experiment, "gate_allows_runs", return_value=(True, "")), \
                        mock.patch.object(run_experiment, "_cli_version", return_value="test-cli"), \
                        mock.patch.object(llm_call, "call", return_value={"ok": True, "json": {"label": "yes"}}) as call, \
                        contextlib.redirect_stdout(io.StringIO()):
                    run_dir = run_experiment.run(self.ws, name, "dev", "smoke")
                self.assertEqual(call.call_args.kwargs["backend"], override)
                self.assertEqual(call.call_args.kwargs["model"], expected)
                config = common.read_json(run_dir / "meta.json")["config"]
                self.assertEqual(config["backend"], override)
                self.assertEqual(config["model"], expected)

    def test_codex_usd_budget_refuses_model_call_but_allows_code_only_candidate(self):
        charter = self.initialize(env={"CODEX_THREAD_ID": "session"})
        charter["limits"]["max_cost_usd"] = 0.001
        common.write_json(self.ws / "charter.json", charter)
        model_candidate = self.smoke_candidate("c001-model")
        code_candidate = self.smoke_candidate("c002-code", pipeline=
            'def run(unit, ctx):\n    return {"label": "yes"}\n')
        with mock.patch.object(run_experiment, "gate_allows_runs", return_value=(True, "")), \
                mock.patch.object(llm_call, "call") as call, \
                mock.patch.object(run_experiment.subprocess, "run",
                                  return_value=SimpleNamespace(stdout="test-cli", returncode=0)) as process, \
                contextlib.redirect_stdout(io.StringIO()):
            denied = run_experiment.run(self.ws, model_candidate, "dev", "smoke")
            allowed = run_experiment.run(self.ws, code_candidate, "dev", "smoke")
        call.assert_not_called()
        self.assertTrue(all(c.args[0][-1] == "--version" for c in process.call_args_list))
        denied_row = common.read_jsonl(denied / "outputs.jsonl")[0]
        self.assertEqual(denied_row["status"], "harness_error")
        self.assertIn("max_cost_usd cannot be enforced", denied_row["error"])
        self.assertEqual(common.read_json(denied / "summary.json")["calls"], 0)
        self.assertEqual(common.read_jsonl(allowed / "outputs.jsonl")[0]["status"], "ok")
        self.assertEqual(common.read_json(allowed / "summary.json")["calls"], 0)

    def test_usd_budget_checks_effective_per_call_backend_before_dispatch(self):
        for backend in ("codex", "auto"):
            with self.subTest(backend=backend):
                ctx = run_experiment.Ctx(self.root, {"backend": "claude", "model": "haiku"},
                                         run_experiment.RunLog(self.root), threading.BoundedSemaphore(1),
                                         usd_cost_limit=0.001)
                with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "session"}, clear=True), \
                        mock.patch.object(llm_call, "call") as call, \
                        self.assertRaisesRegex(RuntimeError, "max_cost_usd cannot be enforced"):
                    ctx.llm("hello", backend=backend)
                self.assertTrue(ctx.harness_failed)
                self.assertEqual(ctx.n_calls, 0)
                call.assert_not_called()

    def test_per_call_backend_override_preserves_same_provider_model_and_defaults_after_provider_change(self):
        cases = (("codex", "gpt-6-luna", "claude", "claude", "haiku"),
                 ("claude", "sonnet", "cli", "claude", "sonnet"),
                 ("codex", "custom-codex", "codex", "codex", "custom-codex"))
        for original, model, override, canonical, expected_model in cases:
            with self.subTest(original=original, model=model, override=override):
                log = run_experiment.RunLog(self.root)
                ctx = run_experiment.Ctx(self.root, {"backend": original, "model": model},
                                         log, threading.BoundedSemaphore(1))
                answer = {"ok": True, "text": "hello"}
                with mock.patch.object(llm_call, "_cli_attempt", return_value=answer) as claude, \
                        mock.patch.object(llm_call, "_codex_attempt", return_value=answer) as codex:
                    result = ctx.llm("hello", backend=override)
                selected, unused = (codex, claude) if canonical == "codex" else (claude, codex)
                selected.assert_called_once()
                unused.assert_not_called()
                self.assertEqual(result["backend"], canonical)
                self.assertEqual(result["model_requested"], expected_model)


if __name__ == "__main__":
    unittest.main()
