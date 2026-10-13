import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import llm
import run_checked

FLAGS = '--ephemeral --ignore-user-config --skip-git-repo-check --sandbox --cd --color --output-schema --model'


class CodexDiagnosticsTests(unittest.TestCase):
    def call(self, answer='{"ok":true,"note":"ready"}', code=0, stderr='', failure=None, file_mode=False):
        def run(command, **kwargs):
            if '--version' in command:
                return subprocess.CompletedProcess(command, 0, 'codex-cli 0.159.2', 'read-only PATH alias warning')
            if '--help' in command:
                return subprocess.CompletedProcess(command, 0, FLAGS + (' --output-last-message' if file_mode else ''), '')
            if failure:
                raise failure
            if file_mode and answer is not None:
                Path(command[command.index('--output-last-message') + 1]).write_text(answer)
            self.command = command
            self.kwargs = kwargs
            return subprocess.CompletedProcess(command, code, answer or '', stderr)
        with patch.dict(os.environ, {'RADAR_LLM':'codex'}), patch.object(llm, 'codex_path', return_value='/test/codex'), patch.object(llm.subprocess, 'run', side_effect=run):
            return llm.preflight('gpt-6-luna', 5)

    def test_nonfinite_numbers_are_not_json_verdicts(self):
        self.assertFalse(llm._valid(json.loads('{"score":NaN}'), {
            "type":"object", "properties":{"score":{"type":"number","minimum":0,"maximum":1}}}))
        self.assertFalse(llm._valid({"score": float('inf')}, {"type":"object"}))

    def test_timeout_must_be_positive_and_finite(self):
        for timeout in [float('inf'), float('nan'), 0, -1]:
            with patch.object(llm, 'backend') as backend:
                with self.assertRaises(ValueError):
                    llm.judge('instruction', 'payload', llm.PROBE_SCHEMA, timeout=timeout)
                backend.assert_not_called()
        for module in [llm, run_checked]:
            with patch.object(sys, 'argv', ['script', '--timeout', 'inf']), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    module.main()
                self.assertEqual(exc.exception.code, 2)

    def test_valid_stdout(self):
        result = self.call()
        self.assertEqual(result['verdict'], {'ok':True,'note':'ready'})
        self.assertEqual(result['diagnostics']['child_returncode'], 0)
        self.assertEqual(result['diagnostics']['cli_version'], '0.159.2')
        self.assertEqual(self.command[self.command.index('--sandbox')+1], 'read-only')
        self.assertNotIn('ready', self.command)

    def test_last_message_file(self):
        self.assertIsNotNone(self.call(file_mode=True)['verdict'])
        self.assertEqual(self.call(answer=None, file_mode=True)['diagnostics']['failure'], 'final_response_missing')

    def test_invalid_empty_and_schema(self):
        for answer, kind in [('not json','invalid_json'), ('','empty_response'), ('{"ok":"yes","note":"ready"}','schema_violation'), ('{"ok":false,"note":"ready"}','probe_mismatch'), ('{"type":"event"}\n{"type":"event"}','invalid_json')]:
            with self.subTest(answer=answer):
                result = self.call(answer)
                self.assertIsNone(result['verdict'])
                self.assertEqual(result['diagnostics']['failure'],kind)

    def test_child_failures(self):
        for message, kind in [('unknown option','unsupported_option'), ('Unauthorized','authentication'), ('model is not supported','model_unavailable'), ('Read-only file system','read_only'), ('app-server failed','appserver'), ('boom','child_nonzero')]:
            with self.subTest(kind=kind):
                result = self.call(code=7,stderr=message)
                self.assertIsNone(result['verdict'])
                self.assertEqual(result['diagnostics']['child_returncode'],7)
                self.assertEqual(result['diagnostics']['failure'],kind)

    def test_timeout_launch_and_missing_cli(self):
        for exc, kind in [(subprocess.TimeoutExpired('cmd',5,output='private',stderr='secret'),'timeout'), (OSError('personal data'),'launch_failed'), (FileNotFoundError('private path'),'cli_not_found')]:
            result = self.call(failure=exc)
            self.assertEqual(result['diagnostics']['failure'],kind)
        with patch.dict(os.environ, {'RADAR_LLM':'codex'}), patch.object(llm,'codex_path',return_value=None):
            self.assertEqual(llm.preflight()['diagnostics']['failure'],'cli_not_found')

    def test_help_does_not_allow_unverified_flags(self):
        with patch.dict(os.environ, {'RADAR_LLM':'codex'}), patch.object(llm,'codex_path',return_value='codex'), patch.object(llm.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'wrapper without options','')) as run:
            result = llm.preflight()
            self.assertEqual(run.call_count,2)
            self.assertEqual(result['diagnostics']['failure'],'unsupported_option')

    def test_masking_including_unknown_secrets_and_pii(self):
        secret = 'Bearer opaque-secret\nKEY=unknown-value\nuser@example.org /home/private/name\n個人名'
        result = self.call(code=1,stderr=secret)
        public = json.dumps(result)
        for value in ['opaque-secret','unknown-value','user@example.org','/home/private/name']:
            self.assertNotIn(value,public)
        self.assertEqual(result['diagnostics']['stderr']['text'],'[withheld]')
        self.assertGreater(result['diagnostics']['elapsed_sec'],-1)

    def test_parent_cli_codes(self):
        for verdict, code in [({'ok':True,'note':'ready'},0), (None,1)]:
            with patch.object(sys,'argv',['llm.py']), patch.object(llm,'preflight',return_value={'verdict':verdict,'diagnostics':{}}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(llm.main(),code)

    def test_failed_gate_preserves_pending_state_and_never_starts_command(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)/'queue.json'
            state.write_text(json.dumps({'version':1,'candidates':[{'id':'pending'}]}))
            before = state.read_bytes()
            with patch.object(sys,'argv',['run_checked.py','--','python','collect.py']), patch.object(llm,'preflight',return_value={'verdict':None,'diagnostics':{'failure':'cli_not_found'}}), patch.object(run_checked.subprocess,'run') as child, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_checked.main(),1)
                child.assert_not_called()
            self.assertEqual(state.read_bytes(),before)

    def test_successful_gate_propagates_command_exit(self):
        with patch.object(sys,'argv',['run_checked.py','--','python','collect.py']), patch.object(llm,'preflight',return_value={'verdict':{'ok':True,'note':'ready'},'diagnostics':{}}), patch.object(run_checked.subprocess,'run',return_value=subprocess.CompletedProcess([],9)) as child:
            self.assertEqual(run_checked.main(),9)
            child.assert_called_once_with(['python','collect.py'])


if __name__ == '__main__':
    unittest.main()
