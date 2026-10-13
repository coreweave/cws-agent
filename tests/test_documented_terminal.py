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
        with patch.object(agent, "require_active"), patch.object(agent, "probe_session_meta", return_value=("dev1", "claude")), patch.object(agent, "prepare_claude_rc"), patch.object(agent, "exec_retry", return_value=result), patch.object(sys, "stdout", io.StringIO()) as output, patch.object(sys, "stderr", io.StringIO()) as error:
            self.assertEqual(agent.cmd_rc(types.SimpleNamespace(name="dev1")), 17)
        self.assertIn("startup failed", output.getvalue())
        self.assertIn("auth required", error.getvalue())
        self.assertNotIn("is running", output.getvalue())

    def test_remote_control_server_gets_a_permission_mode_it_accepts(self):
        args = types.SimpleNamespace(name="dev1", permission_mode=None, yolo=False)
        self.assertEqual(agent.remote_control_permission_flag(args), " --permission-mode bypassPermissions")
        args.permission_mode = "accept-edits"
        self.assertEqual(agent.remote_control_permission_flag(args), " --permission-mode acceptEdits")

    def test_remote_control_prep_trusts_project_and_installs_login_privately(self):
        with tempfile.TemporaryDirectory(prefix="cws-rc-prep-") as home:
            Path(home, ".claude.json").write_text('{"theme": "light", "projects": {"/other": {"x": 1}}}')
            login = {"oauth": {"refreshToken": "example-refresh"}, "account": {"emailAddress": "user@example.test"}}
            subprocess.run([sys.executable, "-c", agent.CLAUDE_RC_PREP_SCRIPT, home, "/workspace/project"],
                           input=agent.json.dumps(login), text=True, check=True, timeout=10)
            state = agent.json.loads(Path(home, ".claude.json").read_text())
            creds = Path(home, ".claude", ".credentials.json")
            self.assertEqual(state["theme"], "light")
            self.assertEqual(state["projects"]["/other"], {"x": 1})
            self.assertTrue(state["projects"]["/workspace/project"]["hasTrustDialogAccepted"])
            self.assertTrue(state["remoteDialogSeen"])
            self.assertEqual(state["oauthAccount"], login["account"])
            self.assertEqual(agent.json.loads(creds.read_text()), {"claudeAiOauth": login["oauth"]})
            self.assertEqual(creds.stat().st_mode & 0o777, 0o600)

    def test_remote_control_prep_replaces_planted_temp_files_without_following_them(self):
        with tempfile.TemporaryDirectory(prefix="cws-rc-prep-") as home:
            claude_dir = Path(home, ".claude")
            claude_dir.mkdir()
            outside = Path(home, "outside.json")
            outside.write_text("untouched")
            (claude_dir / ".credentials.json.tmp").symlink_to(outside)
            Path(home, ".claude.json.tmp").write_text("{}")
            Path(home, ".claude.json.tmp").chmod(0o644)
            login = {"oauth": {"refreshToken": "example-refresh"}, "account": {}}
            subprocess.run([sys.executable, "-c", agent.CLAUDE_RC_PREP_SCRIPT, home, "/workspace/project"],
                           input=agent.json.dumps(login), text=True, check=True, timeout=10)
            creds = claude_dir / ".credentials.json"
            self.assertEqual(outside.read_text(), "untouched")
            self.assertEqual(creds.stat().st_mode & 0o777, 0o600)
            self.assertFalse(creds.is_symlink())
            self.assertEqual(Path(home, ".claude.json").stat().st_mode & 0o777, 0o600)

    def test_remote_control_prep_follows_a_custom_claude_config_dir(self):
        with tempfile.TemporaryDirectory(prefix="cws-rc-prep-") as home:
            config_dir = Path(home, "claude-state")
            login = {"oauth": {"refreshToken": "example-refresh"}, "account": {"emailAddress": "user@example.test"}}
            subprocess.run([sys.executable, "-c", agent.CLAUDE_RC_PREP_SCRIPT, home, "/workspace/project"],
                           input=agent.json.dumps(login), text=True, check=True, timeout=10,
                           env={**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)})
            state = agent.json.loads((config_dir / ".claude.json").read_text())
            self.assertTrue(state["projects"]["/workspace/project"]["hasTrustDialogAccepted"])
            self.assertTrue((config_dir / ".credentials.json").is_file())
            self.assertFalse(Path(home, ".claude", ".credentials.json").exists())

    def test_local_login_reads_the_keychain_entry_of_a_custom_profile(self):
        with tempfile.TemporaryDirectory(prefix="cws-login-") as config_dir:
            Path(config_dir, ".claude.json").write_text('{"oauthAccount": {"emailAddress": "work@example.test"}}')
            services = []
            def security(command, **kwargs):
                services.append(command[command.index("-s") + 1])
                return types.SimpleNamespace(returncode=0, stdout='{"claudeAiOauth": {"refreshToken": "work"}}')
            with patch.object(sys, "platform", "darwin"), patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": config_dir}), patch("subprocess.run", side_effect=security):
                login = agent.local_claude_login()
        digest = agent.hashlib.sha256(config_dir.encode()).hexdigest()[:8]
        self.assertEqual(services, [f"Claude Code-credentials-{digest}"])
        self.assertEqual(login["account"]["emailAddress"], "work@example.test")

    def test_remote_control_status_ignores_conversation_text_that_quotes_it(self):
        with tempfile.TemporaryDirectory(prefix="cws-rc-status-") as home:
            transcript = Path(home, ".claude", "projects", "-workspace-project", "abc.jsonl")
            transcript.parent.mkdir(parents=True)
            quoted = "/remote-control is active · Continue here, on your phone, or at https://claude.ai/code/fake"
            lines = [{"type": "user", "message": {"content": quoted}},
                     {"type": "assistant", "message": {"content": [{"type": "text", "text": quoted}]}},
                     {"type": "system", "subtype": "bridge_status",
                      "content": "/remote-control is active · Continue here, on your phone, or at https://claude.ai/code/session_real"}]
            transcript.write_text("\n".join(agent.json.dumps(line) for line in lines) + "\nnot json\n")
            result = subprocess.run([sys.executable, "-c", agent.CLAUDE_RC_STATUS_SCRIPT, home, "-workspace-project", "abc"],
                                    capture_output=True, text=True, check=True, timeout=10,
                                    env={k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"})
        status = agent.json.loads(result.stdout)
        self.assertEqual(status["count"], 1)
        self.assertTrue(status["content"].endswith("session_real"))

    def run_handoff(self, statuses, *, running=None, has_claude=True, rows=None, last=1, running_ids=frozenset()):
        row = {"agent": "claude", "id": "5fdcd1b8-7ceb-48e6-95e4-93360d3e2613", "cwd": os.getcwd(), "updated_at": 1}
        rows = rows or [row]
        nested, commands = [], []
        def execute(sandbox, command, **kwargs):
            commands.append(command[-1])
            missing = "command -v claude" in command[-1] and not has_claude
            return types.SimpleNamespace(returncode=1 if missing else 0, stdout="pane", stderr="")
        args = types.SimpleNamespace(session_id=None, dir=None, name="proj", ttl=3600, env=[], exclude=[],
                                     verbose=False, permission_mode=None, yolo=False, last=last)
        output = io.StringIO()
        with patch.object(agent, "scan_native_history", return_value=rows), patch.object(agent, "running_claude_sessions", return_value=running_ids), patch.object(agent, "local_claude_login", return_value={"oauth": {}, "account": {}}), patch.object(agent, "find_active", return_value=running), patch.object(agent, "main", side_effect=lambda argv: nested.append(argv) or 0), patch.object(agent, "require_active"), patch.object(agent, "prepare_claude_rc") as prepare, patch.object(agent, "exec_retry", side_effect=execute), patch.object(agent, "remote_control_status", side_effect=statuses), patch.object(agent.time, "sleep"), patch.dict(os.environ), patch.object(sys, "stdout", output), patch.object(sys, "stderr", io.StringIO()):
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            try:
                code = agent.cmd_handoff(args)
            except SystemExit as error:
                code = error
        return code, nested, commands, output.getvalue(), prepare

    def test_handoff_creates_sandbox_transfers_and_reports_the_new_connection(self):
        url = "https://claude.ai/code/session_new"
        statuses = [{"count": 0, "content": ""}, {"count": 0, "content": ""},
                    {"count": 1, "content": f"/remote-control is active · Continue here, on your phone, or at {url}"}]
        code, nested, commands, output, prepare = self.run_handoff(statuses)
        self.assertEqual(code, 0)
        self.assertEqual(nested[0][:2], ["launch", "proj"])
        self.assertIn("--ttl", nested[0])
        self.assertEqual(nested[1], ["session", "transfer", "proj", "--upload", "5fdcd1b8-7ceb-48e6-95e4-93360d3e2613", "--replace"])
        prepare.assert_called_once()
        start = next(command for command in commands if "new-session" in command)
        self.assertIn("kill-session -t '=cws-handoff-5fdcd1b8'", start)
        self.assertIn("--resume", start)
        self.assertIn(url, output)

    def test_handoff_last_moves_a_folders_conversations_under_their_own_titles(self):
        with tempfile.TemporaryDirectory(prefix="cws-handoff-") as directory:
            rows = []
            for index, (session_id, title) in enumerate([("aaaaaaaa-0000-4000-8000-000000000000", "Old idea"),
                                                         ("bbbbbbbb-0000-4000-8000-000000000000", "Add retry logic"),
                                                         ("cccccccc-0000-4000-8000-000000000000", None)]):
                path = Path(directory, session_id + ".jsonl")
                lines = [{"type": "user", "message": {"content": "hi"}}]
                if title:
                    lines.append({"type": "ai-title", "aiTitle": title})
                path.write_text("\n".join(agent.json.dumps(line) for line in lines) + "\n")
                rows.append({"agent": "claude", "id": session_id, "cwd": os.getcwd(), "updated_at": index, "path": str(path)})
            ready = {"count": 1, "content": "/remote-control is active · https://claude.ai/code/session_x"}
            statuses = [{"count": 0, "content": ""}, ready, {"count": 0, "content": ""}, ready]
            code, nested, commands, output, prepare = self.run_handoff(statuses, rows=rows, last=2)
        self.assertEqual(code, 0)
        self.assertEqual([argv[4] for argv in nested if argv[0] == "session"],
                         ["bbbbbbbb-0000-4000-8000-000000000000", "cccccccc-0000-4000-8000-000000000000"])
        starts = [command for command in commands if "new-session" in command]
        self.assertIn("'proj: Add retry logic'", starts[0])
        self.assertIn("cws-handoff-cccccccc", starts[1])
        self.assertIn("proj: Add retry logic", output)
        self.assertNotIn("Old idea", output)
        prepare.assert_called_once()

    def test_handoff_progress_line_fits_a_narrow_terminal_so_the_check_line_replaces_it(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        screen = Terminal()
        label = 'Start "Done command for marking items finished" with Remote Control'
        with patch.object(sys, "stdout", screen), patch.object(agent.shutil, "get_terminal_size", return_value=os.terminal_size((40, 20))):
            with agent.handoff_step(label, False):
                pass
        pending, done = screen.getvalue().split("\n")[:2]
        self.assertLessEqual(len(pending), 39)
        self.assertTrue(done.startswith("\033[1A\033[2K  ✓ Start"))

    def test_plain_handoff_moves_every_session_still_open_in_the_folder(self):
        rows = [{"agent": "claude", "id": f"{c * 8}-0000-4000-8000-000000000000", "cwd": os.getcwd(), "updated_at": i}
                for i, c in enumerate("abc")]
        ready = {"count": 1, "content": "/remote-control is active · https://claude.ai/code/session_x"}
        statuses = [{"count": 0, "content": ""}, ready] * 2
        open_ids = {rows[0]["id"], rows[1]["id"]}
        code, nested, commands, output, prepare = self.run_handoff(statuses, rows=rows, last=None, running_ids=open_ids)
        self.assertEqual(code, 0)
        self.assertEqual({argv[4] for argv in nested if argv[0] == "session"}, open_ids)
        code, nested, *_ = self.run_handoff(statuses[:2], rows=rows, last=None)
        self.assertEqual([argv[4] for argv in nested if argv[0] == "session"], [rows[2]["id"]])

    def test_running_sessions_come_from_live_claude_processes_in_the_folder(self):
        finished = subprocess.Popen([sys.executable, "-c", "pass"])
        finished.wait()
        with tempfile.TemporaryDirectory(prefix="cws-claude-") as config_dir:
            sessions = Path(config_dir, "sessions")
            sessions.mkdir()
            here = os.path.realpath(config_dir)
            for name, pid, cwd, kind in [("live", os.getpid(), here, "interactive"), ("dead", finished.pid, here, "interactive"),
                                         ("elsewhere", os.getpid(), "/", "interactive"), ("headless", os.getpid(), here, "print")]:
                Path(sessions, f"{name}.json").write_text(agent.json.dumps(
                    {"pid": pid, "sessionId": name, "cwd": cwd, "kind": kind}))
            Path(sessions, "broken.json").write_text("{")
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": config_dir}):
                self.assertEqual(agent.running_claude_sessions(here), {"live"})

    def test_handoff_sandboxes_default_to_an_eight_hour_lifetime(self):
        with patch.object(agent, "cmd_handoff", return_value=0) as handoff:
            agent.main(["handoff"])
        self.assertEqual(handoff.call_args.args[0].ttl, 8 * 3600)
        self.assertIsNone(handoff.call_args.args[0].last)

    def test_conversation_title_falls_back_to_the_first_prompt_without_a_model(self):
        with tempfile.TemporaryDirectory(prefix="cws-title-") as directory:
            path = Path(directory, "s.jsonl")
            path.write_text("\n".join(agent.json.dumps(line) for line in [
                {"type": "user", "isMeta": True, "message": {"content": "<local-command-caveat>"}},
                {"type": "user", "message": {"content": "Add retry logic to the upload client and log each attempt"}}]) + "\n")
            self.assertEqual(agent.conversation_title(str(path)), "Add retry logic to the upload client an…")

    def test_conversation_title_prefers_the_users_rename(self):
        with tempfile.TemporaryDirectory(prefix="cws-title-") as directory:
            path = Path(directory, "s.jsonl")
            path.write_text("\n".join(agent.json.dumps(line) for line in [
                {"type": "ai-title", "aiTitle": "Auto summary"}, {"type": "custom-title", "customTitle": "My\nname"},
                {"type": "ai-title", "aiTitle": "Later summary"}]) + "\nnot json\n")
            self.assertEqual(agent.conversation_title(str(path)), "My name")
            self.assertIsNone(agent.conversation_title(str(Path(directory, "missing.jsonl"))))

    def test_handoff_rerun_waits_for_a_connection_newer_than_the_replayed_one(self):
        old = {"count": 1, "content": "/remote-control is active · at https://claude.ai/code/session_old"}
        code, nested, commands, output, prepare = self.run_handoff([old] * 46, running=object())
        self.assertIsInstance(code, SystemExit)
        self.assertIn("did not start", str(code))
        self.assertEqual([argv[0] for argv in nested], ["session"])
        self.assertIn("kill-session -t '=cws-handoff-5fdcd1b8'", commands[0])
        self.assertNotIn("session_old", output)

    def test_handoff_refuses_to_reuse_a_sandbox_without_claude_before_copying_anything(self):
        code, nested, commands, output, prepare = self.run_handoff([], running=object(), has_claude=False)
        self.assertIsInstance(code, SystemExit)
        self.assertIn("no Claude Code", str(code))
        self.assertEqual(nested, [])
        prepare.assert_not_called()

    def test_bare_session_id_routes_to_handoff(self):
        session_id = "5fdcd1b8-7ceb-48e6-95e4-93360d3e2613"
        with patch.object(agent, "cmd_handoff", return_value=0) as handoff:
            self.assertEqual(agent.main([session_id]), 0)
        self.assertEqual(handoff.call_args.args[0].session_id, session_id)

    def test_handoff_refuses_agents_without_remote_control(self):
        row = {"agent": "codex", "id": "abc", "cwd": "/tmp", "updated_at": 1}
        with patch.object(agent, "scan_native_history", return_value=[row]), patch.object(agent, "find_active") as find:
            with self.assertRaisesRegex(SystemExit, "session transfer"):
                agent.cmd_handoff(types.SimpleNamespace(session_id="abc", dir=None, name=None))
            find.assert_not_called()

    def test_handoff_inside_claude_uses_the_current_session(self):
        rows = [{"agent": "claude", "id": "current", "cwd": "/elsewhere", "updated_at": 1},
                {"agent": "codex", "id": "newer", "cwd": os.getcwd(), "updated_at": 2}]
        with patch.object(agent, "scan_native_history", return_value=rows), patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "current"}), patch.object(agent, "local_claude_login", side_effect=SystemExit("stop")) as login:
            with self.assertRaisesRegex(SystemExit, "project folder /elsewhere does not exist"):
                agent.cmd_handoff(types.SimpleNamespace(session_id=None, dir=None, name=None))
            login.assert_not_called()

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
                with patch.object(agent, "HOME_DIR", directory), patch.object(agent, "SH_WRAP", "{cmd}"), patch.object(agent, "AGENT_ENV", ""), patch.object(agent, "require_active"), patch.object(agent, "probe_session_meta", return_value=("dev1", "claude")), patch.object(agent, "prepare_claude_rc"), patch.object(agent, "exec_retry", side_effect=execute), patch.object(sys, "stdout", io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
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
