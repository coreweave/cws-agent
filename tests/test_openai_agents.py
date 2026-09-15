"""Agents API routing, credential boundaries, lifecycle and streamed turns."""
import contextlib
import io
import json
import os
import types
import unittest
from unittest.mock import MagicMock, Mock, patch

from openai.types.beta.environment import EnvironmentResourceSelfHosted
from test_documented_terminal import agent, completed


def session(status="idle"):
    return types.SimpleNamespace(
        id="asess_test", status=status,
        environment=EnvironmentResourceSelfHosted(
            id="env_test", type="self_hosted", capability_directories=[],
            workspace_directory=agent.PROJECT_DIR,
            remote_url="wss://api.openai.com/v1/environment?opaque=keep%2Fexact",
        ),
    )


def arguments(command, *extra):
    function = {"launch": "cmd_launch", "restore": "cmd_resume", "run": "cmd_run"}[command]
    with patch.object(agent, function, side_effect=lambda args: args):
        return agent.main([command, *extra])


class OpenAIAgentsTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.__enter__.return_value = self.client
        self.client.beta.agents.sessions.create.return_value = session()
        self.client.beta.agents.sessions.retrieve.return_value = session()
        self.client.beta.agents.environments.retrieve.return_value = types.SimpleNamespace(status="connected")
        self.sb = Mock(sandbox_id="sandbox-test")
        self.sb.write_file.return_value = completed()
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def launch_args(self, *extra):
        return arguments("launch", "--name", "api-test", "--agent", "openai", *extra)

    def test_only_executor_key_is_copied_and_platform_key_is_rejected(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "platform-secret",
                                     "OPENAI_EXECUTOR_API_KEY": "executor-secret"}, clear=True):
            self.assertEqual(agent.build_env(agent.HARNESSES["openai"], [], []),
                             {"CODEX_API_KEY": "executor-secret"})
            for extra, passthrough in ((["OPENAI_API_KEY=secret"], []), ([], ["OPENAI_API_KEY"])):
                with self.assertRaisesRegex(SystemExit, "keep OPENAI_API_KEY"):
                    agent.build_env(agent.HARNESSES["openai"], extra, passthrough)

    def test_no_api_key_and_no_executor_key_fail_before_provisioning(self):
        for env, missing in (({}, "OPENAI_EXECUTOR_API_KEY"),
                             ({"OPENAI_EXECUTOR_API_KEY": "secret"}, "OPENAI_API_KEY")):
            with self.subTest(missing=missing), patch.dict(os.environ, env, clear=True), \
                    patch.object(agent, "find_active", return_value=None), \
                    patch.object(agent, "provision_session") as provision:
                with self.assertRaisesRegex(SystemExit, missing):
                    agent.cmd_launch(self.launch_args())
                provision.assert_not_called()

    def test_invalid_worker_options_fail_before_api_or_compute(self):
        for flags in (("--outpost", "other"), ("--claude-env", "env_other"),
                      ("--workers", "2"), ("--yolo",), ("--telegram",),
                      ("--openai-session", "asess_existing", "--openai-model", "model")):
            with self.subTest(flags=flags), patch.object(agent, "openai_client") as client, \
                    patch.object(agent, "find_active") as lookup:
                with self.assertRaises(SystemExit):
                    agent.cmd_launch(self.launch_args(*flags))
                client.assert_not_called()
                lookup.assert_not_called()

    def test_launch_creates_api_session_and_starts_backend_without_platform_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "platform", "OPENAI_EXECUTOR_API_KEY": "executor"}), \
                patch.object(agent, "openai_client", return_value=self.client), \
                patch.object(agent, "find_active", return_value=None), \
                patch.object(agent, "provision_session", return_value=self.sb) as provision, \
                patch.object(agent, "start_backend") as start:
            self.assertEqual(agent.cmd_launch(self.launch_args("--openai-model", "my-model")), 0)
        create = self.client.beta.agents.sessions.create.call_args.kwargs
        self.assertEqual(create["agent"]["model"], "my-model")
        self.assertEqual(create["environment"], {"type": "self_hosted", "workspace_directory": agent.PROJECT_DIR})
        self.assertEqual(provision.call_args.kwargs["env"], {"CODEX_API_KEY": "executor"})
        self.assertEqual(start.call_args.args[1], agent.backend_config("openai", "asess_test", 1))
        self.client.beta.agents.sessions.delete.assert_not_called()
        saved = json.loads(self.sb.write_file.call_args.args[1])
        self.assertEqual(saved["target"], "asess_test")
        self.assertNotIn("remote_url", saved)

    def test_launch_failure_deletes_only_new_api_session(self):
        for existing in (False, True):
            self.client.reset_mock()
            args = self.launch_args(*(["--openai-session", "asess_existing"] if existing else []))
            with patch.dict(os.environ, {"OPENAI_EXECUTOR_API_KEY": "executor"}), \
                    patch.object(agent, "openai_client", return_value=self.client), \
                    patch.object(agent, "find_active", return_value=None), \
                    patch.object(agent, "provision_session", side_effect=RuntimeError("provision failed")):
                with self.assertRaisesRegex(RuntimeError, "provision failed"):
                    agent.cmd_launch(args)
            if existing:
                self.client.beta.agents.sessions.delete.assert_not_called()
            else:
                self.client.beta.agents.sessions.delete.assert_called_once_with("asess_test")

    def test_environment_validation_and_exact_url(self):
        s = session()
        self.assertIs(agent.openai_environment(s), s.environment)
        for field, value in (("type", "openai_hosted"), ("workspace_directory", "/other"),
                             ("remote_url", "wss://attacker.invalid/path"),
                             ("remote_url", "http://api.openai.com/path"),
                             ("remote_url", "wss://secret@api.openai.com/path")):
            s = session()
            setattr(s.environment, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(SystemExit):
                agent.openai_environment(s)

    def test_executor_requires_actual_api_connection_and_preserves_url(self):
        self.client.beta.agents.environments.retrieve.side_effect = [
            types.SimpleNamespace(status=s) for s in ("disconnected", "pending", "connected")]
        with patch.object(agent, "openai_client", return_value=self.client), \
                patch.object(agent, "start_checked_worker") as worker, patch.object(agent.time, "sleep"):
            agent.start_openai_executor(self.sb, "asess_test")
        command = worker.call_args.args[3]
        import shlex
        self.assertEqual(shlex.split(command), ["codex", "exec-server", "--remote",
            session().environment.remote_url, "--environment-id", "env_test"])
        self.assertEqual(worker.call_args.args[2], agent.PROJECT_DIR)

    def test_connected_executor_is_not_replaced(self):
        with patch.object(agent, "openai_client", return_value=self.client), \
                patch.object(agent, "start_checked_worker") as worker:
            with self.assertRaisesRegex(SystemExit, "already has"):
                agent.start_openai_executor(self.sb, "asess_test")
            worker.assert_not_called()

    def test_executor_connection_failure_is_not_success(self):
        self.client.beta.agents.environments.retrieve.side_effect = [
            types.SimpleNamespace(status=s) for s in ("disconnected", "failed")]
        with patch.object(agent, "openai_client", return_value=self.client), \
                patch.object(agent, "start_checked_worker"):
            with self.assertRaisesRegex(SystemExit, "did not connect"):
                agent.start_openai_executor(self.sb, "asess_test")

    def test_restore_uses_saved_api_session_and_executor_key(self):
        snap = types.SimpleNamespace(size_bytes=0, request_id="api-test|agent=openai",
                                     file_system_snapshot_id="snapshot-test")
        state = agent.backend_config("openai", "asess_test", 1)
        with patch.dict(os.environ, {"OPENAI_EXECUTOR_API_KEY": "executor"}), \
                patch.object(agent, "openai_client", return_value=self.client), \
                patch.object(agent, "find_active", return_value=None), \
                patch.object(agent, "latest_ready_snapshot", return_value=snap), \
                patch.object(agent, "harness_from_request_id", return_value="openai"), \
                patch.object(agent, "provision_session", return_value=self.sb) as provision, \
                patch.object(agent, "read_backend_config", return_value=state), \
                patch.object(agent, "start_backend") as start:
            self.assertEqual(agent.cmd_resume(arguments("restore", "api-test")), 0)
        self.assertEqual(provision.call_args.kwargs["restore_snapshot_id"], "snapshot-test")
        self.assertEqual(start.call_args.args[1], state)
        self.assertEqual(start.call_args.args[3], {"CODEX_API_KEY": "executor"})
        self.client.beta.agents.sessions.create.assert_not_called()

    def run_events(self, events):
        stream = self.client.beta.agents.sessions.events.stream.return_value
        stream.__enter__.return_value = iter(events)
        with patch.object(agent, "openai_client", return_value=self.client), \
                patch.object(agent, "read_backend_config", return_value=agent.backend_config("openai", "asess_test", 1)), \
                patch.object(agent, "workspace_access", return_value=contextlib.nullcontext()):
            return agent.run_openai_prompt(self.sb, arguments("run", "api-test", "write a file"))

    def test_stream_waits_for_root_completion_and_submits_once_after_open(self):
        events = [types.SimpleNamespace(type="agent.session.idle"),
                  types.SimpleNamespace(type="agent.session.turn.completed", turn=types.SimpleNamespace(subagent_id="sub")),
                  types.SimpleNamespace(type="agent.session.turn.output_text.delta", delta="done"),
                  types.SimpleNamespace(type="agent.session.turn.completed", turn=types.SimpleNamespace(subagent_id=None))]
        self.assertEqual(self.run_events(events), 0)
        api = self.client.beta.agents.sessions.events
        api.create.assert_called_once()
        names = [str(c).split("(")[0] for c in api.mock_calls]
        self.assertLess(names.index("call.stream"), names.index("call.create"))

    def test_ended_stream_and_root_failure_are_not_success_and_do_not_resubmit(self):
        for events in ([], [types.SimpleNamespace(type="agent.session.turn.failed", turn=types.SimpleNamespace(subagent_id=None))]):
            self.client.reset_mock()
            with self.subTest(events=events), self.assertRaises(SystemExit):
                self.run_events(events)
            self.client.beta.agents.sessions.events.create.assert_called_once()

    def test_busy_session_or_disconnected_executor_prevents_input(self):
        for busy in (True, False):
            self.client.reset_mock()
            self.client.beta.agents.sessions.retrieve.return_value = session("in_progress" if busy else "idle")
            self.client.beta.agents.environments.retrieve.return_value = types.SimpleNamespace(status="disconnected")
            with self.assertRaises(SystemExit):
                self.run_events([])
            self.client.beta.agents.sessions.events.create.assert_not_called()

    def test_api_client_does_not_automatically_retry_mutations(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "platform"}), patch("openai.OpenAI") as client:
            agent.openai_client()
        self.assertEqual(client.call_args.kwargs["max_retries"], 0)


if __name__ == "__main__":
    unittest.main()
