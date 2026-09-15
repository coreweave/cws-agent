"""Cursor's native CLI contract, without network access or account credentials."""
import contextlib
import io
import json
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
import types
import unittest
from unittest.mock import patch

from test_terminal import agent
from test_consolidation import cli_parser


class CursorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.harness = agent.HARNESSES["cursor"]

    def test_launch_selects_dedicated_cursor_harness(self):
        args = cli_parser().parse_args(["launch", "--name", "cursor1", "--agent", "cursor"])
        self.assertEqual(args.agent, "cursor")
        self.assertEqual(self.harness.agent_bin, "cursor-agent")
        self.assertEqual(self.harness.interactive_cmd, "exec cursor-agent")

    def test_install_preserves_complete_package_outside_workspace(self):
        script = self.harness.bootstrap
        self.assertIn("https://downloads.cursor.com/lab/2026.09.02-c22c1a3/linux/", script)
        self.assertIn('tar --strip-components=1 -xzf "$cursor_tmp/package.tar.gz" -C /opt/agent/cursor', script)
        self.assertIn('exec /opt/agent/cursor/cursor-agent --disable-auto-update "$@"', script)
        self.assertIn("test -x /opt/agent/.local/bin/cursor-agent", script)
        self.assertNotIn("set -o pipefail", script)
        self.assertNotIn("| tar", script)
        self.assertNotIn('Path("/workspace/home/.cursor', script)
        result = subprocess.run(["/bin/sh", "-n"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @contextlib.contextmanager
    def offline_bootstrap(self, *, corrupt_archive=False):
        """Execute the real generated shell, replacing only paths and download I/O."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            (root / "tmp").mkdir()
            (bin_dir / "python3").symlink_to(sys.executable)
            curl = bin_dir / "curl"
            curl.write_text('''#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then shift; destination=$1; fi
  shift
done
cp "$CWS_CURSOR_TEST_ARCHIVE" "$destination"
exit "${CWS_CURSOR_TEST_CURL_STATUS:-0}"
''')
            curl.chmod(0o755)
            archive = root / "fixture.tar.gz"
            if corrupt_archive:
                archive.write_bytes(b"a truncated or invalid archive")
            else:
                with tarfile.open(archive, "w:gz") as package:
                    for name, body, mode in (
                        ("cursor-agent", b'#!/bin/sh\nprintf "cursor-fixture %s\\n" "$*"\n', 0o755),
                        ("node", b"#!/bin/sh\nexit 0\n", 0o755),
                        ("index.js", b"// complete companion fixture\n", 0o644),
                    ):
                        member = tarfile.TarInfo("package/" + name)
                        member.size, member.mode = len(body), mode
                        package.addfile(member, io.BytesIO(body))
            script = self.harness.bootstrap.replace("/workspace", str(root / "workspace"))
            script = script.replace("/opt/", str(root / "opt") + "/")
            script = script.replace("/tmp/cws-cursor.", str(root / "tmp/cws-cursor."))
            env = {"PATH": str(bin_dir) + ":/usr/bin:/bin", "CWS_CURSOR_TEST_ARCHIVE": str(archive)}
            yield root, script, env

    @unittest.skipUnless(shutil.which("dash"), "requires dash, the Debian /bin/sh implementation")
    def test_full_bootstrap_executes_under_dash_and_reuses_complete_install(self):
        with self.offline_bootstrap() as (root, script, env):
            for status in ("0", "22"):
                # A completed install must not download again (second curl would fail).
                result = subprocess.run([shutil.which("dash")], input=script, text=True, capture_output=True,
                                        env={**env, "CWS_CURSOR_TEST_CURL_STATUS": status})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("cursor-fixture --disable-auto-update --version", result.stdout)
                self.assertTrue((root / "opt/agent/cursor/.cws-install-complete").is_file())
                self.assertEqual(list((root / "tmp").iterdir()), [])

    @unittest.skipUnless(shutil.which("dash"), "requires dash, the Debian /bin/sh implementation")
    def test_failed_download_stops_before_extract_and_can_retry(self):
        with self.offline_bootstrap() as (root, script, env):
            result = subprocess.run([shutil.which("dash")], input=script, text=True, capture_output=True,
                                    env={**env, "CWS_CURSOR_TEST_CURL_STATUS": "22"})
            self.assertEqual(result.returncode, 22)
            self.assertFalse((root / "opt/agent/cursor/cursor-agent").exists())
            self.assertFalse((root / "opt/agent/cursor/.cws-install-complete").exists())
            self.assertEqual(list((root / "tmp").iterdir()), [])
            retried = subprocess.run([shutil.which("dash")], input=script, text=True, capture_output=True, env=env)
            self.assertEqual(retried.returncode, 0, retried.stderr)

    @unittest.skipUnless(shutil.which("dash"), "requires dash, the Debian /bin/sh implementation")
    def test_failed_extraction_does_not_mark_install_complete_and_cleans_archive(self):
        with self.offline_bootstrap(corrupt_archive=True) as (root, script, env):
            result = subprocess.run([shutil.which("dash")], input=script, text=True, capture_output=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "opt/agent/cursor/.cws-install-complete").exists())
            self.assertFalse((root / "opt/agent/.local/bin/cursor-agent").exists())
            self.assertEqual(list((root / "tmp").iterdir()), [])

    def test_runtime_home_keeps_auth_and_history_in_snapshots(self):
        self.assertIn('export HOME="/workspace/home"', agent.AGENT_ENV)
        self.assertIn("CURSOR_CONFIG_DIR=/workspace/home/.cursor", self.harness.bootstrap)
        self.assertIn("CURSOR_DATA_DIR=/workspace/home/.cursor", self.harness.bootstrap)

    def test_installed_shim_disables_updates_for_every_command(self):
        install = self.harness.bootstrap.split("<<'PYCURSOR'\n", 1)[1].split("\nPYCURSOR", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / "cursor-agent"
            install = install.replace("/opt/agent/.local/bin/cursor-agent", str(launcher))
            install = install.replace("/opt/agent/cursor/cursor-agent", "/bin/echo")
            subprocess.run([sys.executable, "-c", install], check=True)
            for command in (["login"], ["ls"], ["create-chat"], ["--resume", "chat-id"],
                            ["-p", "--", "--force"]):
                result = subprocess.run([str(launcher), *command], capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.strip(), " ".join(["--disable-auto-update", *command]))

    def test_shim_overrides_nonportable_state_and_cache_environment(self):
        expected = {"HOME": "/workspace/home", "CURSOR_CONFIG_DIR": "/workspace/home/.cursor",
                    "CURSOR_DATA_DIR": "/workspace/home/.cursor", "XDG_CONFIG_HOME": "/workspace/home/.config",
                    "XDG_DATA_HOME": "/workspace/home/.local/share", "XDG_STATE_HOME": "/workspace/home/.local/state",
                    "XDG_CACHE_HOME": "/opt/cache", "NODE_COMPILE_CACHE": "/opt/cache/cursor-compile-cache"}
        install = self.harness.bootstrap.split("<<'PYCURSOR'\n", 1)[1].split("\nPYCURSOR", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / "cursor-agent"
            probe = Path(directory) / "probe"
            probe.write_text("#!" + sys.executable + "\nimport json, os\nprint(json.dumps({key: os.environ.get(key) for key in "
                             + repr(list(expected)) + "}))\n")
            probe.chmod(0o755)
            install = install.replace("/opt/agent/.local/bin/cursor-agent", str(launcher))
            install = install.replace("/opt/agent/cursor/cursor-agent", str(probe))
            subprocess.run([sys.executable, "-c", install], check=True)
            result = subprocess.run([str(launcher), "login"], capture_output=True, text=True, check=True,
                                    env={key: "/tmp/nonportable" for key in expected})
            self.assertEqual(json.loads(result.stdout), expected)

    def test_api_key_is_env_only_not_command_argument(self):
        self.assertEqual(self.harness.env_passthrough, ("CURSOR_API_KEY",))
        self.assertNotIn("--api-key", self.harness.headless_fmt)
        self.assertNotIn("--api-key", self.harness.login_cmd)

    def test_login_shows_remote_browser_flow(self):
        self.assertEqual(shlex.split(self.harness.login_cmd),
                         ["exec", "env", "NO_OPEN_BROWSER=1", "cursor-agent", "login"])

    def test_accept_edits_does_not_enable_shell_or_mcp_bypass(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            flags = agent.permission_flags(self.harness, types.SimpleNamespace(yolo=False))
        self.assertEqual(flags, "")
        self.assertIn("configured permissions", stderr.getvalue())

    def test_native_mode_retains_permissions_without_notice(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            flags = agent.permission_flags(self.harness,
                types.SimpleNamespace(yolo=False, permission_mode="native"))
        self.assertEqual(flags, "")
        self.assertEqual(stderr.getvalue(), "")

    def test_explicit_bypass_maps_only_to_cursor_force(self):
        flags = agent.permission_flags(self.harness, types.SimpleNamespace(yolo=True))
        self.assertEqual(flags, " --force")
        self.assertNotIn("--approve-mcps", flags)

    def test_headless_prompt_remains_one_quoted_argument(self):
        prompt = "Review README; $(touch /tmp/never-run) and 'quoted text'"
        command = self.harness.headless_fmt.format(prompt=shlex.quote(prompt), extra="")
        argv = shlex.split(command)
        self.assertEqual(argv, ["cursor-agent", "-p", "--output-format", "text", "--trust", "--", prompt])
        self.assertNotIn("--force", argv)

    def test_auth_status_uses_status_specific_json_not_exit_code(self):
        for status, authenticated in (("unauthenticated", False), ("partially-authenticated", False),
                                      ("authenticated", True)):
            with self.subTest(status=status), patch.object(agent, "exec_retry", return_value=types.SimpleNamespace(
                    returncode=0, stdout=json.dumps({"status": status, "isAuthenticated": authenticated,
                                                    "userInfo": {"email": "private@example.test"}}))) as run:
                self.assertIs(agent.cursor_auth_status("sandbox"), authenticated)
                command = run.call_args.args[1][2]
                self.assertIn("cursor-agent status --format json", command)
                self.assertNotIn("--output-format", command)
                self.assertEqual(run.call_args.kwargs, {"timeout_seconds": 15, "attempts": 1})

    def test_api_key_configured_path_does_not_call_status_or_print_key(self):
        with patch.object(agent, "exec_retry", return_value=types.SimpleNamespace(
                returncode=0, stdout='{"status":"api-key-configured"}')) as run:
            self.assertTrue(agent.cursor_auth_status("sandbox"))
        command = run.call_args.args[1][2]
        command = command[command.index('if [ -n "${CURSOR_API_KEY:-}" ]'):]
        result = subprocess.run(["/bin/sh", "-c", command], text=True, capture_output=True,
                                env={"CURSOR_API_KEY": "fake-secret-never-log"}, check=True)
        self.assertEqual(json.loads(result.stdout), {"status": "api-key-configured"})
        self.assertNotIn("fake-secret", result.stdout + result.stderr)

    def test_auth_status_rejects_incomplete_or_plaintext_results_without_leaks(self):
        for output in ('Not logged in\n', '{"status":"authenticated"}',
                       '{"isAuthenticated":true}', '{"status":"authenticated","isAuthenticated":1}',
                       '{"status":"error","message":"private-token"}', '"private-token"'):
            with self.subTest(output=output), patch.object(agent, "exec_retry", return_value=types.SimpleNamespace(
                    returncode=0, stdout=output, stderr="private-token")):
                with self.assertRaises(RuntimeError) as raised:
                    agent.cursor_auth_status("sandbox")
                self.assertNotIn("private-token", str(raised.exception))

    def test_auth_status_timeout_is_redacted_and_never_retried(self):
        with patch.object(agent, "exec_retry", side_effect=TimeoutError("private-token")) as run:
            with self.assertRaisesRegex(RuntimeError, "No agent prompt was sent") as raised:
                agent.cursor_auth_status("sandbox")
        self.assertNotIn("private-token", str(raised.exception))
        run.assert_called_once()

    def test_unsigned_telegram_does_not_allocate_chat_or_mark_prompt_uncertain(self):
        for state in ({}, {"id": "chat-test", "started": True}):
            before = dict(state)
            with patch.object(agent, "cursor_auth_status", return_value=False), patch.object(agent, "exec_retry") as run:
                reply = agent.telegram_agent_reply("sandbox", self.harness, "prompt", state, 60,
                                                   types.SimpleNamespace(name="cursor1"))
            self.assertIn("cws-agent login cursor1", reply)
            self.assertEqual(state, before)
            run.assert_not_called()

    def test_unsigned_headless_run_returns_actionable_login_before_prompt(self):
        with patch.object(agent, "require_active", return_value="sandbox"), \
                patch.object(agent, "active_harness", return_value=self.harness), \
                patch.object(agent, "cursor_auth_status", return_value=False), \
                patch.object(agent, "exec_retry") as run:
            with self.assertRaisesRegex(SystemExit, "cws-agent login cursor1"):
                agent.cmd_run(types.SimpleNamespace(name="cursor1", prompt="prompt", timeout=60))
        run.assert_not_called()

    def test_auth_check_failure_does_not_change_telegram_session(self):
        state = {}
        with patch.object(agent, "cursor_auth_status", side_effect=RuntimeError("Could not check Cursor sign-in")), \
                patch.object(agent, "exec_retry") as run:
            reply = agent.telegram_agent_reply("sandbox", self.harness, "prompt", state, 60)
        self.assertEqual(reply, "Could not check Cursor sign-in")
        self.assertEqual(state, {})
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
