"""Transfer byte accounting and failures, without cloud resources."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

from test_transfer import cli


def result(code=0, stdout=""):
    return types.SimpleNamespace(returncode=code, stdout=stdout, stderr="")


def operation(value=None):
    return types.SimpleNamespace(result=lambda **kw: value)


class TransferProgressTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.output = self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))

    def test_redirected_progress_reports_total_without_escapes(self):
        with cli.TransferProgress("Uploading", 4096) as progress:
            progress.advance(2048)
            progress.render()
            progress.advance(2048)
        output = self.output.getvalue()
        self.assertIn("50% 2.0 KiB / 4.0 KiB", output)
        self.assertIn("100% 4.0 KiB / 4.0 KiB", output)
        self.assertNotIn("\033", output)
        self.assertNotIn("\r", output)

    def test_resumed_progress_does_not_count_saved_bytes_as_new_transfer_speed(self):
        with cli.TransferProgress("Uploading", 4096, initial=2048) as progress:
            self.assertEqual(progress.done, 2048)
            self.assertIn("50% 2.0 KiB / 4.0 KiB | 0 B/s", self.output.getvalue())
            progress.advance(2048)
        self.assertIn("100% 4.0 KiB / 4.0 KiB", self.output.getvalue())

    def test_terminal_bar_and_narrow_terminal(self):
        with patch.object(self.output, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM": "xterm"}), \
                patch.object(cli.shutil, "get_terminal_size", return_value=os.terminal_size((100, 30))):
            with cli.TransferProgress("Uploading", 100) as progress:
                progress.advance(50)
                progress.render()
                progress.advance(50)
        self.assertIn("[##########----------]", self.output.getvalue())
        self.output.seek(0)
        self.output.truncate()
        with patch.object(self.output, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM": "xterm"}), \
                patch.object(cli.shutil, "get_terminal_size", return_value=os.terminal_size((30, 30))):
            with cli.TransferProgress("Uploading", 0) as progress:
                self.assertLessEqual(len(progress.spinner.text.plain), 27)

    def test_live_transfer_refreshes_without_new_bytes_and_stops_on_error(self):
        stdout = io.StringIO()
        with patch.object(self.output, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM": "xterm"}), \
                contextlib.redirect_stdout(stdout), self.assertRaises(RuntimeError):
            with cli.TransferProgress("Uploading", 100) as progress:
                self.assertTrue(progress.live.is_started)
                self.assertTrue(progress.live.auto_refresh)
                self.assertEqual(progress.done, 0)
                print("transfer stdout")
                raise RuntimeError("connection failed")
        self.assertFalse(progress.live.is_started)
        self.assertEqual(stdout.getvalue(), "transfer stdout\n")
        self.assertNotIn("transfer stdout", self.output.getvalue())
        self.assertIn("failed", self.output.getvalue())
        self.assertNotIn("100%", self.output.getvalue())

    def test_dumb_terminal_transfer_has_no_control_sequences(self):
        with patch.object(self.output, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM": "dumb"}):
            with cli.TransferProgress("Uploading", 1) as progress:
                progress.advance(1)
        self.assertNotIn("\033", self.output.getvalue())

    def test_failure_and_interrupt_never_claim_success_even_after_all_bytes(self):
        for error in (RuntimeError("failed"), KeyboardInterrupt()):
            self.output.seek(0)
            self.output.truncate()
            with self.assertRaises(type(error)):
                with cli.TransferProgress("Uploading", 1) as progress:
                    progress.advance(1)
                    raise error
            self.assertNotIn("100%", self.output.getvalue())
            self.assertNotIn("| done", self.output.getvalue())

    def test_archive_reports_selected_source_bytes_and_keeps_symlinks(self):
        (self.root / "file").write_bytes(b"abc")
        (self.root / "empty").mkdir()
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules/ignored").write_bytes(b"ignored")
        (self.root / "link").symlink_to("file")
        os.link(self.root / "file", self.root / "hardlink")
        archive, count = cli.build_local_tar(str(self.root), include_git=True, extra_excludes=[])
        self.addCleanup(os.unlink, archive)
        self.assertEqual(count, 2)
        self.assertIn("Selected 2 files, 6 B", self.output.getvalue())
        self.assertIn("Packaging: 100% 6 B / 6 B", self.output.getvalue())
        with tarfile.open(archive) as tar:
            self.assertTrue(tar.getmember("./link").issym())
            self.assertIn("./empty", tar.getnames())
            self.assertNotIn("./node_modules", tar.getnames())
            self.assertEqual(tar.extractfile("./file").read(), b"abc")

    def test_partial_archive_removed_on_packaging_interrupt(self):
        real_mkstemp = tempfile.mkstemp
        created = []

        def temporary(**kw):
            fd, path = real_mkstemp(**kw)
            created.append(path)
            return fd, path

        with patch("tempfile.mkstemp", side_effect=temporary), \
                patch("tarfile.TarFile.addfile", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            cli.build_local_tar(str(self.root), include_git=True, extra_excludes=[])
        self.assertTrue(created)
        self.assertTrue(all(not Path(path).exists() for path in created))
        self.assertIn("interrupted", self.output.getvalue())

    def archive_after_scan_change(self, change):
        original = tempfile.mkstemp

        def create(**kwargs):
            change()
            return original(**kwargs)

        with patch("tempfile.mkstemp", side_effect=create):
            archive, count = cli.build_local_tar(str(self.root), include_git=True, extra_excludes=[])
        self.addCleanup(os.unlink, archive)
        return archive, count

    def test_directory_timestamp_changes_and_new_files_do_not_abort(self):
        (self.root / "existing").write_bytes(b"old")
        archive, count = self.archive_after_scan_change(lambda: (self.root / "new").write_bytes(b"new"))
        self.assertEqual(count, 1)
        with tarfile.open(archive) as tar:
            self.assertEqual(tar.extractfile("./existing").read(), b"old")
            self.assertNotIn("./new", tar.getnames())

    def test_deleted_file_and_directory_after_scan_are_skipped(self):
        (self.root / "gone").mkdir()
        (self.root / "gone/file").write_bytes(b"gone")
        (self.root / "kept").write_bytes(b"kept")

        def remove():
            (self.root / "gone/file").unlink()
            (self.root / "gone").rmdir()

        archive, count = self.archive_after_scan_change(remove)
        self.assertEqual(count, 1)
        with tarfile.open(archive) as tar:
            self.assertEqual(tar.extractfile("./kept").read(), b"kept")
            self.assertNotIn("./gone/file", tar.getnames())
        self.assertIn("Warning: skipped", self.output.getvalue())

    def test_edited_and_atomically_replaced_files_include_current_contents(self):
        file = self.root / "file"
        file.write_bytes(b"old")

        def replace():
            replacement = self.root / "replacement"
            replacement.write_bytes(b"new longer contents")
            replacement.replace(file)

        archive, count = self.archive_after_scan_change(replace)
        self.assertEqual(count, 1)
        with tarfile.open(archive) as tar:
            self.assertEqual(tar.extractfile("./file").read(), b"new longer contents")
        self.assertIn("Captured updated contents", self.output.getvalue())
        self.assertIn("100% 19 B / 19 B", self.output.getvalue())

    def test_shrink_or_append_while_reading_retries_without_corrupting_archive(self):
        file = self.root / "file"
        original_fstat = os.fstat
        for new in (b"x", b"a much longer file"):
            file.write_bytes(b"original")
            calls = 0

            def fstat(fd):
                nonlocal calls
                calls += 1
                if calls == 2:  # before the stability check, after capture
                    file.write_bytes(new)
                return original_fstat(fd)

            with patch("os.fstat", side_effect=fstat):
                archive, count = cli.build_local_tar(str(self.root), include_git=True, extra_excludes=[])
            self.addCleanup(os.unlink, archive)
            self.assertEqual(count, 1)
            with tarfile.open(archive) as tar:
                self.assertEqual(tar.extractfile("./file").read(), new)

    def test_continuously_changing_file_is_skipped_after_bounded_retry(self):
        file = self.root / "file"
        file.write_bytes(b"original")
        original_fstat = os.fstat
        calls = 0

        def fstat(fd):
            nonlocal calls
            calls += 1
            if calls in (2, 4):
                file.write_bytes(b"changed" * calls)
            return original_fstat(fd)

        with patch("os.fstat", side_effect=fstat):
            archive, count = cli.build_local_tar(str(self.root), include_git=True, extra_excludes=[])
        self.addCleanup(os.unlink, archive)
        self.assertEqual(calls, 4)
        self.assertEqual(count, 0)
        with tarfile.open(archive) as tar:
            self.assertNotIn("./file", tar.getnames())
        self.assertIn("Warning: skipped 1", self.output.getvalue())

    def test_file_truncated_before_first_read_is_retried_not_padded(self):
        file = self.root / "file"
        file.write_bytes(b"original long file")
        original_fdopen = os.fdopen
        truncated = False

        class Reader:
            def __init__(self, stream):
                self.stream = stream

            def __getattr__(self, attr):
                return getattr(self.stream, attr)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def read(self, size):
                nonlocal truncated
                if not truncated:
                    file.write_bytes(b"x")
                    truncated = True
                return self.stream.read(size)

        with patch("os.fdopen", side_effect=lambda *a, **kw: Reader(original_fdopen(*a, **kw))):
            archive, count = cli.build_local_tar(str(self.root), include_git=True, extra_excludes=[])
        self.addCleanup(os.unlink, archive)
        self.assertEqual(count, 1)
        with tarfile.open(archive) as tar:
            self.assertEqual(tar.extractfile("./file").read(), b"x")

    def test_symlink_replacement_is_not_followed(self):
        file = self.root / "file"
        file.write_bytes(b"original")
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret"
            secret.write_bytes(b"do not upload")

            def replace():
                file.unlink()
                file.symlink_to(secret)

            archive, count = self.archive_after_scan_change(replace)
        self.assertEqual(count, 0)
        with tarfile.open(archive) as tar:
            self.assertNotIn("./file", tar.getnames())

    def test_disappearing_file_during_scan_does_not_abort(self):
        file = self.root / "file"
        file.write_bytes(b"gone")
        original_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            if str(path) == str(file):
                file.unlink()
            return original_lstat(path, *args, **kwargs)

        with patch("os.lstat", side_effect=lstat):
            archive, count = cli.build_local_tar(str(self.root), include_git=True, extra_excludes=[])
        self.addCleanup(os.unlink, archive)
        self.assertEqual(count, 0)
        self.assertIn("Warning: skipped", self.output.getvalue())

    def test_permission_errors_still_fail(self):
        file = self.root / "file"
        file.write_bytes(b"private")
        original_open = os.open

        def opening(path, *args, **kwargs):
            if str(path) == str(file):
                raise PermissionError("permission denied")
            return original_open(path, *args, **kwargs)

        with patch("os.open", side_effect=opening), self.assertRaises(PermissionError):
            cli.build_local_tar(str(self.root), include_git=True, extra_excludes=[])
        self.assertIn("failed", self.output.getvalue())

    def test_history_upload_is_chunked_and_checked(self):
        payload = b"x" * ((2 << 20) + 3)
        chunks = []
        proc = types.SimpleNamespace(
            stdin=types.SimpleNamespace(write=lambda chunk: chunks.append(chunk) or operation(),
                                        close=lambda: operation()),
            result=lambda **kw: result())
        sb = types.SimpleNamespace(exec=lambda *a, **kw: proc)
        cli.upload_history_payload(sb, "/tmp/bundle.json", payload)
        self.assertEqual(list(map(len, chunks)), [1 << 20, 1 << 20, 3])
        self.assertEqual(b"".join(chunks), payload)
        self.assertIn("100%", self.output.getvalue())

    def test_history_upload_failure_not_success(self):
        proc = types.SimpleNamespace(
            stdin=types.SimpleNamespace(write=lambda chunk: operation(), close=lambda: operation()),
            result=lambda **kw: result(1))
        with self.assertRaisesRegex(ValueError, "upload failed"):
            cli.upload_history_payload(types.SimpleNamespace(exec=lambda *a, **kw: proc), "/tmp/x", b"abc")
        self.assertNotIn("100%", self.output.getvalue())

    def download(self, chunks, expected, code=0):
        reader = io.StringIO("".join(chunks))
        # Exercise chunk boundaries, not StringIO's line iteration.
        class Stream:
            def __iter__(self):
                return iter(chunks)

            def close(self):
                reader.close()
        proc = types.SimpleNamespace(stdout=Stream(), result=lambda **kw: result(code))
        sb = types.SimpleNamespace(exec=lambda *a, **kw: proc)
        try:
            with patch.object(cli, "exec_retry", return_value=result(stdout=str(expected))):
                return cli.download_history_payload(sb, "/tmp/bundle.json")
        finally:
            self.assertTrue(reader.closed)

    def test_download_progress_tracks_received_bytes_and_no_content_leaks(self):
        self.assertEqual(self.download(['{"secret":', '"hidden"}'], 19), b'{"secret":"hidden"}')
        self.assertIn("100% 19 B / 19 B", self.output.getvalue())
        self.assertNotIn("hidden", self.output.getvalue())

    def test_download_rejects_short_long_or_failed_transfers(self):
        for chunks, expected, code in [(["a"], 2, 0), (["abc"], 2, 0), (["a"], 1, 1)]:
            with self.subTest(chunks=chunks, expected=expected, code=code):
                with self.assertRaises(ValueError):
                    self.download(chunks, expected, code)
        self.assertNotIn("100%", self.output.getvalue())

    def test_download_size_limit_checked_before_read(self):
        with patch.object(cli, "exec_retry", return_value=result(stdout=str((46 << 20) + 1))):
            with self.assertRaisesRegex(ValueError, "limit"):
                cli.download_history_payload(object(), "/tmp/bundle.json")

    def test_download_helper_runs_in_clean_process(self):
        payload = b'{"test":"\\ud83d\\ude00"}'
        path = self.root / "bundle.json"
        path.write_bytes(payload)

        def execute(command, **kw):
            completed = subprocess.run([sys.executable, *command[1:]], capture_output=True, check=True)
            return types.SimpleNamespace(stdout=io.StringIO(completed.stdout.decode("ascii")),
                                         result=lambda **kw: result())

        with patch.object(cli, "exec_retry", return_value=result(stdout=str(len(payload)))):
            self.assertEqual(cli.download_history_payload(types.SimpleNamespace(exec=execute), str(path)), payload)


if __name__ == "__main__":
    unittest.main()
