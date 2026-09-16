"""Offline SDK/PTY and documented attach/login/rc command checks.

Run with: uv run --with 'cwsandbox>=1.1' python -m unittest discover -s tests
Set CWS_TEST_REF to a git revision to demonstrate regressions against that code.
"""
import io
import os
import pty
import signal
import shutil
import subprocess
import sys
import tempfile
import termios
import threading
import types
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch

from cwsandbox import Sandbox
from cwsandbox._types import OperationRef, StreamReader, StreamWriter, TerminalSession


ROOT = Path(__file__).resolve().parents[1]
if os.environ.get("CWS_TEST_REF"):
    ref = os.environ["CWS_TEST_REF"]
    path = "cws-agent.py"
    if subprocess.run(["git", "cat-file", "-e", ref + ":" + path], cwd=ROOT,
                      stderr=subprocess.DEVNULL).returncode:
        path = "cws-agent"  # Revisions before the source rename.
    source = subprocess.check_output(["git", "show", ref + ":" + path], cwd=ROOT).decode()
else:
    source = (ROOT / "cws-agent.py").read_text()
agent = types.ModuleType("documented_terminal_agent")
sys.modules[agent.__name__] = agent
exec(compile(source, str(ROOT / "cws-agent.py"), "exec"), agent.__dict__)


def completed(value=None):
    future = Future()
    future.set_result(value)
    return OperationRef(future)


class TerminalFile:
    def __init__(self, fd):
        self.fd = fd
        self.buffer = io.BytesIO()

    def fileno(self):
        return self.fd

    def isatty(self):
        return True


class Output:
    def __init__(self, chunks, close=None):
        self.chunks = chunks
        self.close = close or Mock(spec=StreamReader.close)

    def __iter__(self):
        return iter(self.chunks)


class DocumentedTerminalTests(unittest.TestCase):
    def setUp(self):
        self.master, self.slave = pty.openpty()
        termios.tcsetwinsize(self.slave, (37, 123))
        self.stdin = TerminalFile(self.slave)
        self.stdout = TerminalFile(self.slave)
        self.session = Mock(spec=TerminalSession)
        self.session.returncode = None
        self.session.wait.return_value = 0
        self.session.stdin = Mock(spec=StreamWriter)
        self.session.stdin.write.return_value = completed()
        self.session.stdin.close.return_value = completed()
        self.session.output = Output([b"\x1b[H\xf0\x9f", b"\x91\xbb\r\n\xff"])
        self.sandbox = Mock(spec=Sandbox)
        self.sandbox.shell.return_value = self.session
        self.existing_threads = set(threading.enumerate())

    def tearDown(self):
        os.close(self.master)
        os.close(self.slave)
        # Also release the broken baseline's leaked reader after an assertion.
        for thread in set(threading.enumerate()) - self.existing_threads:
            thread.join(timeout=0.5)

    def attach(self):
        with patch.object(sys, "stdin", self.stdin), patch.object(sys, "stdout", self.stdout):
            return agent.pty_attach(self.sandbox, "exec bash")

    def test_attach_reads_live_geometry_despite_stale_environment(self):
        with patch.dict(os.environ, {"COLUMNS": "80", "LINES": "24"}):
            self.attach()
        self.assertEqual(self.sandbox.shell.call_args.kwargs, {"width": 123, "height": 37})

    def test_attach_forwards_terminal_capabilities_and_raw_bytes(self):
        with patch.dict(os.environ, {"TERM": "xterm-ghostty", "TERM_PROGRAM": "ghostty"}):
            self.assertEqual(self.attach(), 0)
        script = self.sandbox.shell.call_args.args[0][2]
        self.assertIn("export TERM=", script)
        self.assertIn("xterm-256color", script)
        self.assertEqual(self.stdout.buffer.getvalue(), b"\x1b[H\xf0\x9f\x91\xbb\r\n\xff")

    def test_placeholder_term_types_get_interactive_capability_fallback(self):
        for term in ("", "dumb", "unknown"):
            with self.subTest(term=term), patch.dict(os.environ, {"TERM": term}):
                script = agent.terminal_env()
            result = subprocess.run(["sh", "-c", script + 'printf %s "$TERM"'], capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"xterm-256color")

    def test_sigwinch_propagates_new_geometry(self):
        resized = threading.Event()
        def resize(width, height):
            if (width, height) == (170, 50):
                resized.set()
        self.session.resize.side_effect = resize
        def chunks():
            termios.tcsetwinsize(self.slave, (50, 170))
            os.kill(os.getpid(), signal.SIGWINCH)
            self.assertTrue(resized.wait(2), "new PTY geometry was not forwarded")
            yield b"resized"
        self.session.output = Output(chunks())
        with patch.dict(os.environ, {"COLUMNS": "80", "LINES": "24"}):
            self.attach()

    def test_attach_releases_stdin_reader(self):
        self.attach()
        alive = [t for t in set(threading.enumerate()) - self.existing_threads
                 if t.is_alive() and "forward_stdin" in t.name]
        self.assertEqual(alive, [], "attach left a reader that can consume local shell input")
        self.session.output.close.assert_called()

    def test_cleanup_failure_still_restores_termios_and_handler(self):
        original = termios.tcgetattr(self.slave)
        handler = signal.getsignal(signal.SIGWINCH)
        self.session.output.close.side_effect = RuntimeError("transport broken")
        self.attach()
        restored = termios.tcgetattr(self.slave)
        for settings in (original, restored):
            settings[3] &= ~getattr(termios, "PENDIN", 0)  # macOS kernel bookkeeping
        self.assertEqual(restored, original)
        self.assertEqual(signal.getsignal(signal.SIGWINCH), handler)

    def test_input_failure_ends_attach_with_error(self):
        closed = threading.Event()
        self.session.output.close.side_effect = closed.set
        self.session.stdin.write.side_effect = RuntimeError("write failed")
        def chunks():
            os.write(self.master, b"hello")
            self.assertTrue(closed.wait(2), "failed stdin write did not close the stream")
            return
            yield
        self.session.output.chunks = chunks()
        with patch.object(sys, "stderr", io.StringIO()) as error:
            self.assertEqual(self.attach(), 1)
        self.assertIn("write failed", error.getvalue())


class DocumentedLoginAndRemoteControlTests(unittest.TestCase):
    def test_attach_dispatches_default_and_custom_commands(self):
        sandbox = Mock(spec=Sandbox)
        for command, expected in [(None, "exec claude --permission-mode acceptEdits"), ("bash", "exec sh -c bash"), ("tmux attach -t outpost-0", "exec sh -c 'tmux attach -t outpost-0'")]:
            with patch.object(agent, "require_active", return_value=sandbox), patch.object(agent, "active_harness", return_value=agent.HARNESSES["claude"]), patch.object(agent, "pty_attach", return_value=0) as attach:
                self.assertEqual(agent.cmd_attach(types.SimpleNamespace(name="dev1", agent=None, cmd=command, yolo=False, permission_mode="accept-edits", no_config_sync=True)), 0)
                attach.assert_called_once_with(sandbox, expected)

    def test_custom_attach_runs_every_command_in_compound_shell_expression(self):
        with patch.object(agent, "require_active"), patch.object(agent, "active_harness", return_value=agent.HARNESSES["claude"]), patch.object(agent, "pty_attach", return_value=0) as attach:
            agent.cmd_attach(types.SimpleNamespace(name="dev1", agent=None,
                                                  cmd="printf FIRST; printf SECOND", yolo=False, permission_mode="accept-edits"))
        result = subprocess.run(["sh", "-c", attach.call_args.args[1]], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"FIRSTSECOND")

    def test_login_messages_match_selected_cli(self):
        for harness_name, text in [("claude", "/login"), ("codex", "Codex"), ("devin", "devin auth login")]:
            with self.subTest(agent=harness_name), patch.object(agent, "require_active"), patch.object(agent, "active_harness", return_value=agent.HARNESSES[harness_name]), patch.object(agent, "pty_attach", return_value=0) as attach, patch.object(sys, "stdout", io.StringIO()) as output:
                self.assertEqual(agent.cmd_login(types.SimpleNamespace(name="dev1")), 0)
                self.assertIn(text, output.getvalue())
                self.assertTrue(attach.call_args.args[1].endswith(agent.HARNESSES[harness_name].login_cmd))
                if harness_name == "claude":
                    self.assertIn("unset CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY", attach.call_args.args[1])

    def test_worker_backend_has_no_interactive_login(self):
        with patch.object(agent, "require_active"), patch.object(agent, "active_harness", return_value=agent.HARNESSES["ant"]), patch.object(agent, "pty_attach") as attach:
            with self.assertRaisesRegex(SystemExit, "interactive login"):
                agent.cmd_login(types.SimpleNamespace(name="worker"))
            attach.assert_not_called()

    def test_remote_control_propagates_failure_and_diagnostics(self):
        result = types.SimpleNamespace(returncode=17, stdout="startup failed\n", stderr="auth required\n")
        with patch.object(agent, "require_active"), patch.object(agent, "probe_session_meta", return_value=("dev1", "claude")), patch.object(agent, "exec_retry", return_value=result), patch.object(sys, "stdout", io.StringIO()) as output, patch.object(sys, "stderr", io.StringIO()) as error:
            self.assertEqual(agent.cmd_rc(types.SimpleNamespace(name="dev1")), 17)
        self.assertIn("startup failed", output.getvalue())
        self.assertIn("auth required", error.getvalue())
        self.assertNotIn("is running", output.getvalue())

    def test_remote_control_rejects_other_harnesses(self):
        for harness in ("devin", "codex", "ant"):
            with patch.object(agent, "require_active"), patch.object(agent, "probe_session_meta", return_value=("box", harness)), patch.object(agent, "exec_retry") as execute:
                with self.assertRaisesRegex(SystemExit, "Claude Code"):
                    agent.cmd_rc(types.SimpleNamespace(name="box"))
                execute.assert_not_called()

    def test_remote_control_shell_detects_exit_and_avoids_duplicate_processes(self):
        for mode, expected in [("failed", 1), ("running", 0), ("already", 0)]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="cws-rc-test-") as directory:
                root = Path(directory)
                log = root / "remote-control.log"
                log.write_text("existing URL\n")
                (root / "sleep").write_text("#!/bin/sh\nexit 0\n")
                (root / "sleep").chmod(0o755)
                (root / "tmux").write_text("""#!/bin/sh
case "$1" in
  has-session)
    [ "$RC_MODE" = already ] && exit 0
    [ "$RC_MODE" = running ] && [ -f "$RC_STATE" ] && exit 0
    exit 1 ;;
  new-session)
    printf launched > "$RC_STATE"
    printf 'simulated agent startup\\n' > "$RC_LOG"
    exit 0 ;;
esac
exit 2
""")
                (root / "tmux").chmod(0o755)
                env = {"PATH": directory + ":/usr/bin:/bin", "RC_MODE": mode,
                       "RC_STATE": str(root / "launched"), "RC_LOG": str(log)}
                def execute(sandbox, command, **kwargs):
                    self.assertIn("unset CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", command[2])
                    return subprocess.run(["sh", "-c", command[2]], env=env, capture_output=True, text=True, timeout=5)
                with patch.object(agent, "HOME_DIR", directory), patch.object(agent, "SH_WRAP", "{cmd}"), patch.object(agent, "AGENT_ENV", ""), patch.object(agent, "require_active"), patch.object(agent, "probe_session_meta", return_value=("dev1", "claude")), patch.object(agent, "exec_retry", side_effect=execute), patch.object(sys, "stdout", io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
                    self.assertEqual(agent.cmd_rc(types.SimpleNamespace(name="dev1")), expected)
                self.assertEqual((root / "launched").exists(), mode != "already")

    def test_all_harness_bootstrap_scripts_parse_as_posix_shell(self):
        for name, harness in agent.HARNESSES.items():
            with self.subTest(agent=name):
                result = subprocess.run(["sh", "-n"], input=harness.bootstrap, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_vendor_help_accepts_documented_flags_without_auth(self):
        checked = 0
        # Help only: no login, remote-control startup, model prompt, or user
        # configuration. Each executable receives an empty temporary HOME.
        for binary, arguments, expected in (
            ("claude", ["--help"], ("--print", "--output-format", "--dangerously-skip-permissions")),
            ("codex", ["login", "--help"], ("--with-api-key",)),
            ("codex", ["exec", "--help"], ("--dangerously-bypass-approvals-and-sandbox",)),
        ):
            executable = shutil.which(binary)
            if not executable:
                continue
            with self.subTest(binary=binary, arguments=arguments), tempfile.TemporaryDirectory(prefix="cws-cli-help-") as directory:
                env = {"HOME": directory, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TERM": "dumb"}
                result = subprocess.run([executable, *arguments], env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                for flag in expected:
                    self.assertIn(flag, result.stdout)
                checked += 1
        if not checked:
            self.skipTest("no locally installed vendor CLIs; remaining tests are fully offline")
