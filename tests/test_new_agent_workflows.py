"""Offline integration contracts for OpenCode and Cursor lifecycle and messaging."""
import contextlib
import io
import json
import shlex
import types
import unittest
from unittest.mock import patch

from test_consolidation import cli_parser
from test_terminal import agent as cli


def result(stdout="", returncode=0):
    return types.SimpleNamespace(stdout=stdout, stderr="private provider error", returncode=returncode)


def events(sid="ses_test", text="**Done**", reason="stop"):
    return "\n".join(json.dumps(e) for e in [
        {"type": "step_start", "sessionID": sid, "part": {}},
        {"type": "tool_use", "sessionID": sid, "part": {"state": {"output": "private tool output"}}},
        {"type": "text", "sessionID": sid, "part": {"id": "prt_text", "text": text}},
        {"type": "step_finish", "sessionID": sid, "part": {"reason": reason}},
    ])


class NewAgentWorkflowTests(unittest.TestCase):
    def test_all_cli_entrypoints_accept_new_harnesses(self):
        parser = cli_parser()
        for name in ("opencode", "cursor"):
            for argv in (["launch", "--name", "test"], ["restore", "test"],
                         ["connect", "test"], ["session", "start", "test", "task"],
                         ["session", "restart", "test", "task"],
                         ["session", "history", "test"],
                         ["session", "resume", "test", "native-id"],
                         ["session", "transfer", "test", "--upload", "native-id"]):
                with self.subTest(name=name, argv=argv):
                    self.assertEqual(parser.parse_args([*argv, "--agent", name]).agent, name)

    def test_native_resume_and_latest_use_provider_syntax(self):
        self.assertEqual(cli.native_resume_command("opencode", "ses_test"), "opencode --session ses_test")
        self.assertEqual(cli.native_resume_command("opencode"), "opencode --continue")
        self.assertEqual(cli.native_resume_command("cursor", "chat-test"), "cursor-agent --resume chat-test")
        self.assertEqual(cli.native_resume_command("cursor"), "cursor-agent resume")
        for name in ("cursor", "opencode"):
            with self.assertRaises(SystemExit):
                cli.native_resume_command(name, "--force; false")

    def test_login_routes_to_dedicated_native_flow(self):
        for name in ("cursor", "opencode"):
            with self.subTest(name=name), contextlib.redirect_stdout(io.StringIO()), \
                    patch.object(cli, "require_active", return_value="sandbox"), \
                    patch.object(cli, "active_harness", return_value=cli.HARNESSES[name]), \
                    patch.object(cli, "pty_attach", return_value=0) as terminal:
                self.assertEqual(cli.main(["login", "test"]), 0)
                terminal.assert_called_once_with("sandbox", cli.HARNESSES[name].login_cmd)

    def test_cursor_history_is_native_picker_not_guessed_private_database(self):
        with patch.object(cli, "require_active", return_value="sandbox"), \
                patch.object(cli, "pty_attach", return_value=0) as terminal:
            self.assertEqual(cli.main(["session", "history", "test", "--agent", "cursor"]), 0)
            terminal.assert_called_once_with("sandbox", "exec cursor-agent ls")
            with self.assertRaisesRegex(SystemExit, "not a documented JSON"):
                cli.main(["session", "history", "test", "--agent", "cursor", "--json"])

    def test_cursor_transfer_is_rejected_before_any_sandbox_access(self):
        with patch.object(cli, "require_active") as active, \
                self.assertRaisesRegex(SystemExit, "no documented native session import/export"):
            cli.main(["session", "transfer", "test", "--upload", "chat-test", "--agent", "cursor"])
        active.assert_not_called()

    def test_cursor_resume_uses_explicit_directory(self):
        with patch.object(cli, "require_active", return_value="sandbox"), \
                patch.object(cli, "remote_native_history") as history, \
                patch.object(cli, "pty_attach", return_value=0) as terminal:
            cli.main(["session", "resume", "test", "chat-test", "--agent", "cursor",
                      "--cwd", "/workspace/project with spaces", "--permission-mode", "native"])
        history.assert_not_called()
        command = terminal.call_args.args[1]
        self.assertIn("cd '/workspace/project with spaces' && exec cursor-agent --resume chat-test", command)
        self.assertNotIn("--force", command)

    def test_opencode_history_uses_native_metadata_api_and_omits_titles(self):
        output = json.dumps([{"id": "ses_test", "directory": "/work", "updated": 1000,
                              "title": "private conversation title"}])
        with patch("shutil.which", return_value="/bin/opencode"), \
                patch("subprocess.run", return_value=result(output)) as run:
            rows = cli.scan_opencode_history()
        self.assertEqual(rows, [{"agent": "opencode", "id": "ses_test", "cwd": "/work", "modified": 1.0}])
        self.assertEqual(run.call_args.args[0], ["/bin/opencode", "session", "list", "--format", "json", "--max-count", "1000"])

    def test_opencode_history_missing_binary_and_invalid_response(self):
        with patch("shutil.which", return_value=None):
            self.assertEqual(cli.scan_opencode_history(), [])
        for output in ('{}', '[{"id":"--bad", "directory":"/work"}]', 'SECRET'):
            with patch("shutil.which", return_value="/bin/opencode"), \
                    patch("subprocess.run", return_value=result(output)), \
                    self.assertRaisesRegex(RuntimeError, "invalid history metadata"):
                cli.scan_opencode_history()

    def test_remote_history_helper_is_self_contained_and_checks_known_projects(self):
        for cwd in (None, "/workspace/another project"):
            with patch.object(cli, "exec_retry", return_value=result("[]")) as execute:
                self.assertEqual(cli.remote_native_history("sandbox", opencode_cwd=cwd), [])
            script = execute.call_args.args[1][2]
            compile(script, "remote-native-history", "exec")
            self.assertIn("def scan_opencode_history", script)
            if cwd:
                self.assertIn(repr(cwd), script)
                self.assertNotIn("root.iterdir()", script)
            else:
                self.assertIn("root.iterdir()", script)

    def test_opencode_history_cwd_is_explicit_and_absolute(self):
        with patch.object(cli, "require_active", return_value="sandbox"), \
                patch.object(cli, "remote_native_history", return_value=[]) as history, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["session", "history", "test", "--agent", "opencode", "--cwd", "/other"])
            history.assert_called_once_with("sandbox", opencode_cwd="/other")
        with patch.object(cli, "require_active") as active, self.assertRaises(SystemExit):
            cli.main(["session", "history", "test", "--agent", "opencode", "--cwd", "relative"])
        active.assert_not_called()

    def test_opencode_telegram_continues_same_session_and_returns_only_text(self):
        state, saved = {}, []
        prompt = "--cws-permission=bypass 'quoted' $(false)"
        with patch.object(cli, "exec_retry", return_value=result(events())) as run:
            answer = cli.telegram_agent_reply("sandbox", cli.HARNESSES["opencode"], prompt, state, 30,
                                             persist=lambda: saved.append(dict(state)))
            self.assertEqual(answer, "**Done**")
            self.assertIn(" -- " + shlex.quote(prompt), run.call_args.args[1][2])
            self.assertEqual(run.call_args.kwargs["attempts"], 1)
            self.assertTrue(saved[0]["uncertain"])
            self.assertEqual(state, {"id": "ses_test", "started": True})
            cli.telegram_agent_reply("sandbox", cli.HARNESSES["opencode"], "next", state, 30)
            self.assertIn("--session ses_test", run.call_args.args[1][2])

    def test_opencode_unfinished_error_and_mismatched_sessions_are_not_retried(self):
        for output in (events(reason="tool-calls"), events(sid="ses_other"),
                       '{"type":"error", "sessionID":"ses_test", "error":"SECRET"}', "not json"):
            state = {"id": "ses_test", "started": True}
            with patch.object(cli, "exec_retry", return_value=result(output)) as run:
                answer = cli.telegram_agent_reply("sandbox", cli.HARNESSES["opencode"], "next", state, 30)
                self.assertIn("use /new", answer)
                self.assertNotIn("SECRET", answer)
                self.assertTrue(state["uncertain"])
                cli.telegram_agent_reply("sandbox", cli.HARNESSES["opencode"], "again", state, 30)
                run.assert_called_once()

    def test_cursor_telegram_allocates_once_and_persists_before_prompt(self):
        state, saved = {}, []
        response = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                               "result": "Done", "session_id": "chat-test"})
        with patch.object(cli, "cursor_auth_status", return_value=True), \
                patch.object(cli, "exec_retry", side_effect=[result("chat-test\n"), result(response), result(response)]) as run:
            for _ in range(2):
                self.assertEqual(cli.telegram_agent_reply("sandbox", cli.HARNESSES["cursor"], "--force", state, 30,
                                 persist=lambda: saved.append(dict(state))), "Done")
            self.assertIn("cursor-agent create-chat", run.call_args_list[0].args[1][2])
            self.assertIn("--resume chat-test", run.call_args.args[1][2])
            self.assertIn(" -- --force", run.call_args.args[1][2])
            self.assertTrue(saved[0]["uncertain"])
            self.assertEqual(saved[0]["id"], "chat-test")
            self.assertEqual(state, {"id": "chat-test", "started": True})

    def test_cursor_telegram_invalid_result_keeps_uncertain_marker(self):
        state = {"id": "chat-test", "started": True}
        with patch.object(cli, "cursor_auth_status", return_value=True), \
                patch.object(cli, "exec_retry", return_value=result('{"result":"SECRET", "session_id":"other"}')) as run:
            answer = cli.telegram_agent_reply("sandbox", cli.HARNESSES["cursor"], "next", state, 30)
        self.assertTrue(state["uncertain"])
        self.assertIn("use /new", answer)
        self.assertNotIn("SECRET", answer)
        run.assert_called_once()
