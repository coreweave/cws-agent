"""Sandbox authentication routing; all service operations are mocked."""
import contextlib
import io
import importlib.util
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_terminal import agent


class SandboxAuthTests(unittest.TestCase):
    def test_wandb_default_and_explicit_coreweave_compatibility(self):
        for env, expected in [
            ({}, agent.AuthStrategy.WANDB),
            ({"WANDB_API_KEY": "wandb-fixture"}, agent.AuthStrategy.WANDB),
            ({"CWSANDBOX_API_KEY": " "}, agent.AuthStrategy.WANDB),
            ({"CWSANDBOX_API_KEY": "cw-fixture"}, agent.AuthStrategy.COREWEAVE_API_KEY),
            ({"CWSANDBOX_API_KEY": "cw-fixture", "WANDB_API_KEY": "wandb-fixture"},
             agent.AuthStrategy.COREWEAVE_API_KEY),
        ]:
            with self.subTest(env=list(env)), patch.dict(os.environ, env, clear=True):
                self.assertEqual(agent.sandbox_auth(), expected)

    def test_create_restore_lookup_and_snapshot_management_use_selected_auth(self):
        for env, expected in [({"WANDB_API_KEY": "wandb-fixture"}, agent.AuthStrategy.WANDB),
                              ({"CWSANDBOX_API_KEY": "cw-fixture"}, agent.AuthStrategy.COREWEAVE_API_KEY)]:
            sb = types.SimpleNamespace(sandbox_id="box")
            snap = types.SimpleNamespace(source_sandbox_id="box", file_system_snapshot_id="snap",
                                         created_at=None, status="ready", size_bytes=0)
            with self.subTest(strategy=expected), patch.dict(os.environ, env, clear=True), \
                    patch.object(agent, "Sandbox") as sdk, contextlib.redirect_stdout(io.StringIO()):
                sdk.list.return_value.result.return_value = [sb]
                sdk.list_snapshots.return_value.result.return_value = [snap]
                sdk.get_snapshot.return_value.result.return_value = snap
                for restore_id in (None, "snap"):
                    agent.create_session_sandbox(name="test", harness=agent.HARNESSES["claude"],
                                                 image="ubuntu:24.04", lifetime_seconds=3600,
                                                 cpu="2", memory="4Gi", disk="10Gi", env={},
                                                 mode=None, restore_snapshot_id=restore_id)
                    self.assertEqual(sdk.run.call_args.kwargs["auth"], expected)
                    self.assertNotIn("WANDB_API_KEY", sdk.run.call_args.kwargs["environment_variables"])
                    self.assertNotIn("CWSANDBOX_API_KEY", sdk.run.call_args.kwargs["environment_variables"])
                self.assertIs(agent.require_active("test"), sb)
                self.assertEqual(agent.session_snapshots("test"), [snap])
                agent.cmd_prune(types.SimpleNamespace(name="test", keep=0))
                with patch.object(agent, "probe_session_meta", return_value=("test", "claude")), \
                        patch.object(agent, "take_snapshot", return_value="snap"):
                    agent.cmd_snapshot(types.SimpleNamespace(name="test"))
                sdk.list.return_value.result.return_value = []
                agent.cmd_list(types.SimpleNamespace())
                for method in (sdk.run, sdk.list, sdk.list_snapshots, sdk.get_snapshot, sdk.delete_snapshot):
                    for call in method.call_args_list:
                        self.assertEqual(call.kwargs["auth"], expected)

    def test_opencode_only_receives_wandb_key_when_requested(self):
        snapshot = types.SimpleNamespace(request_id=None, size_bytes=0,
                                         file_system_snapshot_id="snapshot-fixture")
        for command in ("launch", "restore"):
            for options, expected in (([], None), (["--wandb"], "wandb-fixture"),
                                      (["--env-passthrough", "WANDB_API_KEY"], "wandb-fixture"),
                                      (["--env", "WANDB_API_KEY=explicit-fixture"], "explicit-fixture")):
                with self.subTest(command=command, options=options), \
                        patch.dict(os.environ, {"WANDB_API_KEY": "wandb-fixture"}, clear=True), \
                        patch.object(agent, "find_active", return_value=None), \
                        patch.object(agent, "latest_ready_snapshot", return_value=snapshot), \
                        patch.object(agent, "provision_session", side_effect=RuntimeError("stop before provisioning")) as provision, \
                        contextlib.redirect_stdout(io.StringIO()), \
                        self.assertRaisesRegex(RuntimeError, "stop before provisioning"):
                    agent.main([command, "test", "--agent", "opencode", *options])
                self.assertEqual(provision.call_args.kwargs["env"].get("WANDB_API_KEY"), expected)

    def test_authentication_failure_prints_hint_without_sdk_details(self):
        for deferred in (False, True):
            error = agent.CWSandboxAuthenticationError("private-fixture must not be printed")
            with self.subTest(deferred=deferred), patch.object(agent, "Sandbox") as sdk, \
                    contextlib.redirect_stderr(io.StringIO()) as output:
                if deferred:
                    sdk.list.return_value.result.side_effect = error
                else:
                    sdk.list.side_effect = error
                self.assertEqual(agent.main(["list"]), 1)
            self.assertEqual(len(output.getvalue().splitlines()), 1)
            for hint in ("WANDB_API_KEY", "CWSANDBOX_API_KEY", "wandb login"):
                self.assertIn(hint, output.getvalue())
            self.assertNotIn("private-fixture", output.getvalue())
            self.assertNotIn("Traceback", output.getvalue())

    def test_openai_smoke_cleanup_uses_selected_auth_after_launch_failure(self):
        spec = importlib.util.spec_from_file_location(
            "openai_smoke_test", Path(__file__).parents[1] / "smoke_openai_agents.py")
        smoke = importlib.util.module_from_spec(spec)
        for key in ("WANDB_API_KEY", "CWSANDBOX_API_KEY", None):
            cli = Mock()
            cli.main.return_value = 1
            cli.Sandbox.list.return_value.result.return_value = []
            cli.session_snapshots.return_value = [types.SimpleNamespace(file_system_snapshot_id="snapshot-fixture")]
            credentials = {"OPENAI_API_KEY": "platform-fixture", "OPENAI_EXECUTOR_API_KEY": "executor-fixture"}
            if key:
                credentials[key] = "sandbox-fixture"
            with self.subTest(key=key), patch.dict(os.environ, credentials, clear=True), \
                    patch.dict(sys.modules, {"smoke_harnesses": types.SimpleNamespace(load_cli=lambda: cli)}), \
                    contextlib.redirect_stdout(io.StringIO()):
                spec.loader.exec_module(smoke)
                with self.assertRaisesRegex(RuntimeError, "launch failed"):
                    smoke.main([])
            cli.main.assert_called_once()
            self.assertEqual(cli.Sandbox.list.call_args.kwargs["auth"], cli.sandbox_auth.return_value)
            cli.Sandbox.delete_snapshot.assert_called_once_with(
                "snapshot-fixture", missing_ok=True, auth=cli.sandbox_auth.return_value)

    def test_background_upload_inherits_auth_without_putting_keys_in_command(self):
        args = types.SimpleNamespace(name="test", no_git=False, exclude=[], local_dir=".")
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(agent, "background_job_folder", return_value=Path(folder)), \
                patch.dict(os.environ, {"WANDB_API_KEY": "wandb-fixture"}, clear=True), \
                patch("subprocess.Popen") as spawn, contextlib.redirect_stdout(io.StringIO()):
            agent.start_background_upload(types.SimpleNamespace(sandbox_id="box"), args)
        self.assertNotIn("wandb-fixture", repr(spawn.call_args))
        self.assertNotIn("env", spawn.call_args.kwargs)  # Popen inherits the parent environment.
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])
        self.assertEqual(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
