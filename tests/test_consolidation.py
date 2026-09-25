"""Guard the combined UX features and worker backend boundaries after merging."""
import argparse
import ast
from collections import Counter
import os
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

from cwsandbox import Sandbox
from test_terminal import agent


ROOT = Path(__file__).resolve().parents[1]


def cli_parser():
    captured = []
    def capture(parser, argv=None, **kwargs):
        captured.append(parser)
        return argparse.Namespace(func=lambda args: 0)
    with patch.object(argparse.ArgumentParser, "parse_args", autospec=True, side_effect=capture):
        agent.main([])
    return captured[0]


def subcommands(parser):
    return next(action.choices for action in parser._actions
                if isinstance(action, argparse._SubParsersAction))


class ConsolidationTests(unittest.TestCase):
    def test_top_level_functions_and_classes_are_unique(self):
        tree = ast.parse((ROOT / "cws-agent.py").read_text())
        names = Counter(node.name for node in tree.body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        self.assertEqual({name: count for name, count in names.items() if count > 1}, {})
        self.assertEqual(names["main"], 1)  # embedded helper scripts aren't CLI entrypoints

    def test_cli_command_families_and_subcommands_are_retained_once(self):
        top = subcommands(cli_parser())  # argparse rejects duplicate registrations
        self.assertEqual(set(top), {"shell", "launch", "connect", "attach", "run", "login", "exec", "sync", "uploads",
                                    "snapshot", "checkpoint", "down", "restore", "resume", "list", "status", "snapshots",
                                    "prune", "rc", "session", "bridge", "discord", "config"})
        self.assertEqual(set(subcommands(top["session"])),
                         {"start", "attach", "ls", "history", "transfer", "resume", "restart", "diff", "stop"})
        self.assertEqual(set(subcommands(top["bridge"])), {"telegram"})
        self.assertEqual(set(subcommands(top["config"])), {"preview", "sync"})

    def test_all_eight_ux_features_remain_reachable(self):
        parser = cli_parser()
        # Terminal, clipboard and image paste share the attach command.
        self.assertEqual(parser.parse_args(["attach", "dev1"]).func, agent.cmd_attach)
        for name in ("terminal_size", "terminal_env", "clipboard_setup", "clipboard_image", "upload_clipboard_image"):
            self.assertTrue(callable(getattr(agent, name)))
        self.assertTrue(callable(agent.ImagePasteInput))
        # Native session management, transfer, messaging, permissions, config.
        examples = (
            (["session", "history", "dev1"], agent.cmd_session_history),
            (["session", "resume", "dev1", "session-id", "--agent", "claude"], agent.cmd_session_resume),
            (["session", "restart", "dev1", "worker"], agent.cmd_session_restart),
            (["session", "transfer", "dev1", "--upload", "session-id"], agent.cmd_session_transfer),
            (["session", "transfer", "dev1", "--download", "session-id"], agent.cmd_session_transfer),
            (["bridge", "telegram", "dev1", "--allow-chat", "1", "--allow-user", "2"], agent.cmd_bridge_telegram),
            (["config", "preview", "dev1"], agent.cmd_config),
            (["config", "sync", "dev1", "--select", "skill:example", "--yes"], agent.cmd_config),
        )
        for argv, function in examples:
            with self.subTest(argv=argv):
                self.assertEqual(parser.parse_args(argv).func, function)
        args = parser.parse_args(["launch", "--name", "dev1", "--dangerously-skip-permissions", "--no-config-sync"])
        self.assertTrue(args.yolo)
        self.assertTrue(args.no_config_sync)
        self.assertIsNone(parser.parse_args(["attach", "dev1"]).permission_mode)

    def test_managed_worker_default_attach_skips_local_config_discovery(self):
        sandbox = Mock(spec=Sandbox)
        args = types.SimpleNamespace(name="workerbox", agent=None, cmd=None,
                                     yolo=False, permission_mode="accept-edits", no_config_sync=False)
        with patch.object(agent, "require_active", return_value=sandbox), patch.object(agent, "active_harness", return_value=agent.HARNESSES["ant"]), patch.object(agent, "discover_imports") as discover, patch.object(agent, "pty_attach", return_value=0) as attach:
            self.assertEqual(agent.cmd_attach(args), 0)
        discover.assert_not_called()
        sandbox.exec.assert_not_called()
        sandbox.write_file.assert_not_called()
        attach.assert_called_once_with(sandbox, "exec bash")

    def test_explicit_config_rejects_managed_worker_before_imports_or_writes(self):
        sandbox = Mock(spec=Sandbox)
        for preview in (True, False):
            with self.subTest(preview=preview), patch.object(agent, "require_active", return_value=sandbox), patch.object(agent, "active_harness", return_value=agent.HARNESSES["ant"]), patch.object(agent, "sync_agent_config") as sync:
                with self.assertRaisesRegex(SystemExit, "agent CLIs"):
                    agent.cmd_config(types.SimpleNamespace(name="workerbox", preview=preview, select=["all"], yes=True))
                sync.assert_not_called()
        sandbox.exec.assert_not_called()
        sandbox.write_file.assert_not_called()

    def test_telegram_rejects_managed_worker_before_state_writes_or_api_calls(self):
        sandbox = Mock(spec=Sandbox)
        args = types.SimpleNamespace(name="workerbox", timeout=30, allow_chat=[1], allow_user=[2])
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:offline_test_token"}), patch.object(agent, "require_active", return_value=sandbox), patch.object(agent, "active_harness", return_value=agent.HARNESSES["ant"]), patch.object(agent, "telegram_api") as api, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaisesRegex(SystemExit, "agent CLIs"):
                agent.cmd_bridge_telegram(args)
        api.assert_not_called()
        mkdir.assert_not_called()
        sandbox.exec.assert_not_called()
        sandbox.write_file.assert_not_called()

    def test_worker_harness_is_not_a_native_cli_parser_choice(self):
        parser = cli_parser()
        choices = subcommands(subcommands(parser)["session"])
        for command in ("start", "history", "transfer", "resume", "restart"):
            action = next(action for action in choices[command]._actions if action.dest == "agent")
            self.assertEqual(set(action.choices), {"claude", "codex", "devin", "opencode", "cursor"}, command)
        # Worker launch still accepts its backend harness.
        launch = subcommands(parser)["launch"]
        action = next(action for action in launch._actions if action.dest == "agent")
        self.assertIn("ant", action.choices)

    def test_documented_smoke_script_is_executable(self):
        self.assertTrue(os.access(ROOT / "smoke.sh", os.X_OK), "README ./smoke.sh must be executable")
