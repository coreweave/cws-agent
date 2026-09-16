"""Offline command-boundary tests: python3 -m unittest discover -s tests."""
import contextlib
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import shlex
import sys
import types
import unittest
from unittest.mock import patch


def load_cli():
    sdk = types.ModuleType("cwsandbox")
    sdk.AuthStrategy = types.SimpleNamespace(WANDB="wandb", COREWEAVE_API_KEY="coreweave_api_key")
    sdk.CWSandboxAuthenticationError = type("CWSandboxAuthenticationError", (Exception,), {})
    for name in ("Sandbox", "ResourceOptions", "FileSystemSnapshotOptions"):
        setattr(sdk, name, type(name, (), {}))
    loader = importlib.machinery.SourceFileLoader("permissions_cli", str(Path(__file__).parents[1] / "cws-agent"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    with patch.dict(sys.modules, {"cwsandbox": sdk}):
        loader.exec_module(module)
    return module


cli = load_cli()


class PermissionTests(unittest.TestCase):
    def setUp(self):
        sync = patch.object(cli, "sync_agent_config")
        sync.start()
        self.addCleanup(sync.stop)

    def test_attach_default_and_bypass_for_every_agent(self):
        expected = {
            "claude": ["--permission-mode", "acceptEdits"],
            "devin": ["--permission-mode", "accept-edits"],
            "codex": ["--sandbox", "workspace-write", "--ask-for-approval", "on-request"],
            "opencode": ["--cws-permission=accept-edits"],
            "cursor": [],
        }
        for name, flags in expected.items():
            harness = cli.HARNESSES[name]
            for option in ([], ["--yolo"], ["--dangerously-skip-permissions"],
                           ["--dangerously-bypass-approvals-and-sandbox"],
                           ["--permission-mode", "accept-edits"],
                           ["--permission-mode", "native"]):
                with self.subTest(name=name, option=option), \
                     patch.object(cli, "require_active"), \
                     patch.object(cli, "active_harness", return_value=harness), \
                     patch.object(cli, "pty_attach", return_value=0) as attach:
                    self.assertEqual(cli.main(["attach", "dev1", *option]), 0)
                    result = shlex.split(attach.call_args.args[1])
                    chosen = ([] if "native" in option else
                              flags if "accept-edits" in option else shlex.split(harness.yolo_flag))
                    self.assertEqual(result, [*shlex.split(harness.interactive_cmd), *chosen])

    def test_headless_codex_policy_uses_supported_config_argument(self):
        result = types.SimpleNamespace(stdout="", stderr="", returncode=0)
        with patch.object(cli, "require_active"), \
             patch.object(cli, "active_harness", return_value=cli.HARNESSES["codex"]), \
             patch.object(cli, "exec_retry", return_value=result) as execute:
            cli.main(["run", "dev1", "fix $(touch /tmp/unwanted) 'quote'", "--permission-mode", "accept-edits"])
        script = execute.call_args.args[1][2]
        self.assertIn("--sandbox workspace-write -c 'approval_policy=\"on-request\"'", script)
        self.assertNotIn("--ask-for-approval", script)
        self.assertIn(shlex.quote("fix $(touch /tmp/unwanted) 'quote'"), script)
        self.assertIn(" --skip-git-repo-check", script)
        self.assertTrue(script.endswith(" </dev/null"), script)

    def test_headless_run_closes_stdin_for_every_agent(self):
        result = types.SimpleNamespace(stdout="", stderr="", returncode=0)
        for name in ("claude", "codex", "devin", "opencode", "cursor"):
            harness = cli.HARNESSES[name]
            with self.subTest(name=name), \
                 patch.object(cli, "require_active"), \
                 patch.object(cli, "active_harness", return_value=harness), \
                 patch.object(cli, "cursor_auth_status", return_value=True), \
                 patch.object(cli, "exec_retry", return_value=result) as execute:
                cli.main(["run", "dev1", "task"])
                script = execute.call_args.args[1][2]
                self.assertIn("IS_SANDBOX=1", script)
                self.assertIn(harness.yolo_flag, script)
                self.assertTrue(script.endswith(" </dev/null"), script)

    def test_flags_reach_all_agent_entrypoints(self):
        cases = [(["launch", "--name", "dev1"], "cmd_launch"),
                 (["attach", "dev1"], "cmd_attach"),
                 (["resume", "dev1", "--attach"], "cmd_resume"),
                 (["run", "dev1", "task"], "cmd_run"),
                 (["session", "start", "dev1", "task"], "cmd_session_start"),
                 (["session", "resume", "dev1", "session-id"], "cmd_session_resume"),
                 (["session", "restart", "dev1", "task"], "cmd_session_restart"),
                 (["bridge", "telegram", "dev1", "--allow-chat", "1", "--allow-user", "1"], "cmd_bridge_telegram"),
                 (["rc", "dev1"], "cmd_rc")]
        for argv, handler in cases:
            for options in ([], ["--yolo"], ["--permission-mode", "accept-edits"],
                            ["--permission-mode", "native"]):
                with self.subTest(argv=argv, options=options), patch.object(cli, handler, return_value=0) as command:
                    cli.main([*argv, *options])
                    args = command.call_args.args[0]
                    flags = cli.permission_flags(cli.HARNESSES["claude"], args)
                    expected = (" --permission-mode acceptEdits" if "accept-edits" in options else
                                "" if "native" in options else " --dangerously-skip-permissions")
                    self.assertEqual(flags, expected)

    def test_worker_attach_keeps_its_shell_by_default(self):
        for name in ("ant", "openai"):
            with self.subTest(name=name), patch.object(cli, "require_active"), \
                    patch.object(cli, "active_harness", return_value=cli.HARNESSES[name]), \
                    patch.object(cli, "pty_attach", return_value=0) as attach:
                cli.main(["connect", "worker"])
                self.assertEqual(attach.call_args.args[1], "exec bash")

    def test_conflicting_policy_flags_fail_before_remote_access(self):
        with patch.object(cli, "require_active") as active, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["attach", "dev1", "--yolo", "--permission-mode", "native"])
            active.assert_not_called()

    def test_custom_command_not_modified(self):
        with patch.object(cli, "require_active"), \
             patch.object(cli, "active_harness", return_value=cli.HARNESSES["claude"]), \
             patch.object(cli, "pty_attach", return_value=0) as attach:
            cli.main(["attach", "dev1", "--cmd", "bash"])
            self.assertEqual(shlex.split(attach.call_args.args[1]), ["exec", "sh", "-c", "bash"])
            with self.assertRaisesRegex(SystemExit, "--cmd"):
                cli.main(["attach", "dev1", "--cmd", "bash", "--yolo"])

    def test_worker_backend_rejects_headless_cli_run(self):
        with patch.object(cli, "require_active", return_value=object()), \
                patch.object(cli, "active_harness", return_value=cli.HARNESSES["ant"]), \
                patch.object(cli, "exec_retry") as execute, \
                self.assertRaisesRegex(SystemExit, "Managed Agents"):
            cli.main(["run", "managed", "task"])
        execute.assert_not_called()

    def test_agent_env_marks_sandbox_for_root_bypass(self):
        self.assertIn("IS_SANDBOX=1", cli.AGENT_ENV)
        self.assertIn("IS_SANDBOX=1", cli.SH_WRAP)

    def test_worker_backends_reject_bypass_before_provisioning(self):
        for backend in (["--outpost", "worker"], ["--claude-env", "env_example"]):
            with self.subTest(backend=backend), patch.object(cli, "find_active") as active:
                with self.assertRaisesRegex(SystemExit, "permission flags.*worker backends"):
                    cli.main(["launch", "--name", "dev1", *backend, "--yolo"])
                active.assert_not_called()


if __name__ == "__main__":
    unittest.main()
