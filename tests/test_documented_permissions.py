"""Permission bypass is explicitly requested and works in root containers."""
import io
import shlex
import types
import unittest
from unittest.mock import Mock, patch

from test_documented_terminal import agent


class SandboxPermissionTests(unittest.TestCase):
    def verify_command(self, function):
        for harness_name in ("claude", "codex", "devin"):
            for yolo in (False, True):
                with self.subTest(command=function, harness=harness_name, yolo=yolo):
                    harness = agent.HARNESSES[harness_name]
                    args = types.SimpleNamespace(name="dev1", session="worker", prompt="test prompt",
                                                 base="main", branch=None, yolo=yolo, attach=False,
                                                 timeout=30, agent=None, session_id=None,
                                                 permission_mode="accept-edits")
                    result = types.SimpleNamespace(returncode=0, stdout="", stderr="")
                    with patch.object(agent, "require_active", return_value=Mock()), patch.object(agent, "active_harness", return_value=harness), patch.object(agent, "read_session_sessions", return_value=[{"name": "worker", "alive": False}]), patch.object(agent, "exec_retry", return_value=result) as execute, patch.object(agent.sys, "stdout", io.StringIO()):
                        self.assertEqual(getattr(agent, function)(args), 0)
                    command = execute.call_args.args[1][-1]
                    # This variable declares the actual remote sandbox globally;
                    # it does not itself bypass permissions in any harness.
                    self.assertIn("IS_SANDBOX=1", command)
                    self.assertEqual(harness.yolo_flag.strip() in command, yolo)
                    tokens = shlex.split(command)
                    default = {"claude": ["--permission-mode", "acceptEdits"],
                               "devin": ["--permission-mode", "accept-edits"],
                               "codex": ["--sandbox", "workspace-write"]}[harness_name]
                    pairs = [tokens[index:index + 2] for index in range(len(tokens) - 1)]
                    self.assertEqual(default in pairs, not yolo)
                    if harness_name == "codex" and not yolo:
                        if function == "cmd_run":
                            self.assertIn('approval_policy="on-request"', tokens)
                        else:
                            self.assertIn(["--ask-for-approval", "on-request"], pairs)

    def test_run_yolo_is_explicit_and_scoped_to_claude(self):
        self.verify_command("cmd_run")

    def test_session_start_yolo_is_explicit_and_scoped_to_claude(self):
        self.verify_command("cmd_session_start")

    def test_session_restart_yolo_is_explicit_and_scoped_to_claude(self):
        self.verify_command("cmd_session_restart")
