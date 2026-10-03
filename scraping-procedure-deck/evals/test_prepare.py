import importlib.util
import socket
import tempfile
import threading
import unittest
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("prepare.py")
spec = importlib.util.spec_from_file_location("prepare_under_test", SCRIPT)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class EnsureServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.servers = []
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.stop_servers)

    def stop_servers(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()

    def serve(self, revision):
        (self.root / "index.html").write_text("test site", encoding="utf-8")
        if revision is not None:
            (self.root / "rev.js").write_bytes(revision)
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(prepare.QuietHandler, directory=str(self.root)))
        self.servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_port}/"

    def test_existing_rev1_is_reused(self):
        base = self.serve((prepare.SITE / "rev.js").read_bytes())
        with patch.object(prepare, "BASE_URL", base), patch.object(prepare, "ThreadingHTTPServer") as factory:
            prepare.ensure_server()
        factory.assert_not_called()

    def test_existing_rev2_and_other_site_are_rejected_without_stopping(self):
        for revision in (b"window.SITE_REV = 2;\n", b"window.OTHER_SITE = true;\n"):
            with self.subTest(revision=revision):
                base = self.serve(revision)
                with patch.object(prepare, "BASE_URL", base), patch.object(prepare, "ThreadingHTTPServer") as factory:
                    with self.assertRaisesRegex(RuntimeError, "rev.js.*rev1.*rev2"):
                        prepare.ensure_server()
                factory.assert_not_called()
                with urllib.request.urlopen(base + "rev.js") as response:
                    self.assertEqual(response.read(), revision)

    def test_existing_site_without_revision_is_rejected(self):
        base = self.serve(None)
        with patch.object(prepare, "BASE_URL", base), patch.object(prepare, "ThreadingHTTPServer") as factory:
            with self.assertRaisesRegex(RuntimeError, "rev.js.*取得できません"):
                prepare.ensure_server()
        factory.assert_not_called()
        with urllib.request.urlopen(base + "index.html") as response:
            self.assertEqual(response.status, 200)

    def test_existing_site_without_index_is_rejected(self):
        base = self.serve(b"window.SITE_REV = 2;")
        (self.root / "index.html").unlink()
        with patch.object(prepare, "BASE_URL", base), patch.object(prepare, "ThreadingHTTPServer") as factory:
            with self.assertRaisesRegex(RuntimeError, "index.html.*HTTP 404"):
                prepare.ensure_server()
        factory.assert_not_called()

    def test_idle_port_starts_default_rev1(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}/"

        def create_server(*args, **kwargs):
            server = ThreadingHTTPServer(*args, **kwargs)
            self.servers.append(server)
            return server

        with patch.object(prepare, "BASE_URL", base), patch.object(prepare, "PORT", port), patch.object(prepare, "ThreadingHTTPServer", side_effect=create_server):
            prepare.ensure_server()
        with urllib.request.urlopen(base + "rev.js") as response:
            self.assertEqual(response.read(), (prepare.SITE / "rev.js").read_bytes())


if __name__ == "__main__":
    unittest.main()
