"""Worker persistence, startup validation and restored-process lifecycle tests."""
import io
import json
import os
import shlex
import subprocess
import types
import unittest
from unittest.mock import Mock, patch

from cwsandbox import Sandbox
from test_documented_terminal import agent, completed


def result(code=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class BackendConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = Mock(spec=Sandbox)
        self.sandbox.write_file.return_value = completed()

    def test_configuration_contains_only_version_target_kind_and_count(self):
        for kind, target in (("claude", "env_example"), ("outpost", "my-outpost")):
            self.assertEqual(agent.backend_config(kind, target, 3),
                             {"version": 1, "kind": kind, "target": target, "workers": 3})

    def test_invalid_backend_configuration_is_rejected(self):
        for kind, target, count in (("unknown", "env_example", 1), ("claude", "ccpool_example", 1),
                                    ("claude", "", 1), ("outpost", "line\nbreak", 1),
                                    ("outpost", "x" * 201, 1), ("outpost", None, 1),
                                    ("claude", "env_example", 0), ("claude", "env_example", -1),
                                    ("claude", "env_example", True), ("claude", "env_example", "2")):
            with self.subTest(kind=kind, target=target, workers=count), self.assertRaises(SystemExit):
                agent.backend_config(kind, target, count)

    def test_saved_configuration_is_validated_and_extra_fields_discarded(self):
        state = agent.backend_config("claude", "env_example", 2)
        with patch.object(agent, "exec_retry", return_value=result(stdout=json.dumps(dict(state, ignored="discard-me")))):
            self.assertEqual(agent.read_backend_config(self.sandbox), state)
        for payload in ("{", "[]", "null", "{}", json.dumps(dict(state, version=2)),
                        json.dumps(dict(state, version=True)), json.dumps(dict(state, workers=0))):
            with self.subTest(payload=payload), patch.object(agent, "exec_retry", return_value=result(stdout=payload)), self.assertRaises(SystemExit):
                agent.read_backend_config(self.sandbox)
        with patch.object(agent, "exec_retry", return_value=result()):
            self.assertIsNone(agent.read_backend_config(self.sandbox))
        with patch.object(agent, "exec_retry", return_value=result(code=1)), self.assertRaisesRegex(SystemExit, "cannot read"):
            agent.read_backend_config(self.sandbox)

    def test_start_persists_canonical_config_without_credentials(self):
        for kind, target, env, function in (
            ("claude", "env_example", {"ANTHROPIC_ENVIRONMENT_KEY": "test-environment-key"}, "start_claude_workers"),
            ("outpost", "my-outpost", {"DEVIN_OUTPOSTS_TOKEN": "test-outpost-key"}, "start_outpost_workers"),
        ):
            state = agent.backend_config(kind, target, 3)
            with self.subTest(kind=kind), patch.object(agent, function) as start:
                agent.start_backend(self.sandbox, dict(state, secret="must-not-persist"), "workerbox", env)
                start.assert_called_once_with(self.sandbox, target, "workerbox", 3)
            path, contents = self.sandbox.write_file.call_args.args
            self.assertEqual(path, agent.BACKEND_STATE)
            self.assertEqual(json.loads(contents), state)
            self.assertNotIn(b"secret", contents)
            self.assertNotIn(b"test-environment-key", contents)
            self.assertNotIn(b"test-outpost-key", contents)

    def test_missing_key_prevents_writes_and_worker_start(self):
        for kind, target in (("claude", "env_example"), ("outpost", "my-outpost")):
            with self.subTest(kind=kind), patch.object(agent, "start_claude_workers") as claude, patch.object(agent, "start_outpost_workers") as devin:
                with self.assertRaisesRegex(SystemExit, "export"):
                    agent.start_backend(self.sandbox, agent.backend_config(kind, target, 1), "workerbox", {})
                self.sandbox.write_file.assert_not_called()
                claude.assert_not_called()
                devin.assert_not_called()

    def test_ant_environment_does_not_implicitly_copy_platform_api_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_ENVIRONMENT_KEY": "test-environment-key",
                                     "ANTHROPIC_ENVIRONMENT_ID": "env_example",
                                     "ANTHROPIC_API_KEY": "test-platform-api-key"}, clear=True):
            env = agent.build_env(agent.HARNESSES["ant"], [], [])
        self.assertEqual(env, {"ANTHROPIC_ENVIRONMENT_KEY": "test-environment-key",
                               "ANTHROPIC_ENVIRONMENT_ID": "env_example"})


class WorkerStartupTests(unittest.TestCase):
    def test_startup_checks_detect_failed_directory_launch_and_child_exit(self):
        sandbox = Mock(spec=Sandbox)
        for responses, message in (([result(1)], "directory"),
                                   ([result(), result(1)], "could not start"),
                                   ([result(), result(), result(1)], "startup check")):
            with self.subTest(message=message), patch.object(agent, "exec_retry", side_effect=responses), self.assertRaisesRegex(SystemExit, message):
                agent.start_checked_worker(sandbox, "claude-0", "/workspace/claude/0", "ant beta:worker poll")

    def test_successful_start_is_not_retried_and_logs_are_checked(self):
        with patch.object(agent, "exec_retry", side_effect=[result(), result(), result()]) as execute:
            agent.start_checked_worker(Mock(spec=Sandbox), "claude-0", "/workspace/claude/0", "ant beta:worker poll")
        self.assertEqual(execute.call_args_list[1].kwargs["attempts"], 1)
        script = execute.call_args_list[2].args[1][2]
        self.assertIn("tmux has-session", script)
        self.assertIn("worker.log", script)
        self.assertIn("401", script)

    def test_worker_auth_check_ignores_status_digits_in_healthy_timestamps_and_ids(self):
        with patch.object(agent, "exec_retry", return_value=result()) as execute:
            agent.start_checked_worker(Mock(spec=Sandbox), "claude-0", "/workspace/claude/0", "ant beta:worker poll")
        words = shlex.split(execute.call_args.args[1][2])
        pattern = words[words.index("-Eqi") + 1]
        for line, rejected in (
            ("2026-09-06T17:22:10.401Z INFO polling environment env_ok", False),
            ("INFO worker ready for environment env_abc403def", False),
            ("HTTP/1.1 401 Unauthorized", True),
            ("API error (403): permission denied", True),
            ("request failed with status: 401", True),
            ("invalid API key", True),
            ("UnrestrictedPaths is no longer supported", True),
        ):
            with self.subTest(line=line):
                matched = subprocess.run(["grep", "-Eqi", pattern], input=line, text=True).returncode == 0
                self.assertEqual(matched, rejected)

    def test_claude_worker_command_uses_environment_scope_without_obsolete_flag(self):
        sandbox = Mock(spec=Sandbox)
        with patch.object(agent, "start_checked_worker") as start, patch.object(agent.sys, "stdout", io.StringIO()):
            agent.start_claude_workers(sandbox, "env_example", "workerbox", 2)
        self.assertEqual(start.call_count, 2)
        for index, call in enumerate(start.call_args_list):
            self.assertEqual(call.args[:3], (sandbox, f"claude-{index}", f"/workspace/claude/{index}"))
            self.assertIn("--environment-id env_example", call.args[3])
            self.assertIn(f"--worker-id workerbox-{index}", call.args[3])
            self.assertNotIn("--unrestricted-paths", call.args[3])
            self.assertNotIn("ANTHROPIC_ENVIRONMENT_KEY", call.args[3])


class BackendRestoreTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = Mock(spec=Sandbox)
        self.sandbox.sandbox_id = "sandbox-test"
        self.sandbox.write_file.return_value = completed()
        self.sandbox.stop.return_value = completed()
        self.args = types.SimpleNamespace(name="workerbox", claude_env=None, outpost=None,
                                          workers=None, agent=None, image=None, env=[], env_passthrough=[],
                                          lifetime="8h", cpu="2", memory="4Gi", disk="10Gi", mode=None,
                                          attach=False)

    def resume(self, harness, state, env, *, start_error=None):
        snapshot = types.SimpleNamespace(request_id=f"cwsa1|workerbox|{harness}|1", size_bytes=1024,
                                         file_system_snapshot_id="snapshot-test")
        with patch.object(agent, "find_active", return_value=None), patch.object(agent, "latest_ready_snapshot", return_value=snapshot), patch.object(agent, "build_env", return_value=env), patch.object(agent, "provision_session", return_value=self.sandbox) as provision, patch.object(agent, "read_backend_config", return_value=state), patch.object(agent, "start_claude_workers", side_effect=start_error) as claude, patch.object(agent, "start_outpost_workers", side_effect=start_error) as devin, patch.object(agent.sys, "stdout", io.StringIO()):
            code = agent.cmd_resume(self.args)
        return code, provision, claude, devin

    def test_resume_restarts_saved_worker_target_and_count(self):
        state = agent.backend_config("claude", "env_saved", 3)
        code, provision, claude, devin = self.resume("ant", state, {"ANTHROPIC_ENVIRONMENT_KEY": "test-key"})
        self.assertEqual(code, 0)
        self.assertEqual(provision.call_args.kwargs["restore_snapshot_id"], "snapshot-test")
        claude.assert_called_once_with(self.sandbox, "env_saved", "workerbox", 3)
        devin.assert_not_called()
        self.sandbox.stop.assert_not_called()

    def test_outpost_resume_accepts_legacy_singular_token_name(self):
        state = agent.backend_config("outpost", "saved-outpost", 2)
        code, provision, claude, devin = self.resume("devin", state, {"DEVIN_OUTPOST_TOKEN": "test-key"})
        self.assertEqual(code, 0)
        devin.assert_called_once_with(self.sandbox, "saved-outpost", "workerbox", 2)
        self.assertEqual(provision.call_args.kwargs["env"]["DEVIN_OUTPOSTS_TOKEN"], "test-key")
        claude.assert_not_called()

    def test_explicit_worker_override_is_used(self):
        self.args.workers = 4
        state = agent.backend_config("claude", "env_saved", 2)
        _, _, claude, _ = self.resume("ant", state, {"ANTHROPIC_ENVIRONMENT_KEY": "test-key"})
        claude.assert_called_once_with(self.sandbox, "env_saved", "workerbox", 4)

    def test_legacy_ant_snapshot_requires_explicit_environment(self):
        with self.assertRaisesRegex(SystemExit, "legacy snapshot"):
            self.resume("ant", None, {"ANTHROPIC_ENVIRONMENT_KEY": "test-key"})
        self.sandbox.stop.assert_called_once_with(missing_ok=True)
        self.args.claude_env = "env_explicit"
        self.args.workers = 2
        self.sandbox.reset_mock()
        _, _, claude, _ = self.resume("ant", None, {"ANTHROPIC_ENVIRONMENT_KEY": "test-key"})
        claude.assert_called_once_with(self.sandbox, "env_explicit", "workerbox", 2)
        self.sandbox.stop.assert_not_called()

    def test_missing_auth_state_mismatch_and_worker_failure_cleanup(self):
        for harness, state, env, failure in (
            ("devin", agent.backend_config("outpost", "saved", 1), {}, None),
            ("devin", agent.backend_config("claude", "env_wrong", 1), {"DEVIN_OUTPOSTS_TOKEN": "test-key"}, None),
            ("ant", agent.backend_config("claude", "env_saved", 1), {"ANTHROPIC_ENVIRONMENT_KEY": "test-key"}, RuntimeError("worker died")),
            ("ant", agent.backend_config("claude", "env_saved", 1), {"ANTHROPIC_ENVIRONMENT_KEY": "test-key"}, KeyboardInterrupt()),
        ):
            with self.subTest(harness=harness, failure=type(failure).__name__):
                self.sandbox.reset_mock()
                with self.assertRaises((SystemExit, RuntimeError, KeyboardInterrupt)):
                    self.resume(harness, state, env, start_error=failure)
                self.sandbox.stop.assert_called_once_with(missing_ok=True)

    def test_missing_ant_key_fails_before_provisioning(self):
        with patch.object(agent, "find_active", return_value=None), patch.object(agent, "latest_ready_snapshot", return_value=types.SimpleNamespace(request_id="cwsa1|workerbox|ant|1")), patch.object(agent, "build_env", return_value={}), patch.object(agent, "provision_session") as provision:
            with self.assertRaisesRegex(SystemExit, "ANTHROPIC_ENVIRONMENT_KEY"):
                agent.cmd_resume(self.args)
            provision.assert_not_called()

    def test_bootstrap_failure_and_interrupt_release_new_sandbox(self):
        for failure in (SystemExit("bootstrap error"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), patch.object(agent, "create_session_sandbox", return_value=self.sandbox), patch.object(agent, "run_bootstrap", side_effect=failure), patch.object(agent.sys, "stdout", io.StringIO()):
                self.sandbox.reset_mock()
                with self.assertRaises(type(failure)):
                    agent.provision_session(name="workerbox", harness=agent.HARNESSES["ant"], repo_url=None)
                self.sandbox.stop.assert_called_once_with(missing_ok=True)
