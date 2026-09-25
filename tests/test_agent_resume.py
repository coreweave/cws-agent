"""Resume shortcuts select saved agent sessions without allocating sandboxes."""
import contextlib
import io
import types
import unittest
from unittest.mock import patch

from test_terminal import agent


def sandbox(name, harness="claude", status="running"):
    return types.SimpleNamespace(name=name, harness=harness,
                                 status=types.SimpleNamespace(value=status))


def row(harness="claude", sid="chat-123", cwd="/workspace/project"):
    return {"agent": harness, "id": sid, "cwd": cwd}


class AgentResumeTests(unittest.TestCase):
    def setUp(self):
        self.box = sandbox("claude-example")
        self.listing = self.enterContext(patch.object(agent.Sandbox, "list"))
        self.listing.return_value.result.return_value = [self.box]
        self.enterContext(patch.object(agent, "sandbox_auth", return_value="offline"))
        self.enterContext(patch.object(agent, "probe_session_meta",
                                       side_effect=lambda sb: (sb.name, sb.harness)))
        self.active = self.enterContext(patch.object(agent, "require_active", return_value=self.box))
        self.history = self.enterContext(patch.object(agent, "remote_native_history", return_value=[row()]))
        self.attach = self.enterContext(patch.object(agent, "pty_attach", return_value=17))
        self.provision = self.enterContext(patch.object(agent, "provision_session"))
        self.restore = self.enterContext(patch.object(agent, "cmd_resume"))
        self.output = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.errors = self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def tearDown(self):
        self.provision.assert_not_called()
        self.restore.assert_not_called()

    def test_discovers_conversation_for_each_searchable_agent_and_preserves_directory(self):
        for harness, native in (("claude", "claude --resume"), ("codex", "codex resume"),
                                ("opencode", "opencode --session")):
            with self.subTest(harness=harness):
                self.history.return_value = [row(harness, cwd="/workspace/sessions/my task")]
                self.assertEqual(agent.main([harness, "--resume", "chat-123", "--permission-mode", "native"]), 17)
                self.assertEqual(self.attach.call_args.args,
                                 (self.box, f"cd '/workspace/sessions/my task' && exec {native} chat-123"))
                self.assertIn("claude-example", self.output.getvalue())
        self.listing.assert_called_with(tags=[agent.SESSION_TAG], auth="offline")
        self.active.assert_not_called()

    def test_filters_other_conversations_and_selects_matching_sandbox(self):
        other = sandbox("codex-other", "codex")
        self.listing.return_value.result.return_value = [other, self.box]
        self.history.side_effect = lambda sb: ([row("codex"), row(sid="chat-other")]
                                               if sb is other else [row()])
        agent.main(["claude", "--resume=chat-123"])
        self.assertIs(self.attach.call_args.args[0], self.box)

    def test_explicit_sandbox_uses_existing_resume_path_without_listing(self):
        for argv in (["claude", "claude-example", "--resume", "chat-123"],
                     ["claude", "--name", "claude-example", "--resume", "chat-123"]):
            with self.subTest(argv=argv):
                self.assertEqual(agent.main(argv), 17)
                self.active.assert_called_with("claude-example")
        self.listing.assert_not_called()

    def test_native_only_agents_require_explicit_sandbox(self):
        for harness, native in (("devin", "devin --resume"), ("cursor", "cursor-agent --resume")):
            with self.subTest(harness=harness), self.assertRaisesRegex(SystemExit, "requires a sandbox name"):
                agent.main([harness, "--resume", "chat-123"])
            self.assertEqual(agent.main([harness, "dev1", "--resume", "chat-123",
                                         "--cwd", "/workspace/other", "--permission-mode", "native"]), 17)
            self.assertEqual(self.attach.call_args.args[1], f"cd /workspace/other && exec {native} chat-123")
        self.history.assert_not_called()
        self.listing.assert_not_called()

    def test_duplicate_conversation_requires_explicit_sandbox(self):
        self.listing.return_value.result.return_value.append(sandbox("claude-copy"))
        with self.assertRaisesRegex(SystemExit, "multiple sandboxes.*claude-copy, claude-example"):
            agent.main(["claude", "--resume", "chat-123"])
        self.attach.assert_not_called()

    def test_missing_conversation_explains_restore_without_restoring(self):
        self.history.return_value = []
        with self.assertRaisesRegex(SystemExit, "cws-agent restore SANDBOX"):
            agent.main(["claude", "--resume", "chat-123"])
        self.attach.assert_not_called()

    def test_stopped_and_worker_sandboxes_are_not_searched(self):
        self.listing.return_value.result.return_value = [sandbox("stopped", status="terminated"),
                                                        sandbox("worker", "ant"), sandbox("api", "openai"),
                                                        sandbox("terminal", "shell")]
        with self.assertRaisesRegex(SystemExit, "agent session not found"):
            agent.main(["claude", "--resume", "chat-123"])
        self.history.assert_not_called()
        self.attach.assert_not_called()

    def test_incomplete_discovery_does_not_pick_an_unverified_match(self):
        other = sandbox("?", "?")
        self.listing.return_value.result.return_value = [self.box, other]
        with self.assertRaisesRegex(SystemExit, "could not identify"):
            agent.main(["claude", "--resume", "chat-123"])
        self.attach.assert_not_called()

    def test_history_failure_is_not_silently_treated_as_no_match(self):
        self.history.side_effect = SystemExit("error: could not read native session histories")
        with self.assertRaisesRegex(SystemExit, "could not read"):
            agent.main(["claude", "--resume", "chat-123"])
        self.attach.assert_not_called()

    def test_opencode_custom_directory_is_used_for_search_and_resume(self):
        self.history.return_value = [row("opencode")]
        agent.main(["opencode", "--resume", "chat-123", "--cwd", "/workspace/other"])
        self.history.assert_called_with(self.box, opencode_cwd="/workspace/other")
        self.assertIn("cd /workspace/other && exec opencode --session chat-123", self.attach.call_args.args[1])

    def test_invalid_inputs_fail_before_remote_access(self):
        for argv in (["claude", "--resume=--bad"], ["claude", "--resume", "bad;command"],
                     ["claude", "--resume", "chat-123", "--cwd", "relative"],
                     ["claude", "--name=", "--resume", "chat-123"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                agent.main(argv)
        self.listing.assert_not_called()
        self.active.assert_not_called()
        self.attach.assert_not_called()

    def test_creation_options_and_worker_shortcuts_cannot_resume(self):
        for flags in (["--local-dir", "."], ["--detach"], ["--cpu", "4"],
                      ["--cpu", "2"], ["--cp=2"], ["--memory=4Gi"], ["--lifetime", "8h"],
                      ["--env", "EXAMPLE=value"], ["--claude-env", "env_example"],
                      ["--import-codex-auth"], ["--telegram"]):
            with self.subTest(flags=flags), self.assertRaises(SystemExit) as error:
                agent.main(["claude", "--resume", "chat-123", *flags])
            self.assertEqual(error.exception.code, 2)
        for command in ("anthropic", "openai", "launch"):
            with self.subTest(command=command), self.assertRaises(SystemExit) as error:
                agent.main([command, "--resume", "chat-123"])
            self.assertEqual(error.exception.code, 2)
        with self.assertRaises(SystemExit):
            agent.main(["claude", "--cwd", "/workspace/other"])
        self.listing.assert_not_called()
        self.active.assert_not_called()
        self.assertIn("cannot be combined with --resume", self.errors.getvalue())
