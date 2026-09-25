"""Early Telegram readiness, background upload, and reversible snapshot capture."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from test_transfer import cli


class WorkspaceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.private = self.workspace / "home"
        self.private.mkdir(mode=0o700)
        self.secret = self.private / "credentials"
        self.secret.write_text("private fixture")
        self.secret.chmod(0o600)
        self.link = self.workspace / "link"
        self.link.symlink_to("home/credentials")
        (self.workspace / "missing-link").symlink_to("missing")
        self.stack.enter_context(patch("pathlib.Path.home", return_value=self.root / "local-home"))
        self.stack.enter_context(patch.object(cli, "MOUNT_PATH", str(self.workspace)))
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

        def execute(command, **kwargs):
            result = subprocess.run([sys.executable, *command[1:]], capture_output=True, text=True,
                                    env={**os.environ, "CWS_AGENT_DISK": "65Gi"}, timeout=10)
            return types.SimpleNamespace(result=lambda: result)

        self.sb = types.SimpleNamespace(exec=execute, snapshot=Mock())

    def assert_live_restored(self):
        self.assertTrue(self.link.is_symlink())
        self.assertEqual(os.readlink(self.link), "home/credentials")
        self.assertEqual(self.secret.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.private.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.workspace / ".cws-snapshot-restore.json").exists())

    def test_snapshot_retains_links_permissions_and_disk_for_restore(self):
        saved = self.root / "snapshot"

        def capture(**kwargs):
            self.assertIn("|disk=65Gi", kwargs["request_id"])
            self.assertFalse(self.link.is_symlink())
            self.assertTrue(self.secret.stat().st_mode & 0o004)
            shutil.copytree(self.workspace, saved)
            return types.SimpleNamespace(result=lambda: "snap")

        self.sb.snapshot.side_effect = capture
        self.assertEqual(cli.take_snapshot(self.sb, "test", "claude"), "snap")
        self.assert_live_restored()
        with patch.object(cli, "MOUNT_PATH", str(saved)):
            cli.snapshot_metadata(self.sb, "restore-snapshot")
        self.assertTrue((saved / "link").is_symlink())
        self.assertTrue((saved / "missing-link").is_symlink())
        self.assertEqual((saved / "home/credentials").stat().st_mode & 0o777, 0o600)

    def test_snapshot_failure_and_interrupt_restore_live_attributes(self):
        for error in (RuntimeError("backend failed"), KeyboardInterrupt()):
            self.sb.snapshot.side_effect = error
            with self.assertRaises(type(error)):
                cli.take_snapshot(self.sb, "test", "claude")
            self.assert_live_restored()

    def test_snapshot_backend_failure_is_actionable_and_restores_live_files(self):
        sid = "082876fd-4eb9-4bd6-8743-59b4875c0c25"
        self.sb.sandbox_id = "sandbox-test"
        self.sb.snapshot.side_effect = RuntimeError(
            f"Snapshot {sid} failed: CWSANDBOX_FSS_CREATE_FAILED private-detail")
        errors = io.StringIO()
        with patch.object(cli, "require_active", return_value=self.sb), \
                patch.object(cli, "probe_session_meta", return_value=("test", "claude")), \
                patch.object(cli, "Sandbox") as sandbox_class, \
                contextlib.redirect_stderr(errors):
            self.assertEqual(cli.cmd_snapshot(types.SimpleNamespace(name="test")), 1)
        self.assert_live_restored()
        sandbox_class.get_snapshot.assert_not_called()
        self.assertIn(sid, errors.getvalue())
        self.assertIn("CWSANDBOX_FSS_CREATE_FAILED", errors.getvalue())
        self.assertIn("No new backup was confirmed", errors.getvalue())
        self.assertNotIn("private-detail", errors.getvalue())

    def test_snapshot_does_not_replace_a_file_edited_during_capture(self):
        def capture(**kwargs):
            self.link.unlink()
            self.link.write_text("new work, not the placeholder")
            return types.SimpleNamespace(result=lambda: "snap")
        self.sb.snapshot.side_effect = capture
        cli.take_snapshot(self.sb, "test", "claude")
        self.assertEqual(self.link.read_text(), "new work, not the placeholder")
        self.assertFalse(self.link.is_symlink())

    def test_metadata_path_traversal_cannot_change_outside_files(self):
        outside = self.root / "outside"
        outside.write_text("keep")
        metadata = {"version": 1, "links": [], "modes": [["../outside", 0, 0, 0]], "placeholder": "x"}
        (self.workspace / ".cws-snapshot-restore.json").write_text(json.dumps(metadata))
        with self.assertRaises(RuntimeError):
            cli.snapshot_metadata(self.sb, "restore-snapshot")
        self.assertEqual(outside.read_text(), "keep")

    def test_automatic_snapshot_failure_does_not_stop_or_claim_ready(self):
        with patch.object(cli, "take_snapshot", side_effect=RuntimeError("failure")), \
                patch.object(cli, "stop_failed_sandbox") as stop:
            self.assertIsNone(cli.automatic_snapshot(self.sb, "test", "claude"))
        stop.assert_not_called()
        self.assertNotIn(" READY", self.output.getvalue())
        self.assertIn("cws-agent snapshot test", self.output.getvalue())


class BackgroundLaunchTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch("pathlib.Path.home", return_value=self.root))
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.sb = types.SimpleNamespace(sandbox_id="box")

    def args(self):
        with patch.object(cli, "cmd_launch", side_effect=lambda args: args):
            return cli.main(["launch", "--name", "test", "--telegram", "--local-dir", str(self.root),
                             "--dangerously-skip-permissions"])

    def test_launch_imports_and_signs_in_before_starting_background_upload_and_pairing(self):
        events = []
        with patch.object(cli.sys.stdin, "isatty", return_value=True), \
                patch.object(cli.sys.stdout, "isatty", return_value=True), \
                patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:test"}), \
                patch.object(cli, "find_active", return_value=None), \
                patch.object(cli, "build_env", return_value={}), \
                patch.object(cli, "scan_local_dir", return_value=cli.LocalDirectoryInventory(str(self.root), [], 0, 0, set())), \
                patch.object(cli, "provision_session", return_value=self.sb), \
                patch.object(cli, "sync_local_dir", side_effect=AssertionError("foreground upload blocked launch")), \
                patch.object(cli, "sync_agent_config", side_effect=lambda *a, **kw: events.append("imports")), \
                patch.object(cli, "pty_attach", side_effect=lambda *a: events.append("login") or 0), \
                patch.object(cli, "start_background_upload", side_effect=lambda *a: events.append("background")), \
                patch.object(cli, "cmd_bridge_telegram", side_effect=lambda *a: events.append("bridge") or 0):
            self.assertEqual(cli.cmd_launch(self.args()), 0)
        self.assertEqual(events, ["imports", "login", "background", "bridge"])

    def test_detached_worker_uses_private_log_and_no_terminal_input(self):
        with patch("subprocess.Popen") as spawn:
            status_path = Path(cli.start_background_upload(self.sb, self.args()))
        command = spawn.call_args.args[0]
        self.assertIn("--preserve-existing", command)
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])
        self.assertEqual(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(status_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((status_path.parent / "upload.log").stat().st_mode & 0o777, 0o600)
        state = cli.background_upload_status("test", "box")
        self.assertEqual(state["phase"], "starting")
        self.assertIsNone(cli.background_upload_status("test", "another-box"))

    def test_worker_uploads_then_snapshots_and_reports_ready(self):
        with patch("subprocess.Popen") as spawn:
            path = Path(cli.start_background_upload(self.sb, self.args()))
        command = spawn.call_args.args[0][2:]
        events = []
        with patch.object(cli, "require_active", return_value=self.sb), \
                patch.object(cli, "active_harness", return_value=cli.HARNESSES["claude"]), \
                patch.object(cli, "sync_local_dir", side_effect=lambda *a, **k: events.append("upload")), \
                patch.object(cli, "automatic_snapshot", side_effect=lambda *a: events.append("snapshot") or "snap"):
            self.assertEqual(cli.main(command), 0)
        self.assertEqual(events, ["upload", "snapshot"])
        self.assertEqual(json.loads(path.read_text())["phase"], "ready")

    def test_worker_does_not_claim_ready_when_snapshot_fails(self):
        with patch("subprocess.Popen") as spawn:
            path = Path(cli.start_background_upload(self.sb, self.args()))
        with patch.object(cli, "require_active", return_value=self.sb), \
                patch.object(cli, "active_harness", return_value=cli.HARNESSES["claude"]), \
                patch.object(cli, "sync_local_dir"), \
                patch.object(cli, "automatic_snapshot", return_value=None):
            self.assertEqual(cli.main(spawn.call_args.args[0][2:]), 1)
        self.assertEqual(json.loads(path.read_text())["phase"], "snapshot-failed")

    def test_worker_keeps_resume_guidance_after_upload_interruption(self):
        with patch("subprocess.Popen") as spawn:
            path = Path(cli.start_background_upload(self.sb, self.args()))
        with patch.object(cli, "require_active", return_value=self.sb), \
                patch.object(cli, "sync_local_dir", side_effect=cli.UploadPaused("resume-upload test-id")), \
                patch.object(cli, "automatic_snapshot") as snapshot, self.assertRaises(cli.UploadPaused):
            cli.main(spawn.call_args.args[0][2:])
        snapshot.assert_not_called()
        state = json.loads(path.read_text())
        self.assertEqual(state["phase"], "paused")
        self.assertIn("resume-upload test-id", state["message"])

    def test_worker_cannot_upload_into_a_replacement_sandbox(self):
        with patch("subprocess.Popen") as spawn:
            path = Path(cli.start_background_upload(self.sb, self.args()))
        with patch.object(cli, "require_active", return_value=types.SimpleNamespace(sandbox_id="different")), \
                patch.object(cli, "sync_local_dir") as upload, self.assertRaises(SystemExit):
            cli.main(spawn.call_args.args[0][2:])
        upload.assert_not_called()
        self.assertEqual(json.loads(path.read_text())["phase"], "paused")

    def test_resume_restores_saved_disk_and_can_reconnect_telegram(self):
        snap = types.SimpleNamespace(request_id="cwsa1|test|claude|123|disk=65Gi", size_bytes=1,
                                     file_system_snapshot_id="snap")
        with patch.object(cli, "find_active", return_value=None), \
                patch.object(cli, "latest_ready_snapshot", return_value=snap), \
                patch.object(cli, "build_env", return_value={}), \
                patch.object(cli, "provision_session", return_value=self.sb) as provision, \
                patch.object(cli, "read_backend_config", return_value=None), \
                patch.object(cli, "sync_agent_config"), \
                patch.object(cli, "cmd_bridge_telegram", return_value=0) as bridge:
            self.assertEqual(cli.main(["resume", "test", "--telegram", "--dangerously-skip-permissions"]), 0)
        self.assertEqual(provision.call_args.kwargs["disk"], "65Gi")
        self.assertTrue(bridge.call_args.args[0].yolo)
