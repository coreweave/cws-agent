"""Offline contracts for the dedicated OpenCode executable and policy wrapper."""
import json
import os
import shlex
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch

from test_terminal import agent


def launcher_source():
    script = agent.OPENCODE_BOOTSTRAP.split("<<'PYOPENCODE'\n", 1)[1].split("\nPYOPENCODE", 1)[0]
    target = Mock()
    with patch("pathlib.Path", return_value=target):
        exec(compile(script, "opencode-bootstrap", "exec"), {})
    target.chmod.assert_called_once_with(0o755)
    return target.write_text.call_args.args[0]


class OpenCodeIntegrationTests(unittest.TestCase):
    def invoke_launcher(self, args, inherited=None, config=None):
        def debug(command, **kwargs):
            self.assertEqual(command, ["/opt/agent/.opencode/bin/opencode", "debug", "config"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            kwargs["stdout"].write(json.dumps(config or {}).encode())
            return types.SimpleNamespace(returncode=0)
        with patch.object(sys, "argv", ["opencode", *args]), patch.dict(os.environ, inherited or {}, clear=True), patch.object(os, "execv") as execute, patch("subprocess.run", side_effect=debug):
            exec(compile(launcher_source(), "opencode-launcher", "exec"), {})
            return execute.call_args.args, dict(os.environ)

    def test_bootstrap_shell_is_valid_and_installs_pinned_native_binary(self):
        result = subprocess.run(["bash", "-n"], input=agent.OPENCODE_BOOTSTRAP, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("https://opencode.ai/install", agent.OPENCODE_BOOTSTRAP)
        self.assertIn(agent.OPENCODE_VERSION_DEFAULT, agent.OPENCODE_BOOTSTRAP)
        self.assertIn("--no-modify-path", agent.OPENCODE_BOOTSTRAP)
        self.assertIn("test -x /opt/agent/.opencode/bin/opencode", agent.OPENCODE_BOOTSTRAP)

    def test_interactive_login_and_environment_contracts(self):
        harness = agent.HARNESSES["opencode"]
        self.assertEqual(harness.interactive_cmd, "exec opencode")
        self.assertEqual(harness.login_cmd, "exec opencode auth login")
        self.assertEqual(harness.agent_bin, "opencode")
        self.assertIn("OPENCODE_API_KEY", harness.env_passthrough)
        self.assertIn("OPENCODE_VERSION", harness.env_passthrough)
        self.assertNotIn("OPENCODE_CONFIG", harness.env_passthrough)
        invocation, env = self.invoke_launcher(["auth", "login"])
        self.assertEqual(invocation[1][1:], ["auth", "login"])
        self.assertEqual(env["HOME"], "/workspace/home")
        self.assertEqual(env["XDG_CONFIG_HOME"], "/workspace/home/.config")
        self.assertEqual(env["XDG_DATA_HOME"], "/workspace/home/.local/share")
        self.assertEqual(env["XDG_STATE_HOME"], "/workspace/home/.local/state")
        self.assertEqual(env["XDG_CACHE_HOME"], "/opt/cache")
        self.assertEqual(env["OPENCODE_DISABLE_AUTOUPDATE"], "1")

    def test_accept_edits_does_not_enable_shell_or_external_tools(self):
        harness = agent.HARNESSES["opencode"]
        args = types.SimpleNamespace(yolo=False, permission_mode="accept-edits")
        flags = agent.permission_flags(harness, args)
        invocation, env = self.invoke_launcher(shlex.split(flags))
        policy = json.loads(env["OPENCODE_PERMISSION"])
        self.assertEqual(policy["edit"], "allow")
        for tool in ("*", "bash", "external_directory", "doom_loop"):
            self.assertEqual(policy[tool], "ask")
        self.assertEqual(policy["read"]["*.env"], "deny")
        self.assertNotIn("--auto", invocation[1])
        self.assertEqual(agent.permission_flags(harness, args, headless=True), flags)

    def test_native_leaves_user_policy_and_bypass_uses_native_auto(self):
        harness = agent.HARNESSES["opencode"]
        native = types.SimpleNamespace(yolo=False, permission_mode="native")
        self.assertEqual(agent.permission_flags(harness, native), "")
        _, env = self.invoke_launcher([], {"OPENCODE_PERMISSION": '{"bash":"deny"}'})
        self.assertEqual(env["OPENCODE_PERMISSION"], '{"bash":"deny"}')
        bypass = types.SimpleNamespace(yolo=True, permission_mode="accept-edits")
        flags = agent.permission_flags(harness, bypass)
        invocation, env = self.invoke_launcher(["run", *shlex.split(flags), "--", "hello"],
                                                {"OPENCODE_PERMISSION": '{"bash":"deny"}'})
        self.assertIn("--auto", invocation[1])
        self.assertEqual(env["OPENCODE_PERMISSION"], '{"bash":"deny"}')
        self.assertFalse(any(arg.startswith("--cws-") for arg in invocation[1]))

    def test_headless_prompts_are_literal_not_permission_options(self):
        harness = agent.HARNESSES["opencode"]
        command = harness.headless_fmt.format(prompt=shlex.quote("--cws-permission=bypass"),
                                              extra=" --cws-permission=accept-edits")
        invocation, env = self.invoke_launcher(shlex.split(command)[1:])
        self.assertEqual(invocation[1][1:], ["run", "--", "--cws-permission=bypass"])
        self.assertNotIn("--auto", invocation[1])
        self.assertEqual(json.loads(env["OPENCODE_PERMISSION"])["bash"], "ask")

    def test_interactive_prompt_does_not_become_private_wrapper_flag(self):
        invocation, env = self.invoke_launcher(["--prompt", "--cws-permission=bypass",
                                                "--cws-permission=accept-edits"])
        self.assertEqual(invocation[1][1:], ["--prompt", "--cws-permission=bypass"])
        self.assertEqual(json.loads(env["OPENCODE_PERMISSION"])["bash"], "ask")

    def test_invalid_private_mode_fails_closed(self):
        with self.assertRaisesRegex(SystemExit, "invalid"):
            self.invoke_launcher(["--cws-permission=typo"])

    def test_accept_edits_preserves_explicit_deny_and_granular_rules(self):
        for permission in ({"edit": "deny"}, {"edit": {"secret/**": "deny"}},
                           {"*": "deny"}, "deny", {"read": {"private/**": "deny"}}):
            with self.subTest(permission=permission):
                _, env = self.invoke_launcher(["--cws-permission=accept-edits"],
                                               config={"permission": permission})
                self.assertNotIn("OPENCODE_PERMISSION", env)

    def test_unreadable_native_permissions_fail_closed_without_secret_output(self):
        for config in (["bad"], {"permission": {"edit": "typo"}}):
            with self.subTest(config=config), self.assertRaisesRegex(SystemExit, "could not safely read"):
                self.invoke_launcher(["--cws-permission=accept-edits"], config=config)

    def test_explicit_global_ask_is_not_overridden_by_baseline_edit_allow(self):
        _, env = self.invoke_launcher(["--cws-permission=accept-edits"], config={"permission": {"*": "ask"}})
        self.assertNotIn("OPENCODE_PERMISSION", env)

    def test_native_rule_order_is_not_changed_by_baseline_injection(self):
        policies = (
            {"edit": "allow", "*": "deny"},
            {"*": "deny", "edit": "allow"},
            {"read": {"*.env": "allow", "*": "deny"}},
            {"read": {"*": "deny", "*.env": "allow"}},
            {"read": {"*": "deny"}},
            {"edit": {"secret/**": "deny"}},
        )
        for policy in policies:
            with self.subTest(policy=policy):
                _, env = self.invoke_launcher(["--cws-permission=accept-edits"],
                                               config={"permission": policy})
                # No merge can append '*' after secret/**, or baseline
                # '*.env.example':allow after the user's read-all denial.
                self.assertNotIn("OPENCODE_PERMISSION", env)
                inherited = json.dumps(policy)
                _, env = self.invoke_launcher(["--cws-permission=accept-edits"],
                                               inherited={"OPENCODE_PERMISSION": inherited},
                                               config={"permission": policy})
                self.assertEqual(env["OPENCODE_PERMISSION"], inherited)


if __name__ == "__main__":
    unittest.main()
