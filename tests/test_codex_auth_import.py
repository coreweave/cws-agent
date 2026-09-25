"""Codex cache import uses synthetic tokens, mocked SDK calls, and local fixtures."""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from test_terminal import agent as cli


AUTH = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
        "tokens": {"access_token": "access-fixture", "refresh_token": "refresh-fixture",
                   "id_token": "id-fixture", "account_id": "account-fixture"}}
PAYLOAD = json.dumps(AUTH).encode()


class CodexAuthImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.local = self.root / "local"
        self.local.mkdir()
        (self.local / "auth.json").write_bytes(PAYLOAD)
        environment = patch.dict(os.environ, {"CODEX_HOME": str(self.local)}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.harness = cli.HARNESSES["codex"]
        self.args = types.SimpleNamespace(import_codex_auth=True)

    def test_explicit_import_respects_codex_home_and_omits_api_key(self):
        env = {"OPENAI_API_KEY": "api-fixture", "KEEP": "value"}
        self.assertEqual(cli.local_codex_auth(self.harness, self.args, env), PAYLOAD)
        self.assertEqual(env, {"KEEP": "value"})
        self.assertEqual((self.local / "auth.json").read_bytes(), PAYLOAD)

    def test_default_path_and_no_implicit_read(self):
        self.args.import_codex_auth = False
        with patch.object(os, "open", side_effect=AssertionError("credential accessed")):
            self.assertIsNone(cli.local_codex_auth(self.harness, self.args))
        self.args.import_codex_auth = True
        (self.root / ".codex").mkdir()
        (self.root / ".codex/auth.json").write_bytes(PAYLOAD)
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "home", return_value=self.root):
            self.assertEqual(cli.local_codex_auth(self.harness, self.args), PAYLOAD)

    def test_missing_malformed_and_non_chatgpt_caches_are_redacted(self):
        path = self.local / "auth.json"
        for data in (None, b"private-fixture", b"[]", b"{}", b"[" * 2000 + b"]" * 2000,
                     b"x" * (1024 * 1024 + 1),
                     json.dumps({**AUTH, "auth_mode": "apikey"}).encode(),
                     json.dumps({**AUTH, "OPENAI_API_KEY": "private-fixture"}).encode(),
                     json.dumps({**AUTH, "tokens": {"access_token": "private-fixture"}}).encode()):
            with self.subTest(size=len(data) if data else None):
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(data)
                with self.assertRaises(SystemExit) as failure:
                    cli.local_codex_auth(self.harness, self.args)
                self.assertNotIn("private-fixture", str(failure.exception))
                self.assertIn("Sign in locally", str(failure.exception))

    def test_wrong_harness_and_remote_home_are_rejected(self):
        with self.assertRaisesRegex(SystemExit, "requires the Codex CLI"):
            cli.local_codex_auth(cli.HARNESSES["claude"], self.args)
        with self.assertRaisesRegex(SystemExit, "default sandbox CODEX_HOME"):
            cli.local_codex_auth(self.harness, self.args, {"CODEX_HOME": "/other"})

    def test_non_regular_local_cache_is_rejected_without_blocking(self):
        path = self.local / "auth.json"
        path.unlink()
        os.mkfifo(path)
        code = ("import runpy, sys, types; app = runpy.run_path(sys.argv[1]); "
                "app['local_codex_auth'](app['HARNESSES']['codex'], "
                "types.SimpleNamespace(import_codex_auth=True))")
        result = subprocess.run([sys.executable, "-c", code, cli.__file__],
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"cannot read a ChatGPT login", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_transport_uses_stdin_and_redacts_failures(self):
        for error in (None, "write", "exec", "result", "status"):
            with self.subTest(error=error):
                sandbox = Mock()
                process = sandbox.exec.return_value
                process.result.return_value = types.SimpleNamespace(
                    returncode=1 if error == "status" else 0,
                    stdout="access-fixture", stderr="refresh-fixture")
                if error in ("write", "exec", "result"):
                    target = {"write": process.stdin.write, "exec": sandbox.exec,
                              "result": process.result}[error]
                    target.side_effect = RuntimeError("access-fixture")
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    if error:
                        with self.assertRaises(SystemExit) as failure:
                            cli.import_codex_auth(sandbox, PAYLOAD)
                        self.assertNotIn("access-fixture", str(failure.exception))
                    else:
                        cli.import_codex_auth(sandbox, PAYLOAD)
                self.assertNotIn("fixture", output.getvalue())
                self.assertNotIn("access-fixture", repr(sandbox.exec.call_args))
                self.assertTrue(sandbox.exec.call_args.kwargs["stdin"])
                if error != "exec":
                    process.stdin.write.assert_called_once_with(PAYLOAD)
                    self.assertTrue(process.stdin.close.called)

    def lifecycle(self, command, *, fail=False):
        sandbox = Mock(sandbox_id="sandbox-fixture")
        events = []

        def provision(**kwargs):
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            events.append("provision")
            return sandbox

        def transfer(sb, payload):
            self.assertIs(sb, sandbox)
            self.assertEqual(payload, PAYLOAD)
            events.append("import")
            if fail:
                raise SystemExit("fixture import failed")

        snapshot = types.SimpleNamespace(size_bytes=0, request_id="cwsa1|test|codex|1",
                                         file_system_snapshot_id="snapshot-fixture")
        with patch.object(cli, "find_active", return_value=None), \
                patch.object(cli, "require_active", return_value=sandbox), \
                patch.object(cli, "active_harness", return_value=self.harness), \
                patch.object(cli, "latest_ready_snapshot", return_value=snapshot), \
                patch.object(cli, "read_backend_config", return_value=None), \
                patch.object(cli, "provision_session", side_effect=provision) as create, \
                patch.object(cli, "sync_agent_config", side_effect=lambda *a, **kw: events.append("config")), \
                patch.object(cli, "import_codex_auth", side_effect=transfer), \
                patch.object(cli, "stop_failed_sandbox") as stop, \
                patch.object(cli, "pty_attach", side_effect=lambda *a: events.append("attach") or 0), \
                patch.dict(os.environ, {"OPENAI_API_KEY": "api-fixture"}), \
                contextlib.redirect_stdout(io.StringIO()):
            if fail:
                with self.assertRaisesRegex(SystemExit, "fixture import failed"):
                    cli.main(command)
                self.assertNotIn("attach", events)
                if create.called:
                    stop.assert_called_once_with(sandbox)
                else:
                    stop.assert_not_called()
            else:
                self.assertEqual(cli.main(command), 0)
                self.assertIn("import", events)
                if "attach" in events:
                    self.assertLess(events.index("import"), events.index("attach"))
                stop.assert_not_called()
        return events

    def test_startup_existing_login_and_restore_import_before_attach(self):
        commands = (["launch", "test", "--agent", "codex"],
                    ["launch", "test", "--agent", "codex", "--detach"],
                    ["restore", "test", "--connect"], ["connect", "test"], ["login", "test"])
        for command in commands:
            for fail in (False, True):
                with self.subTest(command=command, fail=fail):
                    self.lifecycle([*command, "--import-codex-auth"], fail=fail)

    def test_missing_auth_stops_before_provisioning(self):
        (self.local / "auth.json").unlink()
        with patch.object(cli, "find_active", return_value=None), \
                patch.object(cli, "provision_session") as create, self.assertRaises(SystemExit):
            cli.main(["launch", "test", "--agent", "codex", "--import-codex-auth"])
        create.assert_not_called()


class RemoteImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "workspace/home"
        self.folder = self.home / ".codex"
        self.folder.mkdir(parents=True)
        self.auth = self.folder / "auth.json"
        self.codex = self.root / "codex"
        self.codex.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
auth = json.loads((pathlib.Path(os.environ["HOME"]) / ".codex/auth.json").read_text())
if os.environ.get("CWS_AUTH_TEST_FAIL"):
    print(auth, file=sys.stderr)
    sys.exit(1)
assert auth["tokens"]["access_token"] == "access-fixture"
print("Logged in using ChatGPT", file=sys.stderr)
''')
        self.codex.chmod(0o755)

    def run_import(self, **env):
        script = cli.CODEX_AUTH_IMPORT_SCRIPT.replace("/workspace/home", str(self.home)).replace(
            "/opt/agent/bin/codex", str(self.codex))
        return subprocess.run([sys.executable, "-c", script], input=PAYLOAD, capture_output=True,
                              env={"PATH": "/usr/bin:/bin", **env}, timeout=40)

    def test_secure_write_and_no_secret_output(self):
        result = self.run_import()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.auth.read_bytes(), PAYLOAD)
        self.assertEqual(self.auth.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual(result.stdout + result.stderr, b"")
        self.assertEqual({path.name for path in self.folder.iterdir()},
                         {"auth.json", ".cws-auth-import.lock"})

    def test_failed_verification_rolls_back_and_redacts_output(self):
        for existing in (None, b"previous-cache-fixture"):
            with self.subTest(existing=existing is not None):
                if existing is not None:
                    self.auth.write_bytes(existing)
                result = self.run_import(CWS_AUTH_TEST_FAIL="1")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b"fixture", result.stdout + result.stderr)
                if existing is None:
                    self.assertFalse(self.auth.exists())
                else:
                    self.assertEqual(self.auth.read_bytes(), existing)

    def test_existing_symlink_is_not_followed_or_replaced(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.write_bytes(b"untouched")
        self.auth.symlink_to(elsewhere)
        self.assertNotEqual(self.run_import().returncode, 0)
        self.assertTrue(self.auth.is_symlink())
        self.assertEqual(elsewhere.read_bytes(), b"untouched")

    def test_symlinked_parent_is_rejected(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.folder.rmdir()
        self.folder.symlink_to(elsewhere, target_is_directory=True)
        self.assertNotEqual(self.run_import().returncode, 0)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_conflicting_remote_environment_does_not_write_credentials(self):
        for env in ({"OPENAI_API_KEY": "api-fixture"}, {"CODEX_HOME": "/other"}):
            with self.subTest(env=list(env)):
                self.assertNotEqual(self.run_import(**env).returncode, 0)
                self.assertFalse(self.auth.exists())

    def test_import_uses_the_same_environment_as_agent_startup(self):
        class Immediate:
            def result(self, **kwargs):
                return None

        class Process:
            def __init__(self, command, **kwargs):
                self.command = [part.replace("/workspace", str(self_home.parent)).replace(
                    "/opt/agent/bin/codex", str(self_codex)) for part in command]
                self.stdin = self
                self.payload = b""

            def write(self, payload):
                self.payload += payload
                return Immediate()

            def close(self):
                return Immediate()

            def result(self, **kwargs):
                return subprocess.run(self.command, input=self.payload, capture_output=True,
                                      env={"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"},
                                      timeout=40)

        self_home, self_codex = self.home, self.codex
        sandbox = Mock()
        sandbox.exec.side_effect = Process
        with contextlib.redirect_stdout(io.StringIO()):
            cli.import_codex_auth(sandbox, PAYLOAD)
        self.assertEqual(self.auth.read_bytes(), PAYLOAD)
        self.auth.unlink()
        (self.home / ".cws-import-env.sh").write_text("export OPENAI_API_KEY=api-fixture\n")
        with self.assertRaisesRegex(SystemExit, "Codex auth import failed"):
            cli.import_codex_auth(sandbox, PAYLOAD)
        self.assertFalse(self.auth.exists())

    def test_overlapping_import_is_rejected_without_touching_existing_auth(self):
        self.auth.write_bytes(b"previous-cache-fixture")
        with (self.folder / ".cws-auth-import.lock").open("wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_import()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.auth.read_bytes(), b"previous-cache-fixture")


if __name__ == "__main__":
    unittest.main()
