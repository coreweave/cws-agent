"""Actual remote helper/tar with small local subprocesses, no cloud access."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from test_transfer import cli


class LocalProcess:
    def __init__(self, command, streaming=False, lost_ack=False):
        self.child = subprocess.Popen([sys.executable, *command[1:]],
                                      stdin=subprocess.PIPE if streaming else subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.stdin = types.SimpleNamespace(write=self.write, close=self.close)
        self.lost_ack = lost_ack

    async def write(self, data):
        await asyncio.to_thread(self.child.stdin.write, data)

    async def close(self):
        await asyncio.to_thread(self.child.stdin.close)

    def result(self, **kwargs):
        self.child.wait(timeout=10)
        with self.child.stdout as out, self.child.stderr as err:
            result = types.SimpleNamespace(stdout=out.read().decode(), stderr=err.read().decode(),
                                           returncode=self.child.returncode)
        if self.lost_ack:
            raise TimeoutError("lost response after successful remote operation")
        return result

    def __await__(self):
        return asyncio.to_thread(self.result).__await__()

    def cancel(self):
        self.child.kill()
        self.child.wait()


class LocalSandbox:
    def __init__(self):
        self.requests = []
        self.interrupt_index = None
        self.fail_extract = False
        self.lost_ack = None
        self.fail_put = False

    def exec(self, command, **kwargs):
        request = json.loads(command[-1])
        self.requests.append(request)
        action = request["action"]
        if action == "put":
            if request["index"] == self.interrupt_index:
                raise KeyboardInterrupt()
            if self.fail_put:
                raise TimeoutError("network unavailable")
        if action == "extract" and self.fail_extract:
            raise TimeoutError("connection lost before extraction")
        lost = self.lost_ack == action
        if lost:
            self.lost_ack = None
        return LocalProcess(command, streaming=kwargs.get("stdin", False), lost_ack=lost)


class ResumableUploadTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.original = os.urandom(12000)
        (self.source / "file").write_bytes(self.original)
        self.remote = self.root / "remote"
        self.project = self.remote / "project"
        self.staging = self.remote / ".cws-uploads"
        self.stack.enter_context(patch("pathlib.Path.home", return_value=self.root / "home"))
        self.stack.enter_context(patch.object(cli, "UPLOAD_CHUNK_SIZE", 4096))
        self.stack.enter_context(patch.object(cli, "UPLOAD_REMOTE_ROOT", str(self.staging)))
        self.stack.enter_context(patch.object(cli, "PROJECT_DIR", str(self.project)))
        # Avoid a host-wide filesystem sync on macOS; keep the rest of the exact
        # remote helper, including fsync, rename, flock, checksums, and tar.
        self.stack.enter_context(patch.object(cli, "UPLOAD_REMOTE", cli.UPLOAD_REMOTE.replace("os.sync()", "pass")))
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.errors = self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.stack.enter_context(patch.object(cli.time, "sleep"))
        self.sb = LocalSandbox()

    def sync(self, uid=None, clean=False, preserve_existing=False):
        cli.sync_local_dir(self.sb, str(self.source), include_git=True, extra_excludes=[], clean=clean,
                           resume_upload=uid, session_name="test", transfer_timeout=30,
                           preserve_existing=preserve_existing)

    def cache(self):
        folders = list(cli.upload_cache_root().iterdir())
        self.assertEqual(len(folders), 1)
        return folders[0]

    def puts(self):
        return [r["index"] for r in self.sb.requests if r["action"] == "put"]

    def pause(self):
        self.sb.interrupt_index = 1
        with self.assertRaisesRegex(cli.UploadPaused, "--resume-upload"):
            self.sync()
        self.sb.interrupt_index = None
        return self.cache()

    def test_resume_sends_only_missing_chunks_and_uses_immutable_archive(self):
        folder = self.pause()
        self.assertTrue((self.staging / folder.name / "0.chunk").exists())
        (self.source / "file").write_text("changed after initial packaging")
        self.sb.requests.clear()
        with patch.object(cli, "build_local_tar", side_effect=AssertionError("must not repackage")):
            self.sync(folder.name)
        self.assertNotIn(0, self.puts())
        self.assertEqual((self.project / "file").read_bytes(), self.original)
        self.assertFalse(folder.exists())
        self.assertFalse(list(self.staging.glob("*/*.chunk")))
        self.assertIn("Reusing", self.output.getvalue())

    def test_background_upload_preserves_remote_edits_and_does_not_follow_remote_links(self):
        self.project.mkdir(parents=True)
        (self.project / "file").write_text("agent work")
        (self.source / "new-file").write_text("new local data")
        (self.source / "linked-dir").mkdir()
        (self.source / "linked-dir/should-not-write").write_text("local")
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / "linked-dir").symlink_to(outside, target_is_directory=True)
        self.sync(preserve_existing=True)
        self.assertEqual((self.project / "file").read_text(), "agent work")
        self.assertEqual((self.project / "new-file").read_text(), "new local data")
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(list(self.staging.glob("*/unpacked")))

    def test_corrupt_committed_chunk_is_reuploaded_partial_file_is_discarded(self):
        folder = self.pause()
        (self.staging / folder.name / "0.chunk").write_bytes(b"bad")
        partial = self.staging / folder.name / ".part-interrupted"
        partial.write_bytes(b"not a checkpoint")
        self.sb.requests.clear()
        self.sync(folder.name)
        self.assertIn(0, self.puts())
        self.assertFalse(partial.exists())
        self.assertEqual((self.project / "file").read_bytes(), self.original)

    def test_lost_chunk_receipt_reuses_remote_checkpoint_without_resending(self):
        self.sb.lost_ack = "put"
        self.sync()
        self.assertEqual(self.puts().count(0), 1)
        self.assertEqual((self.project / "file").read_bytes(), self.original)

    def test_extraction_failure_retains_chunks_resume_uploads_zero_bytes(self):
        self.sb.fail_extract = True
        with self.assertRaises(cli.UploadPaused):
            self.sync()
        folder = self.cache()
        self.sb.fail_extract = False
        self.sb.requests.clear()
        self.sync(folder.name)
        self.assertEqual(self.puts(), [])
        self.assertEqual((self.project / "file").read_bytes(), self.original)

    def test_lost_extraction_response_does_not_reapply_or_delete_new_remote_edits(self):
        self.sb.lost_ack = "extract"
        with self.assertRaises(cli.UploadPaused):
            self.sync(clean=True)
        folder = self.cache()
        (self.project / "file").write_text("new remote work")
        self.sb.requests.clear()
        self.sync(folder.name, clean=True)
        self.assertEqual([r["action"] for r in self.sb.requests], ["status"])
        self.assertEqual((self.project / "file").read_text(), "new remote work")

    def test_retries_are_bounded_and_progress_does_not_claim_unsaved_bytes(self):
        self.sb.fail_put = True
        with self.assertRaises(cli.UploadPaused):
            self.sync()
        self.assertEqual(self.puts(), [0, 0, 0])
        self.assertIn("Uploading (verified):   0%", self.errors.getvalue())
        self.assertNotIn("Uploading (verified): 100%", self.errors.getvalue())

    def test_clean_requires_explicit_flag_on_resume_and_removes_hidden_stale_files(self):
        folder = self.pause()
        manifest = cli.upload_manifest(folder)
        manifest["clean"] = True
        # Make a fresh target for this explicitly changed test fixture.
        self.stack.enter_context(patch.object(cli, "UPLOAD_REMOTE_ROOT", str(self.remote / "clean-staging")))
        cli.telegram_save_json(folder / "manifest.json", manifest)
        with self.assertRaisesRegex(SystemExit, "--clean"):
            self.sync(folder.name)
        self.project.mkdir()
        (self.project / "..stale").write_text("old")
        self.sync(folder.name, clean=True)
        self.assertFalse((self.project / "..stale").exists())

    def test_changed_local_archive_is_rejected(self):
        folder = self.pause()
        with (folder / "archive.tar.gz").open("r+b") as stream:
            stream.seek(4096)
            stream.write(b"bad")
        self.sb.requests.clear()
        with self.assertRaisesRegex(cli.UploadPaused, "ValueError"):
            self.sync(folder.name)
        self.assertEqual(self.puts(), [])

    def test_cache_permissions_validation_locking_and_discard(self):
        folder = self.pause()
        for path in (folder, folder / "manifest.json", folder / "archive.tar.gz"):
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        with cli.upload_lock(folder):
            with self.assertRaisesRegex(SystemExit, "already in use"):
                self.sync(folder.name)
            with self.assertRaisesRegex(SystemExit, "already in use"):
                cli.main(["uploads", "--discard", folder.name])
        cli.main(["uploads"])
        self.assertIn(folder.name, self.output.getvalue())
        cli.main(["uploads", "--discard", folder.name])
        self.assertFalse(folder.exists())
        self.assertTrue((self.staging / folder.name / "0.chunk").exists())
        with self.assertRaisesRegex(SystemExit, "invalid upload ID"):
            cli.upload_folder("../../elsewhere")

    def test_symlinked_remote_staging_is_rejected_without_writing_target(self):
        self.remote.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        self.staging.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(cli.UploadPaused, "symlink"):
            self.sync()
        self.assertEqual(list(outside.iterdir()), [])

    def test_resume_filters_rejected_before_cloud_lookup(self):
        for flags in (["somewhere"], ["--no-git"], ["--exclude", "data"]):
            with patch.object(cli, "require_active") as lookup, self.assertRaises(SystemExit):
                cli.main(["sync", "test", "--resume-upload", "a" * 32, *flags])
            lookup.assert_not_called()

    def test_failed_launch_preserves_resumable_sandbox_but_not_failed_packaging(self):
        with patch.object(cli, "cmd_launch", side_effect=lambda args: args):
            args = cli.main(["launch", "--name", "test", "--local-dir", str(self.source),
                             "--detach", "--dangerously-skip-permissions"])
        for error, should_stop in ((cli.UploadPaused("resume me"), False), (ValueError("packaging failed"), True)):
            with patch.object(cli, "find_active", return_value=None), \
                    patch.object(cli, "build_env", return_value={}), \
                    patch.object(cli, "provision_session", return_value=self.sb), \
                    patch.object(cli, "sync_local_dir", side_effect=error), \
                    patch.object(cli, "stop_failed_sandbox") as stop, \
                    self.assertRaises(type(error)):
                cli.cmd_launch(args)
            self.assertEqual(stop.called, should_stop)
        self.assertIn("cws-agent connect test --dangerously-skip-permissions", self.errors.getvalue())

    def test_paused_launch_reconnect_preserves_explicit_permission_mode(self):
        for options in ([], ["--permission-mode", "accept-edits"], ["--permission-mode", "native"]):
            with self.subTest(options=options), \
                    patch.object(cli, "find_active", return_value=None), \
                    patch.object(cli, "build_env", return_value={}), \
                    patch.object(cli, "provision_session", return_value=self.sb), \
                    patch.object(cli, "sync_local_dir", side_effect=cli.UploadPaused("resume me")), \
                    contextlib.redirect_stderr(io.StringIO()) as output, \
                    self.assertRaises(cli.UploadPaused):
                cli.main(["launch", "test", "--local-dir", str(self.source), *options])
            expected = "Then: cws-agent connect test" + (" " + " ".join(options) if options else "")
            self.assertIn(expected + "\n", output.getvalue())

    def test_bad_cache_permissions_and_malformed_manifest_fail_cleanly(self):
        folder = self.pause()
        manifest = folder / "manifest.json"
        manifest.chmod(0o644)
        with self.assertRaisesRegex(SystemExit, "invalid or unreadable"):
            self.sync(folder.name)
        manifest.chmod(0o600)
        manifest.write_text("[]")
        with self.assertRaisesRegex(SystemExit, "invalid or unreadable"):
            self.sync(folder.name)

    def test_remote_project_symlink_is_rejected_before_extraction(self):
        self.remote.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        self.project.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(cli.UploadPaused, "symlink"):
            self.sync()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(list(self.staging.glob("*/*.chunk")))

    def test_cache_is_excluded_from_source_scan_and_interrupted_packaging_can_be_discarded(self):
        folder = self.pause()
        inventory = cli.scan_local_dir(str(self.root), include_git=True, extra_excludes=[])
        self.assertFalse(any(str(cli.upload_cache_root()) in path for path, _, _ in inventory.entries))
        partial = cli.upload_cache_root() / ("b" * 32)
        partial.mkdir(mode=0o700)
        (partial / "cws-sync-interrupted.tar.gz").write_bytes(b"incomplete archive")
        cli.main(["uploads", "--discard", partial.name])
        self.assertFalse(partial.exists())

    def test_corrupt_archive_extraction_retains_chunks_and_reports_tar_error(self):
        import hashlib
        folder = self.pause()
        broken = b"not a gzip archive"
        (folder / "archive.tar.gz").write_bytes(broken)
        manifest = cli.upload_manifest(folder)
        manifest["size"] = len(broken)
        manifest["chunks"] = [{"size": len(broken), "sha256": hashlib.sha256(broken).hexdigest()}]
        cli.telegram_save_json(folder / "manifest.json", manifest)
        self.stack.enter_context(patch.object(cli, "UPLOAD_REMOTE_ROOT", str(self.remote / "broken-staging")))
        with self.assertRaisesRegex(cli.UploadPaused, "tar exit"):
            self.sync(folder.name)
        self.assertTrue((self.remote / "broken-staging" / folder.name / "0.chunk").exists())

    def test_insufficient_remote_space_fails_before_upload(self):
        helper = cli.UPLOAD_REMOTE.replace("shutil.disk_usage(root).free", "0")
        with patch.object(cli, "UPLOAD_REMOTE", helper), self.assertRaisesRegex(cli.UploadPaused, "insufficient disk"):
            self.sync()
        self.assertEqual(self.puts(), [])

    def test_receiver_deadline_removes_incomplete_chunk_and_releases_lock(self):
        folder = self.pause()
        manifest = cli.upload_manifest(folder)
        proc = LocalProcess(cli.upload_command(manifest, "put", index=1, timeout=1), streaming=True)
        try:
            result = proc.result()
        finally:
            proc.child.stdin.close()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timed out", result.stderr)
        self.assertFalse(list((self.staging / folder.name).glob(".part-*")))
        self.sync(folder.name)
        self.assertEqual((self.project / "file").read_bytes(), self.original)

    def test_remote_operation_lock_blocks_concurrent_extraction_or_chunk_writes(self):
        import fcntl
        folder = self.pause()
        with (self.staging / ".lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.sb.requests.clear()
            with self.assertRaisesRegex(cli.UploadPaused, "BlockingIOError"):
                self.sync(folder.name)
            self.assertEqual(self.puts(), [])
        self.sync(folder.name)

    def test_smoke_script_runs_entire_interruption_resume_flow_with_local_processes(self):
        import importlib.util
        from test_sessions import load_cli
        path = Path(__file__).resolve().parents[1] / "smoke_upload.py"
        spec = importlib.util.spec_from_file_location("upload_smoke_test", path)
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        isolated = load_cli()
        isolated.UPLOAD_REMOTE = isolated.UPLOAD_REMOTE.replace("os.sync()", "pass")
        isolated.require_active = lambda name: types.SimpleNamespace(
            exec=lambda command, **kwargs: LocalProcess(command, streaming=kwargs.get("stdin", False)))
        with patch.object(smoke, "load_cli", return_value=isolated), \
                patch.object(smoke.importlib.metadata, "version", return_value="local-test"), \
                patch.object(sys, "argv", ["smoke_upload.py", "test"]):
            smoke.main()
        self.assertIn("PASS: interruption, saved-chunk reuse", self.output.getvalue())
        self.assertFalse(Path(isolated.UPLOAD_REMOTE_ROOT).parent.exists())


if __name__ == "__main__":
    unittest.main()
