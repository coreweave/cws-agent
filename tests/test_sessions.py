"""Offline contract tests: python3 -m unittest discover -s tests."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def load_cli():
    sdk = types.ModuleType("cwsandbox")
    sdk.FileSystemSnapshotOptions = sdk.ResourceOptions = sdk.Sandbox = object
    loader = importlib.machinery.SourceFileLoader("cws_agent_sessions", str(Path(__file__).resolve().parents[1] / "cws-agent"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    with patch.dict(sys.modules, {"cwsandbox": sdk}):
        loader.exec_module(module)
    return module


cli = load_cli()


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def transcript(self, path, *records):
        dest = self.home / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(json.dumps(r) for r in records) + "\n{partial")
        return dest

    def test_scan_across_projects_and_agents_without_prompts(self):
        self.transcript(".claude/projects/-first/a.jsonl", {"sessionId": "a", "cwd": "/first", "message": "secret"})
        self.transcript(".claude/projects/-second/b.jsonl", {"sessionId": "b", "cwd": "/second"})
        self.transcript(".codex/sessions/2026/09/05/rollout-c.jsonl", {"type": "session_meta", "payload": {"id": "c", "cwd": "/third"}})
        rows = cli.scan_native_history(str(self.home))
        self.assertEqual({r["id"] for r in rows}, {"a", "b", "c"})
        self.assertNotIn("secret", json.dumps(rows))

    def test_ignores_subagents_invalid_shapes_and_symlinks(self):
        self.transcript(".claude/projects/-first/child.jsonl", {"sessionId": "child", "cwd": "/first", "isSidechain": True})
        self.transcript(".claude/projects/-first/garbage.jsonl", [], {"sessionId": "--bad", "cwd": "/first"})
        target = self.transcript("outside.jsonl", {"sessionId": "linked", "cwd": "/first"})
        (self.home / ".claude/projects/-first/linked.jsonl").symlink_to(target)
        self.assertEqual(cli.scan_native_history(str(self.home)), [])

    def test_native_commands_and_reject_option_injection(self):
        self.assertEqual(cli.native_resume_command("claude", "abc"), "claude --resume abc")
        self.assertEqual(cli.native_resume_command("codex", "abc"), "codex resume abc")
        self.assertEqual(cli.native_resume_command("devin", "brisk-otter"), "devin --resume brisk-otter")
        for value in ("--last", "../bad", "a;touch /tmp/bad"):
            with self.assertRaises(SystemExit):
                cli.native_resume_command("claude", value)

    def test_restart_live_session_only_attaches(self):
        for attach_requested in (False, True):
            args = types.SimpleNamespace(name="dev", session="fix", agent=None, session_id=None,
                                         attach=attach_requested)
            with self.subTest(attach=attach_requested), patch.object(cli, "require_active", return_value=object()), patch.object(cli, "read_session_sessions", return_value=[{"name": "fix", "alive": True}]), patch.object(cli, "session_attach", return_value=0) as attach, patch.object(cli, "exec_retry") as run:
                self.assertEqual(cli.cmd_session_restart(args), 0)
                self.assertEqual(attach.call_count, int(attach_requested))
                run.assert_not_called()

    def test_restart_keeps_existing_worktree_and_saved_agent(self):
        args = types.SimpleNamespace(name="dev", session="fix", agent=None, session_id="abc", attach=False)
        result = types.SimpleNamespace(stdout="codex", stderr="", returncode=0)
        with patch.object(cli, "require_active", return_value=object()), patch.object(cli, "read_session_sessions", return_value=[{"name": "fix", "alive": False}]), patch.object(cli, "exec_retry", return_value=result) as run:
            self.assertEqual(cli.cmd_session_restart(args), 0)
            cmd = run.call_args_list[-1].args[1]
            self.assertIn("/workspace/sessions/fix", cmd)
            self.assertTrue(cmd[-1].endswith("exec codex resume abc --sandbox workspace-write --ask-for-approval on-request"))
            self.assertNotIn("worktree add", repr(run.call_args_list))

    def test_resume_uses_recorded_directory(self):
        args = types.SimpleNamespace(name="dev", agent=None, session_id="abc", cwd=None)
        with patch.object(cli, "require_active", return_value=object()), patch.object(cli, "remote_native_history", return_value=[{"agent": "claude", "id": "abc", "cwd": "/workspace/sessions/my work"}]), patch.object(cli, "pty_attach", return_value=0) as attach:
            cli.cmd_session_resume(args)
        self.assertEqual(attach.call_args.args[1], "cd '/workspace/sessions/my work' && exec claude --resume abc --permission-mode acceptEdits")

    def test_resume_parser_routes_without_cloud(self):
        with patch.object(cli, "cmd_session_resume", return_value=0) as command:
            self.assertEqual(cli.main(["session", "resume", "dev", "brisk-otter", "--agent", "devin"]), 0)
            self.assertEqual(command.call_args.args[0].session_id, "brisk-otter")

    def test_initial_prompt_is_not_a_cli_option_or_subcommand(self):
        import shlex
        result = types.SimpleNamespace(stdout="", stderr="", returncode=0)
        for name in ("claude", "codex", "devin"):
            agent = cli.HARNESSES[name]
            for prompt in ("--version", "help"):
                with self.subTest(agent=agent.name, prompt=prompt), \
                     patch.object(cli, "require_active"), \
                     patch.object(cli, "active_harness", return_value=agent), \
                     patch.object(cli, "ensure_project_repo", return_value="main"), \
                     patch.object(cli, "exec_retry", return_value=result) as execute:
                    cli.main(["session", "start", "dev1", "task", "--prompt=" + prompt])
                    self.assertEqual(shlex.split(execute.call_args.args[1][-1])[-2:], ["--", prompt])

    def test_worker_backend_cannot_start_cli_worktree(self):
        with patch.object(cli, "require_active", return_value=object()), \
                patch.object(cli, "active_harness", return_value=cli.HARNESSES["ant"]), \
                patch.object(cli, "exec_retry") as execute, \
                self.assertRaisesRegex(SystemExit, "worktree sessions require"):
            cli.main(["session", "start", "managed", "task"])
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
