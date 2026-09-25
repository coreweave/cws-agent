"""Canonical lifecycle verbs preserve the existing command contracts."""
import contextlib
import io
import unittest
from unittest.mock import patch

from test_consolidation import cli_parser, subcommands
from test_terminal import agent


SHORTCUTS = {
    "anthropic": "ant", "claude": "claude", "codex": "codex", "cursor": "cursor",
    "devin": "devin", "openai": "openai", "opencode": "opencode",
}


class LifecycleNameTests(unittest.TestCase):
    def test_omitted_names_use_selected_harness_for_shortcuts_and_launch(self):
        cases = []
        for command, harness in SHORTCUTS.items():
            flags = ["--claude-env", "env_example"] if harness == "ant" else []
            cases.extend((([command, *flags], command),
                          (["launch", "--agent", harness, *flags], command)))
        cases.extend(((["launch"], "claude"),
                      (["launch", "--claude-env", "env_example"], "anthropic"),
                      (["codex", "--claude-env", "env_example"], "anthropic"),
                      (["launch", "--outpost", "example-outpost"], "devin")))
        for argv, prefix in cases:
            with self.subTest(argv=argv), \
                    patch.object(agent, "find_active", return_value=object()) as active, \
                    patch.object(agent, "provision_session") as provision, \
                    self.assertRaisesRegex(SystemExit, "already has an active sandbox"):
                agent.main(argv)
            name = active.call_args.args[0]
            self.assertRegex(name, rf"^{prefix}-[a-f0-9]{{8}}$")
            self.assertIsNotNone(agent.NAME_RE.fullmatch(name))
            provision.assert_not_called()

    def test_generated_names_reach_provision_upload_and_reconnect_output(self):
        names = set()
        for command in ("claude", "claude", "codex", "cursor", "devin", "opencode"):
            output = io.StringIO()
            sandbox = object()
            inventory = agent.LocalDirectoryInventory("/project", [], 0, 0, set())
            with self.subTest(command=command), contextlib.redirect_stdout(output), \
                    patch.object(agent, "find_active", return_value=None), \
                    patch.object(agent, "build_env", return_value={}), \
                    patch.object(agent, "scan_local_dir", return_value=inventory), \
                    patch.object(agent, "provision_session", return_value=sandbox) as provision, \
                    patch.object(agent, "sync_local_dir") as upload, \
                    patch.object(agent, "automatic_snapshot") as snapshot:
                self.assertEqual(agent.main([command, "--local-dir", "/project", "--detach"]), 0)
            name = provision.call_args.kwargs["name"]
            self.assertRegex(name, rf"^{command}-[a-f0-9]{{8}}$")
            self.assertNotIn(name, names)
            names.add(name)
            self.assertEqual(upload.call_args.kwargs["session_name"], name)
            snapshot.assert_called_once_with(sandbox, name, command)
            self.assertIn(f"launching session '{name}'", output.getvalue())
            self.assertIn(f"cws-agent connect {name}", output.getvalue())

    def test_agent_shortcuts_match_explicit_launch(self):
        cases = (["dev1"], ["--name", "dev1"],
                 ["--detach", "dev1", "--local-dir", ".", "--exclude", ".env",
                  "--exclude", "data", "--cpu", "4", "--memory", "8Gi",
                  "--env", "EXAMPLE=one", "--env", "SECOND=two",
                  "--no-config-sync", "-v"])
        specific_options = {
            "claude": ["--telegram", "--permission-mode", "native"],
            "codex": ["--import-codex-auth"],
            "devin": ["--outpost", "example-outpost", "--workers", "2"],
            "opencode": ["--wandb", "--wandb-model", "example-model"],
            "cursor": ["--permission-mode", "native"],
            "ant": ["--claude-env", "env_example", "--workers", "2"],
            "openai": ["--openai-session", "asess_example"],
        }
        for command, harness in SHORTCUTS.items():
            for options in (*cases, ["dev1", *specific_options[harness]]):
                with self.subTest(harness=harness, options=options), \
                        patch.object(agent, "cmd_launch", return_value=17) as launch:
                    self.assertEqual(agent.main(["launch", "--agent", harness, *options]), 17)
                    expected = vars(launch.call_args.args[0]).copy()
                    self.assertEqual(agent.main([command, *options]), 17)
                    actual = vars(launch.call_args.args[0]).copy()
                    self.assertEqual(actual.pop("command"), command)
                    expected.pop("command")
                    self.assertEqual(actual, expected)

    def test_agent_shortcuts_reject_agent_flag_before_dispatch(self):
        for command, harness in SHORTCUTS.items():
            for options in (["dev1", "--agent", "claude"], ["dev1", "--agent=claude"],
                            ["--agent", "claude", "dev1"], ["dev1", "--agent", harness],
                            ["dev1", "--agent"]):
                output = io.StringIO()
                with self.subTest(command=command, options=options), \
                        patch.object(agent, "cmd_launch") as launch, \
                        contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as error:
                    agent.main([command, *options])
                self.assertEqual(error.exception.code, 2)
                self.assertIn(f"--agent is not supported with 'cws-agent {command}'", output.getvalue())
                self.assertIn("the command already selects the agent", output.getvalue())
                self.assertIn("Remove --agent or use 'cws-agent launch NAME --agent AGENT'", output.getvalue())
                launch.assert_not_called()

    def test_agent_shortcuts_do_not_change_launch_or_restore_defaults(self):
        parser = cli_parser()
        for command in ("launch", "restore"):
            self.assertEqual(parser.parse_args([command, "dev1"]).agent, "claude")
            for harness in agent.HARNESSES:
                with self.subTest(command=command, harness=harness):
                    self.assertEqual(parser.parse_args([command, "dev1", "--agent", harness]).agent,
                                     harness)

    def test_agent_shortcuts_reject_duplicate_and_invalid_names(self):
        for command, harness in SHORTCUTS.items():
            for argv in (["dev1", "dev2"], ["dev1", "--name", "dev2"]):
                with self.subTest(harness=harness, argv=argv), \
                        patch.object(agent, "cmd_launch") as launch, \
                        contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    agent.main([command, *argv])
                self.assertEqual(error.exception.code, 2)
                launch.assert_not_called()
            for name in ("Invalid", "-bad", "a" * 41, ""):
                with self.subTest(harness=harness, name=name), \
                        patch.object(agent, "find_active") as active, \
                        self.assertRaisesRegex(SystemExit, "session name must match"):
                    agent.main([command, "--", name])
                active.assert_not_called()

    def test_agent_shortcut_help_lists_launch_options(self):
        for command, harness in SHORTCUTS.items():
            output = io.StringIO()
            with self.subTest(harness=harness), contextlib.redirect_stdout(output), \
                    self.assertRaises(SystemExit) as result:
                agent.main([command, "--help"])
            self.assertEqual(result.exception.code, 0)
            self.assertIn(f"usage: cws-agent {command} ", output.getvalue())
            for flag in ("--local-dir", "--detach", "--telegram", "--import-codex-auth"):
                self.assertIn(flag, output.getvalue())
        help_text = " ".join(cli_parser().format_help().split())
        for harness in SHORTCUTS.values():
            self.assertIn(f"shortcut for launch --agent {harness}", help_text)

    def test_worker_shortcuts_preserve_validation_before_remote_access(self):
        cases = (
            (["anthropic", "dev1"], "requires --claude-env"),
            (["anthropic", "dev1", "--claude-env", "env_example", "--telegram"],
             "cannot be combined with --detach or worker backends"),
            (["openai", "dev1", "--workers", "2"], "exactly one executor"),
            (["openai", "dev1", "--openai-session", "asess_example", "--openai-model", "example"],
             "applies only when creating a new API session"),
        )
        for argv, message in cases:
            with self.subTest(argv=argv), patch.object(agent, "find_active") as active, \
                    self.assertRaisesRegex(SystemExit, message):
                agent.main(argv)
            active.assert_not_called()

    def test_launch_accepts_positional_name_and_compatibility_flag(self):
        for argv in (["dev1"], ["--name", "dev1"],
                     ["dev1", "--agent", "codex"], ["--agent", "codex", "dev1"]):
            with self.subTest(argv=argv), patch.object(agent, "cmd_launch", return_value=0) as launch:
                self.assertEqual(agent.main(["launch", *argv]), 0)
                self.assertEqual(launch.call_args.args[0].name, "dev1")

    def test_launch_rejects_duplicate_names(self):
        for argv in (["dev1", "dev2"],
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
