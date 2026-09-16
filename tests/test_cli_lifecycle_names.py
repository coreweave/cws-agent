"""Canonical lifecycle verbs preserve the existing command contracts."""
import contextlib
import io
import unittest
from unittest.mock import patch

from test_consolidation import cli_parser, subcommands
from test_terminal import agent


class LifecycleNameTests(unittest.TestCase):
    def test_launch_accepts_positional_name_and_compatibility_flag(self):
        for argv in (["dev1"], ["--name", "dev1"],
                     ["dev1", "--agent", "codex"], ["--agent", "codex", "dev1"]):
            with self.subTest(argv=argv), patch.object(agent, "cmd_launch", return_value=0) as launch:
                self.assertEqual(agent.main(["launch", *argv]), 0)
                self.assertEqual(launch.call_args.args[0].name, "dev1")

    def test_launch_requires_exactly_one_name(self):
        for argv in ([], ["--agent", "codex"], ["dev1", "dev2"],
                     ["dev1", "--name", "dev2"], ["--name", "dev1", "dev2"]):
            with self.subTest(argv=argv), patch.object(agent, "cmd_launch") as launch, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                agent.main(["launch", *argv])
            self.assertEqual(error.exception.code, 2)
            launch.assert_not_called()

    def test_launch_validates_both_name_forms_before_remote_access(self):
        for name in ("Invalid", "-bad", "a" * 41, ""):
            for argv in ([f"--name={name}"], ["--", name]):
                with self.subTest(argv=argv), patch.object(agent, "find_active") as active, \
                        self.assertRaisesRegex(SystemExit, "session name must match"):
                    agent.main(["launch", *argv])
                active.assert_not_called()

    def test_connect_and_attach_share_parser_and_options(self):
        parser = cli_parser()
        commands = subcommands(parser)
        self.assertIs(commands["connect"], commands["attach"])
        self.assertIn(" connect ", commands["connect"].format_usage())
        for name in ("connect", "attach"):
            args = parser.parse_args([name, "dev1", "--dangerously-skip-permissions"])
            self.assertIs(args.func, agent.cmd_attach)
            self.assertTrue(args.yolo)
            self.assertEqual(args.name, "dev1")
            self.assertEqual(parser.parse_args([name, "dev1", "--cmd", "bash"]).cmd, "bash")

    def test_restore_and_resume_share_parser_and_connection_flags(self):
        parser = cli_parser()
        commands = subcommands(parser)
        self.assertIs(commands["restore"], commands["resume"])
        self.assertIn(" restore ", commands["restore"].format_usage())
        for name in ("restore", "resume"):
            self.assertFalse(parser.parse_args([name, "dev1"]).attach)
            for flag in ("--connect", "--attach"):
                with self.subTest(command=name, flag=flag):
                    args = parser.parse_args([name, "dev1", flag, "--yolo"])
                    self.assertIs(args.func, agent.cmd_resume)
                    self.assertTrue(args.attach)
                    self.assertTrue(args.yolo)

    def test_nested_session_commands_keep_their_meaning(self):
        parser = cli_parser()
        self.assertIs(parser.parse_args(["session", "resume", "dev1", "conversation"]).func,
                      agent.cmd_session_resume)
        self.assertIs(parser.parse_args(["session", "attach", "dev1", "worktree"]).func,
                      agent.cmd_session_attach)

    def test_connect_uses_existing_sandbox_without_provisioning(self):
        sb = object()
        for name in ("connect", "attach"):
            with self.subTest(command=name), \
                    patch.object(agent, "require_active", return_value=sb) as active, \
                    patch.object(agent, "active_harness", return_value=agent.HARNESSES["claude"]), \
                    patch.object(agent, "sync_agent_config"), \
                    patch.object(agent, "provision_session") as provision, \
                    patch.object(agent, "pty_attach", return_value=0) as terminal:
                self.assertEqual(agent.main([name, "dev1"]), 0)
                active.assert_called_once_with("dev1")
                terminal.assert_called_once()
                self.assertIs(terminal.call_args.args[0], sb)
                provision.assert_not_called()

    def test_restore_requires_stopped_sandbox_and_points_to_connect(self):
        for name in ("restore", "resume"):
            with self.subTest(command=name), \
                    patch.object(agent, "find_active", return_value=object()), \
                    patch.object(agent, "provision_session") as provision, \
                    self.assertRaisesRegex(SystemExit, "cws-agent connect dev1"):
                agent.main([name, "dev1"])
            provision.assert_not_called()

    def test_help_lists_canonical_names_and_aliases(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as result:
            agent.main(["--help"])
        self.assertEqual(result.exception.code, 0)
        self.assertIn("connect (attach)", output.getvalue())
        self.assertIn("restore (resume)", output.getvalue())
