import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location("harness_smoke", Path(__file__).parents[1] / "smoke_harnesses.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class HarnessSmokeTests(unittest.TestCase):
    def cli(self, agent="cursor"):
        cli = Mock()
        cli.active_harness.return_value = types.SimpleNamespace(name=agent)
        cli.cursor_auth_status.return_value = True
        return cli

    def test_default_is_read_only_and_does_not_call_models_or_cloud_lifecycle(self):
        cli = self.cli()
        probe = dict(executable=True, version_command=True, resume_flag=True, auth_command=False)
        output = io.StringIO()
        with patch.object(smoke, "load_cli", return_value=cli), patch.object(smoke, "probe_existing", return_value=probe), contextlib.redirect_stdout(output):
            self.assertEqual(smoke.main(["existing"]), 0)
        cli.require_active.assert_called_once_with("existing")
        cli.telegram_agent_reply.assert_not_called()
        cli.take_snapshot.assert_not_called()
        cli.cmd_launch.assert_not_called()
        cli.cmd_restore.assert_not_called()
        cli.cmd_down.assert_not_called()
        self.assertIn("does not prove model access", output.getvalue())
        self.assertIn("NOT TESTED", output.getvalue())

    def test_optional_turns_really_resume_same_id_and_hide_response(self):
        cli = self.cli()
        seen = []
        def reply(sb, harness, prompt, state, timeout, args):
            seen.append((prompt, dict(state)))
            state.update(id="chat_test", started=True)
            return "CWS_SMOKE_FIRST"
        cli.telegram_agent_reply.side_effect = reply
        self.assertTrue(smoke.model_turns(cli, object(), types.SimpleNamespace(name="cursor"), 60))
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0][1], {})
        self.assertEqual(seen[1][1]["id"], "chat_test")
        self.assertNotIn("CWS_SMOKE_FIRST", seen[1][0])

    def test_failed_turn_does_not_send_second_prompt(self):
        cli = self.cli()
        cli.telegram_agent_reply.return_value = "unavailable"
        with self.assertRaises(RuntimeError):
            smoke.model_turns(cli, object(), types.SimpleNamespace(name="cursor"), 60)
        self.assertEqual(cli.telegram_agent_reply.call_count, 1)

    def test_logged_out_cursor_fails_before_any_prompt_or_chat_creation(self):
        cli = self.cli()
        cli.cursor_auth_status.return_value = False
        with self.assertRaisesRegex(smoke.SmokeLoginRequired, "Run cws-agent login"):
            smoke.model_turns(cli, object(), types.SimpleNamespace(name="cursor"), 60)
        cli.telegram_agent_reply.assert_not_called()

    def test_cursor_status_probe_requests_status_specific_json(self):
        seen = []
        def run(argv, **kwargs):
            seen.append(argv)
            return types.SimpleNamespace(returncode=0)
        with patch("os.path.isfile", return_value=True), patch("os.access", return_value=True), patch("subprocess.run", side_effect=run):
            result = smoke.native_probe("cursor")
        self.assertIn(["/opt/agent/.local/bin/cursor-agent", "status", "--format", "json"], seen)
        self.assertNotIn("authenticated", result)

    def test_remote_unexpected_or_sensitive_metadata_is_rejected(self):
        cli = self.cli()
        cli.SH_WRAP = "cd /workspace/project; {cmd}"
        cli.exec_retry.return_value = types.SimpleNamespace(returncode=0, stdout=json.dumps({"token": "fixture-secret"}))
        with self.assertRaisesRegex(RuntimeError, "invalid probe metadata"):
            smoke.probe_existing(cli, object(), "cursor")

    def test_remote_helper_source_runs_standalone_without_native_binary(self):
        import inspect
        script = (inspect.getsource(smoke.native_probe) + "\n"
                  "from unittest.mock import patch\n"
                  "with patch('os.path.isfile', return_value=False):\n"
                  " print(native_probe('cursor'))\n")
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "{'executable': False}")

    def test_native_output_is_never_returned_by_probe(self):
        def run(argv, **kwargs):
            data = b"fixture-private-credential"
            if "--help" in argv:
                data += b" --resume"
            kwargs["stdout"].write(data)
            return types.SimpleNamespace(returncode=0)
        with patch("os.path.isfile", return_value=True), patch("os.access", return_value=True), patch("subprocess.run", side_effect=run):
            result = smoke.native_probe("cursor")
        self.assertEqual(result, {"executable": True, "version_command": True, "resume_flag": True, "auth_command": True})
        self.assertNotIn("fixture-private-credential", json.dumps(result))

    def test_opencode_help_on_stderr_and_empty_history_are_supported(self):
        def run(argv, **kwargs):
            if "--help" in argv:
                kwargs["stderr"].write(b"--session")
            return types.SimpleNamespace(returncode=0)
        with patch("os.path.isfile", return_value=True), patch("os.access", return_value=True), patch("subprocess.run", side_effect=run):
            result = smoke.native_probe("opencode")
        self.assertTrue(result["resume_flag"])
        self.assertTrue(result["history_command"])
        self.assertEqual(result["history_count"], 0)

    def test_other_harness_rejected_before_native_execution(self):
        with patch.object(smoke, "load_cli", return_value=self.cli("claude")), patch.object(smoke, "probe_existing") as probe:
            with self.assertRaises(RuntimeError):
                smoke.main(["existing"])
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
