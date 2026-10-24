"""Exercise the installed CLI with a local fake provider; no model or credentials."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import codex_backend
import llm_call


@contextmanager
def local_provider(*, fail=False):
    requests = []
    message = {"id": "msg_local", "type": "message", "status": "completed", "role": "assistant",
               "content": [{"type": "output_text", "text": '{"ok":true}', "annotations": []}]}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b'{"data":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            # Retain neither prompts nor headers. Only the public tool schema is
            # needed to verify isolation at the actual HTTP transport boundary.
            requests.append({"model": data.get("model"), "tools": data.get("tools", [])})
            if fail:
                body = b'{"error":{"message":"AUTOTUNER_OFFLINE_SENTINEL","type":"invalid_request_error"}}'
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
            else:
                events = [
                    {"type": "response.created", "response": {"id": "resp_local", "object": "response",
                     "status": "in_progress", "model": "gpt-6-luna", "output": []}},
                    {"type": "response.output_item.added", "output_index": 0,
                     "item": {**message, "status": "in_progress", "content": []}},
                    {"type": "response.output_text.delta", "item_id": "msg_local", "output_index": 0,
                     "content_index": 0, "delta": '{"ok":true}'},
                    {"type": "response.output_item.done", "output_index": 0, "item": message},
                    {"type": "response.completed", "response": {"id": "resp_local", "object": "response",
                     "status": "completed", "model": "gpt-6-luna", "output": [message],
                     "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                               "input_tokens_details": {"cached_tokens": 0}}}},
                ]
                body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                               for event in events).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_run = subprocess.run
    settings = [
        'model_provider="aitt_local_probe"',
        'model_providers.aitt_local_probe.name="Autotuner local offline probe"',
        'model_providers.aitt_local_probe.base_url=' + json.dumps(f"http://127.0.0.1:{server.server_port}/v1"),
        'model_providers.aitt_local_probe.wire_api="responses"',
        'model_providers.aitt_local_probe.requires_openai_auth=false',
        'model_providers.aitt_local_probe.request_max_retries=0',
        'model_providers.aitt_local_probe.stream_max_retries=0',
    ]

    def run_local(command, **kwargs):
        # Override transport only in this test. The adapter's production
        # isolation flags, instruction handling and parser execute unchanged.
        command = list(command)
        overrides = [value for setting in settings for value in ("-c", setting)]
        command[-1:-1] = overrides
        return real_run(command, **kwargs)

    try:
        with mock.patch.object(codex_backend.subprocess, "run", side_effect=run_local):
            yield requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@unittest.skipUnless(shutil.which("codex"), "Codex CLI is not installed; mocked adapter tests still cover parsing")
class CodexStartupTest(unittest.TestCase):
    def attempt(self):
        return codex_backend.attempt('Return {"ok":true}.', "Return only JSON. Do not use tools.",
                                     "gpt-6-luna", "low", None, 15, None)

    def test_installed_cli_completes_without_startup_errors_or_model_tools(self):
        with local_provider() as requests:
            result = self.attempt()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(json.loads(result["text"]), {"ok": True})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0], {"model": "gpt-6-luna", "tools": []})
        self.assertEqual(result["usage"], {"input": 10, "output": 5, "cache_read": 0,
                                           "cache_write": None, "thinking": None})
        self.assertIsNone(result["cost_usd"])
        self.assertIsNone(result["model_served"])

    def test_provider_failure_is_preserved_after_startup_is_fixed(self):
        with local_provider(fail=True) as requests:
            result = self.attempt()
        self.assertFalse(result["ok"])
        self.assertIn("AUTOTUNER_OFFLINE_SENTINEL", result["error"])
        self.assertNotIn("Code Mode is unavailable", result["error"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["tools"], [])


class CodexAuthenticationTest(unittest.TestCase):
    def test_authentication_failure_does_not_retry_connection_or_try_again_guidance(self):
        messages = [
            "HTTP error: 401 Unauthorized: websocket connection failed. Please try again.",
            "Your access token could not be refreshed. Please sign in again; connection failed.",
            "authentication_error: invalid_api_key; please try again.",
        ]
        for message in messages:
            with self.subTest(message=message):
                output = json.dumps({"type": "error", "message": message}) + "\n"
                process = subprocess.CompletedProcess([], 1, stdout=output, stderr="connection failed; try again")
                with mock.patch.object(codex_backend.shutil, "which", return_value="/bin/codex"), \
                        mock.patch.object(codex_backend.subprocess, "run", return_value=process) as run:
                    result = llm_call.call("task", backend="codex", model="gpt-6-luna", retries=3)
                run.assert_called_once()
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_kind"], "harness")
                self.assertEqual(result["attempts"], 1)
                self.assertIn(message, result["error"])


if __name__ == "__main__":
    unittest.main()
