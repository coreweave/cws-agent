"""Checkpoint crash recovery and CLI integration. No cloud calls or model use."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from test_documented_sessions import load_documented_cli, operation


app = load_documented_cli()
REAL_GATE = app.CheckpointCoordinator.gate
SOURCE = "00000000-0000-4000-8000-000000000001"
SNAPSHOT = "00000000-0000-4000-8000-000000000002"
IMAGE = "example.test/agent@sha256:" + "a" * 64


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cws-checkpoint-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / "checkpoint"
        self.hook = self.root / "gate"
        self.hook.write_text("#!/bin/sh\nexit 0\n")
        self.hook.chmod(0o700)
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.events, self.requests = [], []
        container = types.SimpleNamespace(
            image=IMAGE, environment_variables={"CWS_AGENT_NAME": "example", "CWS_AGENT_HARNESS": "claude",
                                               "CWS_AGENT_DISK": "12Gi", "PRIVATE_TOKEN": "must-not-save"},
            volume_mounts=[types.SimpleNamespace(volume="workspace", mount_path="/workspace", sub_path=None, read_only=False)],
            resources=types.SimpleNamespace(requests={"cpu": "3", "memory": "6Gi"}))
        self.source = types.SimpleNamespace(sandbox_id=SOURCE, containers=[container], status="running",
                                            snapshot=Mock(side_effect=self.snapshot), stop=Mock(side_effect=self.stop))
        self.api = types.SimpleNamespace(list=Mock(return_value=operation([self.source])),
                                         from_id=Mock(side_effect=lambda *a, **kw: operation(self.source)),
                                         get_snapshot=Mock(side_effect=self.receipt))
        self.gate = Mock(side_effect=lambda plan, action: self.events.append(action))
        self.addCleanup(patch.stopall)
        patch.object(app, "Sandbox", self.api).start()
        patch.object(app.CheckpointCoordinator, "gate", self.gate).start()
        patch.dict(os.environ, {"CWSANDBOX_API_KEY": "synthetic-test-key", "CWSANDBOX_BASE_URL": "https://example.test"}).start()

    def call(self, *extra):
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            return app.main(["down", "example", "--checkpoint-dir", str(self.directory),
                             "--writer-gate", str(self.hook), *extra])

    def state(self):
        return json.loads((self.directory / "journal.json").read_text())

    def snapshot(self, **kwargs):
        self.assertEqual(self.events[-1], "quiesce")
        self.assertEqual(self.state()["phase"], "SNAPSHOTTING")
        self.assertFalse(kwargs["wait_for_ready"])
        self.requests.append(kwargs["request_id"])
        return operation(SNAPSHOT)

    def receipt(self, snapshot_id, **kwargs):
        return operation(types.SimpleNamespace(
            file_system_snapshot_id=snapshot_id, source_sandbox_id=SOURCE,
            source_volume_name="workspace", request_id=self.state()["plan"]["request_id"],
            status="ready", size_bytes=42))

    def stop(self, **kwargs):
        manifest = json.loads((self.directory / "manifest.json").read_text())
        self.assertEqual(manifest["snapshot_id"], SNAPSHOT)
        self.assertEqual(self.state()["phase"], "STOPPING")
        self.assertEqual(kwargs, {"snapshot_on_stop": False, "missing_ok": True})
        self.events.append("stop")
        self.source.status = "terminated"
        return operation(None)

    def test_commit_precedes_stop_and_release_follows_terminal_confirmation(self):
        self.assertEqual(self.call(), 0)
        self.assertEqual(self.events, ["quiesce", "stop", "release"])
        self.assertEqual(self.state()["phase"], "SUSPENDED")
        self.assertTrue(self.state()["gate_released"])
        self.assertEqual(self.call(), 0)  # No new discovery, snapshot, stop, or release.
        self.api.list.assert_called_once()
        self.source.snapshot.assert_called_once()
        self.source.stop.assert_called_once()
        for name in ("journal.json", "manifest.json", "lock"):
            self.assertEqual((self.directory / name).stat().st_mode & 0o777, 0o600)
            self.assertNotIn("must-not-save", (self.directory / name).read_text())
            self.assertNotIn("synthetic-test-key", (self.directory / name).read_text())

    def test_lost_create_response_retries_same_request_id(self):
        def lost(**kwargs):
            self.snapshot(**kwargs)
            raise TimeoutError("secret request details")
        self.source.snapshot.side_effect = lost
        self.assertEqual(self.call(), 1)
        self.assertIsNone(self.state()["snapshot_id"])
        self.source.stop.assert_not_called()
        self.assertNotIn("secret request details", self.stderr.getvalue())
        self.source.snapshot.side_effect = self.snapshot
        self.assertEqual(self.call(), 0)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0], self.requests[1])
        self.api.list.assert_called_once()

    def test_manifest_write_failure_keeps_source_and_gate(self):
        original = app.CheckpointJournal.write
        def fail_manifest(journal, name, value):
            if name == "manifest.json":
                raise OSError("disk full")
            return original(journal, name, value)
        with patch.object(app.CheckpointJournal, "write", fail_manifest):
            self.assertEqual(self.call(), 1)
        self.assertEqual(self.state()["snapshot_id"], SNAPSHOT)
        self.assertEqual(self.events, ["quiesce"])
        self.source.stop.assert_not_called()
        self.assertEqual(self.call(), 0)
        self.source.snapshot.assert_called_once()

    def test_rename_before_failed_fsync_is_recovered_before_stop(self):
        original = app.CheckpointJournal.write
        sync_directory = app.CheckpointJournal.sync_directory
        def fail_manifest_fsync(path):
            if path == self.directory and (path / "manifest.json").exists():
                raise OSError("directory fsync failed")
            return sync_directory(path)
        with patch.object(app.CheckpointJournal, "sync_directory", staticmethod(fail_manifest_fsync)):
            self.assertEqual(self.call(), 1)
        self.source.stop.assert_not_called()
        self.assertTrue((self.directory / "manifest.json").exists())
        self.assertEqual(self.call("--abort-checkpoint"), 1)
        writes = []
        def record(journal, name, value):
            writes.append(name)
            return original(journal, name, value)
        with patch.object(app.CheckpointJournal, "write", record):
            self.assertEqual(self.call(), 0)
        self.assertEqual(writes[0], "manifest.json")
        self.assertEqual(self.events, ["quiesce", "stop", "release"])

    def test_lost_stop_response_recovers_without_resnapshot(self):
        def lost(**kwargs):
            self.stop(**kwargs)
            raise TimeoutError("lost stop response")
        self.source.stop.side_effect = lost
        self.assertEqual(self.call(), 1)
        self.assertEqual(self.state()["phase"], "STOPPING")
        self.assertEqual(self.events, ["quiesce", "stop"])
        self.assertEqual(self.call(), 0)
        self.source.snapshot.assert_called_once()
        self.source.stop.assert_called_once()
        self.assertEqual(self.events[-1], "release")

    def test_stop_reply_without_terminal_state_does_not_release(self):
        self.source.stop.side_effect = lambda **kw: operation(None)
        with patch.object(app.time, "sleep", side_effect=TimeoutError("still running")):
            self.assertEqual(self.call(), 1)
        self.assertEqual(self.state()["phase"], "STOPPING")
        self.assertEqual(self.events, ["quiesce"])
        self.source.status = "completed"
        self.assertEqual(self.call(), 0)
        self.assertEqual(self.events[-1], "release")

    def test_receipt_mismatch_never_commits_or_stops(self):
        for field, value in (("file_system_snapshot_id", "wrong"), ("source_sandbox_id", "wrong"),
                             ("source_volume_name", "wrong"), ("request_id", "wrong"), ("status", "failed")):
            with self.subTest(field=field):
                def wrong(snapshot_id, **kw):
                    snapshot = self.receipt(snapshot_id).result()
                    setattr(snapshot, field, value)
                    return operation(snapshot)
                self.api.get_snapshot.side_effect = wrong
                self.assertEqual(self.call(), 1)
                self.assertFalse((self.directory / "manifest.json").exists())
                self.source.stop.assert_not_called()
                self.assertNotIn("release", self.events)

    def test_abort_is_durable_before_release_and_cannot_be_committed(self):
        self.source.snapshot.side_effect = TimeoutError("unknown")
        self.assertEqual(self.call(), 1)
        def gate(plan, action):
            self.assertEqual(action, "release")
            self.assertEqual(self.state()["phase"], "ABANDONED")
            raise TimeoutError("lost release acknowledgement")
        self.gate.side_effect = gate
        self.assertEqual(self.call("--abort-checkpoint"), 1)
        self.gate.side_effect = lambda plan, action: self.events.append(action)
        self.assertEqual(self.call("--abort-checkpoint"), 0)
        self.assertEqual(self.call(), 1)
        self.assertFalse((self.directory / "manifest.json").exists())
        self.source.snapshot.assert_called_once()
        self.source.stop.assert_not_called()

    def test_gate_failure_leaves_recoverable_quiescing_state(self):
        self.gate.side_effect = TimeoutError("quiesce may have succeeded")
        self.assertEqual(self.call(), 1)
        self.assertEqual(self.state()["phase"], "QUIESCING")
        self.source.snapshot.assert_not_called()
        self.gate.side_effect = lambda plan, action: self.events.append(action)
        self.assertEqual(self.call(), 0)

    def test_only_typed_source_absence_allows_post_commit_release(self):
        from cwsandbox import SandboxNotFoundError
        self.api.from_id.side_effect = [operation(self.source), TimeoutError("unknown source state")]
        self.assertEqual(self.call(), 1)
        self.assertEqual(self.events, ["quiesce"])
        self.api.from_id.side_effect = SandboxNotFoundError("source deleted")
        self.assertEqual(self.call(), 0)
        self.assertEqual(self.events[-1], "release")
        self.source.stop.assert_not_called()

    def test_release_failure_is_retried_without_stopping_again(self):
        def gate(plan, action):
            if action == "release":
                raise TimeoutError("lost release reply")
            self.events.append(action)
        self.gate.side_effect = gate
        self.assertEqual(self.call(), 1)
        self.assertEqual(self.state()["phase"], "SUSPENDED")
        self.assertFalse(self.state()["gate_released"])
        with self.assertRaises(app.CheckpointError):
            app.checkpoint_restore(self.directory, "example")
        self.gate.side_effect = lambda plan, action: self.events.append(action)
        self.assertEqual(self.call(), 0)
        self.source.stop.assert_called_once()
        self.source.snapshot.assert_called_once()

    def test_hook_uses_argv_bounded_timeout_and_suppresses_output(self):
        import subprocess
        with app.CheckpointJournal(self.directory) as journal:
            coordinator = app.CheckpointCoordinator(journal, 30)
            plan = app.checkpoint_plan(self.source, "example", str(self.hook))
            with patch.object(subprocess, "run", return_value=types.SimpleNamespace(returncode=1)) as run:
                with self.assertRaises(app.CheckpointError):
                    REAL_GATE(coordinator, plan, "quiesce")
            self.assertEqual(run.call_args.args[0], [str(self.hook), "quiesce", SOURCE,
                                                   plan["operation"], str(self.directory)])
            options = run.call_args.kwargs
            self.assertEqual(options["stdout"], subprocess.DEVNULL)
            self.assertEqual(options["stderr"], subprocess.DEVNULL)
            self.assertFalse(options.get("shell", False))
            self.assertLessEqual(options["timeout"], 30)

    def test_invalid_flags_fail_before_cloud_calls(self):
        for extra in (("--no-snapshot",), ("--checkpoint-timeout", "0"), ("--checkpoint-timeout", "601")):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self.call(*extra)
        self.api.list.assert_not_called()
        self.assertEqual(self.call("--abort-checkpoint"), 1)
        self.api.list.assert_not_called()

    def test_ambiguous_source_or_unpinned_image_rejected_before_gate(self):
        self.api.list.return_value = operation([self.source, self.source])
        self.assertEqual(self.call(), 1)
        self.api.list.return_value = operation([self.source])
        self.source.containers[0].image = "example.test/agent:latest"
        self.assertEqual(self.call(), 1)
        self.gate.assert_not_called()

    def test_route_or_hook_change_does_not_touch_source(self):
        self.source.snapshot.side_effect = TimeoutError()
        self.assertEqual(self.call(), 1)
        self.gate.reset_mock()
        with patch.dict(os.environ, {"CWSANDBOX_BASE_URL": "https://other.example.test"}):
            self.assertEqual(self.call(), 1)
        other = self.root / "other-gate"
        other.write_text("#!/bin/sh\nexit 0\n")
        other.chmod(0o700)
        self.assertEqual(self.call("--writer-gate", str(other)), 1)
        self.gate.assert_not_called()

    def test_restore_uses_exact_receipt_image_and_resource_defaults(self):
        self.assertEqual(self.call(), 0)
        with patch.object(app, "latest_ready_snapshot") as latest, \
                patch.object(app, "find_active", return_value=None), \
                patch.object(app, "build_env", return_value={}), \
                patch.object(app, "wandb_opencode_config", return_value=None), \
                patch.object(app, "configure_wandb_opencode"), \
                patch.object(app, "read_backend_config", return_value=None), \
                patch.object(app, "sync_agent_config"), \
                patch.object(app, "provision_session", return_value=types.SimpleNamespace(sandbox_id="sb-example")) as provision, \
                contextlib.redirect_stdout(self.stdout):
            self.assertEqual(app.main(["restore", "example", "--checkpoint-dir", str(self.directory)]), 0)
        latest.assert_not_called()
        kwargs = provision.call_args.kwargs
        self.assertEqual(kwargs["restore_snapshot_id"], SNAPSHOT)
        self.assertEqual((kwargs["image"], kwargs["disk"], kwargs["cpu"], kwargs["memory"]),
                         (IMAGE, "12Gi", "3", "6Gi"))
        with self.assertRaises(app.CheckpointError):
            app.checkpoint_restore(self.directory, "other-name")

    def test_restore_refuses_unfinished_stop_or_missing_manifest(self):
        self.source.stop.side_effect = TimeoutError()
        self.assertEqual(self.call(), 1)
        with self.assertRaises(app.CheckpointError):
            app.checkpoint_restore(self.directory, "example")
        self.source.status = "terminated"
        self.assertEqual(self.call(), 0)
        (self.directory / "manifest.json").unlink()
        with self.assertRaises(app.CheckpointError):
            app.checkpoint_restore(self.directory, "example")

    def test_late_ready_checkpoint_is_excluded_from_default_restore_and_prune(self):
        ordinary = types.SimpleNamespace(request_id="cwsa1|example|claude|123", status="ready", file_system_snapshot_id="ordinary")
        abandoned = types.SimpleNamespace(request_id="cwcp1|example|claude|abandoned", status="ready", file_system_snapshot_id="abandoned")
        self.api.delete_snapshot = Mock(return_value=operation(None))
        with patch.object(app, "session_snapshots", return_value=[abandoned, ordinary]), contextlib.redirect_stdout(self.stdout):
            self.assertIs(app.latest_ready_snapshot("example"), ordinary)
            self.assertEqual(app.main(["prune", "example", "--keep", "0"]), 0)
            self.api.delete_snapshot.assert_called_once_with("ordinary", missing_ok=True, auth=app.sandbox_auth())
            self.api.delete_snapshot.reset_mock()
            self.assertEqual(app.main(["prune", "example", "--keep", "0", "--include-checkpoints"]), 0)
            self.assertEqual(self.api.delete_snapshot.call_count, 2)

    def test_private_journal_rejects_symlinks_unsafe_permissions_and_concurrent_writer(self):
        with app.CheckpointJournal(self.directory) as journal:
            with self.assertRaises(BlockingIOError), app.CheckpointJournal(self.directory):
                pass
            target = self.root / "target"
            target.write_text("{}")
            (self.directory / "journal.json").symlink_to(target)
            with self.assertRaises(OSError):
                journal.read("journal.json")
            (self.directory / "journal.json").unlink()
            journal.write("journal.json", {})
            (self.directory / "journal.json").chmod(0o644)
            with self.assertRaises(app.CheckpointError):
                journal.read("journal.json")
        self.directory.chmod(0o755)
        with self.assertRaises(app.CheckpointError), app.CheckpointJournal(self.directory):
            pass


if __name__ == "__main__":
    unittest.main()
