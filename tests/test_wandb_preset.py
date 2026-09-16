"""Offline CLI and persistent-launcher contracts for W&B inference."""
import contextlib
import io
import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from test_terminal import agent
from test_consolidation import cli_parser
from test_opencode_integration import launcher_source


class WandbPresetTests(unittest.TestCase):
    def config(self, **options):
        args = types.SimpleNamespace(wandb=True, wandb_model=None, **options)
        return agent.wandb_opencode_config(args, agent.HARNESSES["opencode"], {"WANDB_API_KEY": "private-key"})

    def test_parser_supports_launch_and_restore(self):
        parser = cli_parser()
        for command in (["launch", "--name", "test"], ["restore", "test"]):
            args = parser.parse_args([*command, "--agent", "opencode", "--wandb"])
            self.assertTrue(args.wandb)

    def test_explicit_model_and_provider_without_secret_material(self):
        config = self.config()
        self.assertEqual(config["model"], "cws-wandb/" + agent.WANDB_OPENCODE_MODEL)
        self.assertEqual(config["small_model"], config["model"])
        self.assertEqual(config["enabled_providers"], ["cws-wandb"])
        self.assertNotIn("private-key", json.dumps(config))
        provider = config["provider"]["cws-wandb"]
        self.assertEqual(provider["options"]["apiKey"], "{env:WANDB_API_KEY}")
        self.assertEqual(provider["models"][agent.WANDB_OPENCODE_MODEL]["interleaved"], {"field": "reasoning"})
        self.assertNotIn("WANDB_API_KEY", agent.HARNESSES["opencode"].env_passthrough)

    def test_invalid_selection_or_credentials_fail_before_provision(self):
        parser = cli_parser()
        for flags, env in [(["--wandb"], {"WANDB_API_KEY": "key"}),
                           (["--agent", "opencode", "--wandb"], {"CWSANDBOX_API_KEY": "not-inference"}),
                           (["--agent", "opencode", "--wandb-model", "zai-org/GLM-5.2"], {}),
                           (["--agent", "opencode", "--wandb", "--wandb-model", "bad\nmodel"], {"WANDB_API_KEY": "key"})]:
            args = parser.parse_args(["launch", "--name", "test", *flags])
            with patch.object(agent, "find_active", return_value=None), \
                    patch.object(agent, "build_env", return_value=env), \
                    patch.object(agent, "provision_session") as provision, self.assertRaises(SystemExit):
                agent.cmd_launch(args)
            provision.assert_not_called()

    def test_optional_preset_and_model_override(self):
        harness = agent.HARNESSES["opencode"]
        self.assertIsNone(agent.wandb_opencode_config(types.SimpleNamespace(), harness, {}))
        config = agent.wandb_opencode_config(types.SimpleNamespace(wandb=True, wandb_model="zai-org/GLM-5.2"),
                                              harness, {"WANDB_API_KEY": "key"})
        self.assertEqual(config["model"], "cws-wandb/zai-org/GLM-5.2")

    def test_persistent_preset_overlays_model_preserving_mcp_and_permissions(self):
        source = launcher_source()
        inline = {"model": "other/provider", "mcp": {"fixture": {"enabled": False}}, "permission": {"bash": "deny"}}
        with patch.object(sys, "argv", ["opencode", "run", "--", "hello"]), \
                patch.dict(os.environ, {"WANDB_API_KEY": "key", "OPENCODE_CONFIG_CONTENT": json.dumps(inline)}, clear=True), \
                patch("pathlib.Path.is_file", return_value=True), \
                patch("pathlib.Path.read_text", return_value=json.dumps(self.config())), \
                patch.object(os, "execv") as execute:
            exec(compile(source, "opencode-launcher", "exec"), {})
            combined = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
            self.assertEqual(combined["mcp"], inline["mcp"])
            self.assertEqual(combined["permission"], inline["permission"])
            self.assertEqual(combined["model"], self.config()["model"])
            execute.assert_called_once()

    def test_unreadable_preset_or_inline_config_fail_closed(self):
        source = launcher_source()
        for content, env in [("SECRET-bad-json", {}), (json.dumps(self.config()), {"OPENCODE_CONFIG_CONTENT": "[]"})]:
            with patch.object(sys, "argv", ["opencode"]), patch.dict(os.environ, env, clear=True), \
                    patch("pathlib.Path.is_file", return_value=True), patch("pathlib.Path.read_text", return_value=content), \
                    patch.object(os, "execv") as execute, self.assertRaises(SystemExit) as error:
                exec(compile(source, "opencode-launcher", "exec"), {})
            self.assertNotIn("SECRET", str(error.exception))
            execute.assert_not_called()

    def test_install_preset_is_atomic_and_does_not_embed_key(self):
        with patch.object(agent, "exec_retry", return_value=types.SimpleNamespace(returncode=0)) as execute, \
                contextlib.redirect_stdout(io.StringIO()):
            agent.configure_wandb_opencode("sandbox", self.config())
        script = execute.call_args.args[1][2]
        compile(script, "remote-preset", "exec")
        self.assertIn("mkstemp", script)
        self.assertIn("os.replace", script)
        self.assertNotIn("private-key", script)
        self.assertEqual(execute.call_args.kwargs["attempts"], 1)
