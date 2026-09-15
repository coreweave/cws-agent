"""Long-upload deadlines, remote failures, and cancellation without network calls."""
import asyncio
import concurrent.futures
import contextlib
import io
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from test_transfer import cli


class FakeProcess:
    def __init__(self, *, failure=None, blocked=False, pause=0, exit_code=0):
        self.finished = asyncio.Event()
        self.failure, self.blocked, self.pause = failure, blocked, pause
        self.chunks = []
        self.cancelled = False
        self.cancelled_writes = 0
        self.result_value = types.SimpleNamespace(returncode=exit_code, stderr="tar failed" if exit_code else "")
        self.stdin = types.SimpleNamespace(write=self.write, close=self.close)

    def __await__(self):
        async def wait():
            await self.finished.wait()
            if self.failure:
                raise self.failure
            return self.result_value
        return wait().__await__()

    def write(self, chunk):
        async def queue():
            try:
                if self.failure:
                    self.finished.set()
                if self.failure or self.blocked:
                    await asyncio.Event().wait()
                await asyncio.sleep(self.pause)
                self.chunks.append(chunk)
            except asyncio.CancelledError:
                self.cancelled_writes += 1
                raise
        return queue()

    def close(self):
        async def eof():
            self.finished.set()
        return eof()

    def cancel(self):
        self.cancelled = True
        self.finished.set()


class UploadStreamingTests(unittest.TestCase):
    def run_upload(self, proc, data=b"abc", **kwargs):
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.output = output
            with cli.TransferProgress("Uploading", len(data)) as progress:
                return asyncio.run(cli.stream_project_upload(proc, io.BytesIO(data), progress,
                                                              timeout_seconds=900, **kwargs))

    def test_size_budget_covers_large_many_file_upload(self):
        self.assertEqual(cli.project_upload_timeout(1, 1), 900)
        timeout = cli.project_upload_timeout(10 << 30, 546291)
        self.assertEqual(timeout, 600 + 10240 + 5463)
        self.assertGreater(timeout, 4 * 3600)

    def test_success_uses_small_chunks_and_waits_for_remote_exit(self):
        proc = FakeProcess()
        data = b"x" * ((2 << 20) + 3)
        self.run_upload(proc, data)
        self.assertEqual(b"".join(proc.chunks), data)
        self.assertLessEqual(max(map(len, proc.chunks)), 64 << 10)
        self.assertFalse(proc.cancelled)
        self.assertIn("100%", self.output.getvalue())

    def test_remote_timeout_cancels_blocked_write_immediately(self):
        proc = FakeProcess(failure=TimeoutError("remote deadline"))
        with self.assertRaisesRegex(TimeoutError, "remote deadline"):
            self.run_upload(proc)
        self.assertEqual(proc.cancelled_writes, 1)
        self.assertNotIn("100%", self.output.getvalue())

    def test_nonzero_remote_exit_is_reported_not_success(self):
        with self.assertRaisesRegex(cli.ProjectUploadError, "tar failed"):
            self.run_upload(FakeProcess(exit_code=2))
        self.assertNotIn("100%", self.output.getvalue())

    def test_idle_timeout_cancels_writer_and_process(self):
        proc = FakeProcess(blocked=True)
        with self.assertRaisesRegex(cli.ProjectUploadError, "upload stalled"):
            self.run_upload(proc, idle_timeout=0)
        self.assertEqual(proc.cancelled_writes, 1)
        self.assertTrue(proc.cancelled)

    def test_caller_cancellation_leaves_no_write_task(self):
        proc = FakeProcess(blocked=True)

        async def cancel():
            with cli.TransferProgress("Uploading", 3) as progress:
                task = asyncio.create_task(cli.stream_project_upload(proc, io.BytesIO(b"abc"), progress,
                                                                      timeout_seconds=900))
                await asyncio.sleep(0.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse([task for task in asyncio.all_tasks() if task is not asyncio.current_task()])

        with contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(cancel())
        self.assertEqual(proc.cancelled_writes, 1)
        self.assertTrue(proc.cancelled)

    def test_backpressure_wait_does_not_resend_chunk(self):
        proc = FakeProcess(pause=0.02)
        self.run_upload(proc)
        self.assertEqual(proc.chunks, [b"abc"])

    def test_cancellation_reaches_sdk_style_concurrent_future(self):
        future = concurrent.futures.Future()
        proc = FakeProcess(failure=TimeoutError("remote deadline"))

        class Operation:
            def __await__(self):
                return asyncio.wrap_future(future).__await__()

        def write(chunk):
            proc.finished.set()
            return Operation()

        proc.stdin.write = write
        with self.assertRaises(TimeoutError):
            self.run_upload(proc)
        self.assertTrue(future.cancelled())

    def test_real_bounded_queue_failure_cancels_pending_put(self):
        """Reproduce SDK queue saturation without uploading gigabytes or sleeping."""
        async def reproduce():
            queue = asyncio.Queue(maxsize=2)
            blocked = asyncio.Event()
            proc = FakeProcess(failure=TimeoutError("remote deadline"))

            async def write(chunk):
                if queue.full():
                    blocked.set()
                try:
                    await queue.put(chunk)
                except asyncio.CancelledError:
                    proc.cancelled_writes += 1
                    raise

            proc.stdin.write = write

            async def fail_receiver():
                await blocked.wait()
                proc.finished.set()

            receiver = asyncio.create_task(fail_receiver())
            with cli.TransferProgress("Uploading", 4 * (64 << 10)) as progress:
                with self.assertRaisesRegex(TimeoutError, "remote deadline"):
                    await asyncio.wait_for(cli.stream_project_upload(
                        proc, io.BytesIO(b"x" * (4 * (64 << 10))), progress,
                        timeout_seconds=900), timeout=2)
            await receiver
            self.assertTrue(blocked.is_set())
            self.assertEqual(queue.qsize(), 2)
            self.assertEqual(proc.cancelled_writes, 1)
            self.assertFalse([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])

        with contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(reproduce())

    def test_sync_passes_deadline_and_retains_failed_archive_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive.tar.gz"
            archive.write_bytes(b"test")
            archive.chmod(0o600)
            with patch.object(cli, "build_local_tar", return_value=(str(archive), 1)), \
                    patch("pathlib.Path.home", return_value=Path(directory) / "home"), \
                    patch.object(cli, "transfer_cached_upload", side_effect=TimeoutError("remote deadline")) as transfer, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                sb = object()
                with self.assertRaisesRegex(cli.UploadPaused, "upload paused") as error:
                    cli.sync_local_dir(sb, directory, include_git=True, extra_excludes=[], clean=False,
                                       transfer_timeout=14400)
                self.assertNotIn("Traceback", str(error.exception))
                self.assertEqual(transfer.call_args.args[-1], 14400)
                self.assertEqual((transfer.call_args.args[1] / "archive.tar.gz").read_bytes(), b"test")

    def test_timeout_flag_parsed_before_cloud_access(self):
        for argv, handler in ((["launch", "--name", "test"], "cmd_launch"), (["sync", "test"], "cmd_sync")):
            with patch.object(cli, handler, side_effect=lambda args: args):
                args = cli.main([*argv, "--transfer-timeout", "4h"])
                self.assertEqual(args.transfer_timeout, 14400)
            with patch.object(cli, handler) as command, self.assertRaises(SystemExit):
                cli.main([*argv, "--transfer-timeout", "invalid"])
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
