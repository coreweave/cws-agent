"""Sandbox TTL parsing, limits, and routing without cloud access."""
import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_consolidation import cli_parser
from test_terminal import agent


def creation_commands():
    commands = [["shell", "box"], ["restore", "box"], ["resume", "box"],
                ["cloud", "start", "box", "--environment", "ccpool_example"]]
    for harness in agent.HARNESSES:
        extra = ["--claude-env", "env_example"] if harness == "ant" else []
        commands.append(["launch", "box", "--agent", harness, *extra])
        commands.append(["anthropic" if harness == "ant" else harness, "box", *extra])
    return commands


class TTLTests(unittest.TestCase):
    def setUp(self):
        self.parser = cli_parser()
        self.errors = self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_durations_and_defaults_on_every_creation_path(self):
        for command in creation_commands():
            with self.subTest(command=command):
                default = self.parser.parse_args(command).ttl
                self.assertEqual(default, None if command[0] == "shell" else 28800)
                values = (("600", 600), ("10m", 600)) if command[0] == "cloud" else (("1", 1), ("1s", 1), ("300s", 300), ("5m", 300))
                for text, seconds in (*values, ("8h", 28800),
                                      ("30d", 2592000), ("720h", 2592000),
                                      ("43200m", 2592000), ("2592000", 2592000)):
                    for flags in (["--ttl", text], ["--ttl=" + text]):
                        self.assertEqual(self.parser.parse_args([*command, *flags]).ttl, seconds)

    def test_invalid_ttl_and_removed_flag_fail_before_remote_access(self):
        with patch.object(agent, "find_active") as find, \
             patch.object(agent, "build_env") as env, \
             patch.object(agent, "latest_ready_snapshot") as snapshot, \
             patch.object(agent, "openai_client") as api, \
             patch.object(agent, "provision_session") as provision, \
             patch.object(agent, "Sandbox") as sdk:
            for command in creation_commands():
                for value in ("0", "0m", "-1", "31d", "2592001", "2592001s", "720h1s",
                              "1.5h", "1h30m", "8H", "infinite", "2026-10-01", "P30D", "", "9" * 5000):
                    with self.subTest(command=command, value=value[:30]), self.assertRaises(SystemExit) as raised:
                        agent.main([*command, "--ttl=" + value])
                    self.assertEqual(raised.exception.code, 2)
                for flags in (["--lifetime", "8h"], ["--ttl"]):
                    with self.subTest(command=command, flags=flags), self.assertRaises(SystemExit) as raised:
                        agent.main([*command, *flags])
                    self.assertEqual(raised.exception.code, 2)
            for mock in (find, env, snapshot, api, provision, sdk):
                self.assertEqual(mock.mock_calls, [])

    def test_bounds_errors_identify_flag_and_allowed_range(self):
        with patch.object(agent, "find_active") as find, \
             patch.object(agent, "build_env") as env, \
             patch.object(agent, "provision_session") as provision, \
             patch.object(agent, "Sandbox") as sdk:
            for command in creation_commands():
                minimum = "10m (600 seconds)" if command[0] == "cloud" else "1s"
                invalid = ("0", "-1", "2592001s", "31d")
                if command[0] == "cloud":
                    invalid += ("1s", "5m", "599s")
                for value in invalid:
                    self.errors.seek(0)
                    self.errors.truncate(0)
                    with self.subTest(command=command, value=value), self.assertRaises(SystemExit) as raised:
                        agent.main([*command, "--ttl=" + value])
                    self.assertEqual(raised.exception.code, 2)
                    self.assertIn(f"argument --ttl: must be between {minimum} and 30d (2592000 seconds)",
                                  self.errors.getvalue())
            for mock in (find, env, provision, sdk):
                self.assertEqual(mock.mock_calls, [])

    def test_timeout_remains_a_command_limit_not_a_ttl_alias(self):
        for command in creation_commands():
            with self.subTest(command=command), self.assertRaises(SystemExit):
                self.parser.parse_args([*command, "--timeout", "300"])
        args = self.parser.parse_args(["run", "box", "task", "--timeout", "300"])
        self.assertEqual(args.timeout, 300)
        self.assertFalse(hasattr(args, "ttl"))

    def test_existing_compute_paths_do_not_accept_ttl(self):
        commands = [["connect", "box"], ["attach", "box"], ["exec", "box", "true"],
                    ["run", "box", "task"], ["cloud", "run", "box", "task"],
                    ["session", "start", "box", "worker"], ["session", "restart", "box", "worker"]]
        with patch.object(agent, "Sandbox") as sdk, patch.object(agent, "require_active") as active:
            for command in commands:
                with self.subTest(command=command), self.assertRaises(SystemExit):
                    agent.main([*command, "--ttl", "8h"])
            sdk.assert_not_called()
            active.assert_not_called()

    def test_launch_and_agent_shortcuts_forward_ttl_to_provisioning(self):
        # Stop at the shared provisioning boundary; no bootstrap or agent login.
        stop = RuntimeError("provisioning boundary")
        with patch.object(agent, "find_active", return_value=None), \
             patch.object(agent, "build_env", return_value={"ANTHROPIC_ENVIRONMENT_KEY": "fixture",
                                                          "CODEX_API_KEY": "fixture"}), \
             patch.object(agent, "local_codex_auth", return_value=None), \
             patch.object(agent, "wandb_opencode_config", return_value=None), \
             patch.object(agent, "openai_client") as api, \
             patch.object(agent, "openai_environment"), \
             patch.object(agent, "provision_session", side_effect=stop) as provision:
            api.return_value.__enter__.return_value.beta.agents.sessions.create.return_value = SimpleNamespace(id="session_example")
            for command in creation_commands()[4:]:
                with self.subTest(command=command), self.assertRaisesRegex(RuntimeError, "provisioning boundary"):
                    agent.main([*command, "--ttl", "30d", "--detach"])
                self.assertEqual(provision.call_args.kwargs["lifetime_seconds"], 2592000)
                provision.reset_mock()

    def test_restore_and_resume_use_new_ttl(self):
        snapshot = SimpleNamespace(request_id="cwsa1|box|claude|1", size_bytes=42,
                                   file_system_snapshot_id="fss-example")
        with patch.object(agent, "find_active", return_value=None), \
             patch.object(agent, "latest_ready_snapshot", return_value=snapshot), \
             patch.object(agent, "build_env", return_value={}), \
             patch.object(agent, "local_codex_auth", return_value=None), \
             patch.object(agent, "wandb_opencode_config", return_value=None), \
             patch.object(agent, "provision_session", side_effect=RuntimeError("provisioning boundary")) as provision:
            for command in ("restore", "resume"):
                for flags, seconds in (([], 28800), (["--ttl", "5m"], 300)):
                    with self.subTest(command=command, flags=flags), self.assertRaisesRegex(RuntimeError, "provisioning boundary"):
                        agent.main([command, "box", *flags])
                    self.assertEqual(provision.call_args.kwargs["lifetime_seconds"], seconds)
                    self.assertEqual(provision.call_args.kwargs["restore_snapshot_id"], "fss-example")

    def test_shared_provisioning_keeps_sdk_and_metadata_in_agreement(self):
        for auth, mode in ((agent.AuthStrategy.WANDB, "serverless"),
                           (agent.AuthStrategy.COREWEAVE_API_KEY, "serverless"),
                           (agent.AuthStrategy.COREWEAVE_API_KEY, "cks")):
            with self.subTest(auth=auth, mode=mode), \
                 patch.object(agent, "sandbox_auth", return_value=auth), \
                 patch.object(agent.Sandbox, "run", return_value=Mock(sandbox_id="sb-example")) as run, \
                 patch.object(agent, "snapshot_metadata"), patch.object(agent, "run_bootstrap"), \
                 patch.object(agent, "save_workspace_metadata") as metadata:
                agent.provision_session(name="box", harness=agent.HARNESSES["claude"],
                    repo_url=None, image="python:3.11", lifetime_seconds=2592000,
                    cpu="2", memory="4Gi", disk="10Gi", env={}, mode=mode,
                    restore_snapshot_id="fss-example")
                self.assertEqual(run.call_args.kwargs["max_lifetime_seconds"], 2592000)
                self.assertEqual(run.call_args.kwargs["auth"], auth)
                self.assertEqual(metadata.call_args.args[3]["lifetime_seconds"], 2592000)

    def test_transfer_timeout_does_not_inherit_ttl_limit(self):
        args = self.parser.parse_args(["launch", "box", "--transfer-timeout", "31d"])
        self.assertEqual(args.transfer_timeout, 31 * 86400)


if __name__ == "__main__":
    unittest.main()
