import base64
import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from test_terminal import agent


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


class ImagePasteTests(unittest.TestCase):
    def test_native_resume_command_recognition(self):
        for command in ("exec claude", "exec claude --resume abc --permission-mode acceptEdits",
                        "cd '/workspace/project with spaces' && exec claude --resume abc"):
            self.assertTrue(agent.command_uses_claude(command), command)
        for command in ("exec codex", "exec bash", "echo claude", "cd /tmp && exec devin", "exec 'broken"):
            self.assertFalse(agent.command_uses_claude(command), command)

    def test_tmux_attach_uses_per_session_agent_and_legacy_fallback(self):
        for name in ("claude", "codex", "devin", ""):
            with self.subTest(agent=name), patch.object(agent, "exec_retry", return_value=Mock(returncode=0, stdout=name)), patch.object(agent, "active_harness", return_value=agent.HARNESSES["claude"]) as fallback, patch.object(agent, "pty_attach", return_value=0) as attach:
                self.assertEqual(agent.session_attach(Mock(), "worker-1"), 0)
                self.assertEqual(attach.call_args.kwargs["image_paste"], name in ("", "claude"))
                self.assertEqual(fallback.call_count, 0 if name else 1)

    def test_tmux_attach_rejects_invalid_metadata(self):
        with patch.object(agent, "exec_retry", return_value=Mock(returncode=0, stdout="unknown-agent")), patch.object(agent, "pty_attach") as attach:
            with self.assertRaisesRegex(SystemExit, "unsupported agent"):
                agent.session_attach(Mock(), "worker-1")
            attach.assert_not_called()
        with patch.object(agent, "exec_retry") as read:
            with self.assertRaisesRegex(SystemExit, "invalid session"):
                agent.session_attach(Mock(), "../../other")
            read.assert_not_called()

    def test_ctrl_v_uploads_image_and_inserts_bracketed_path(self):
        bridge = agent.ImagePasteInput(Mock(), Mock())
        with patch.object(agent, "clipboard_image", return_value=PNG), patch.object(agent, "upload_clipboard_image", return_value="/workspace/home/.cws-agent/images/test.png") as upload:
            output = bridge.feed(b"look at \x16 please")
        upload.assert_called_once_with(bridge.sandbox, PNG, stopped=None)
        self.assertEqual(output, b"look at \x1b[200~/workspace/home/.cws-agent/images/test.png\x1b[201~ please")

    def test_split_bracketed_paste_never_reads_clipboard(self):
        bridge = agent.ImagePasteInput(Mock(), Mock())
        chunks = [b"\x1b[2", b"00", b"~text\x16", b"\x1b[201", b"~"]
        with patch.object(agent, "clipboard_image") as read:
            output = b"".join(bridge.feed(chunk) for chunk in chunks)
        self.assertEqual(output, b"".join(chunks))
        read.assert_not_called()

    def test_missing_clipboard_and_failed_upload_preserve_keypress(self):
        warn = Mock()
        bridge = agent.ImagePasteInput(Mock(), warn)
        with patch.object(agent, "clipboard_image", return_value=None):
            self.assertEqual(bridge.feed(b"\x16"), b"\x16")
        with patch.object(agent, "clipboard_image", return_value=PNG), patch.object(agent, "upload_clipboard_image", side_effect=RuntimeError("offline")):
            self.assertEqual(bridge.feed(b"\x16"), b"\x16")
        warn.assert_called_once_with("image paste failed: offline")

    def test_escape_history_and_binary_input_preserved(self):
        bridge = agent.ImagePasteInput(Mock(), Mock())
        self.assertEqual(bridge.feed(b"\x1b"), b"")
        self.assertEqual(bridge.flush(), b"\x1b")
        self.assertEqual(bridge.feed(b"\x1b[A\xff\x03"), b"\x1b[A\xff\x03")

    def test_negotiated_ctrl_v_encodings_with_fragmented_input(self):
        for key in agent.ImagePasteInput.CTRL_V:
            with self.subTest(key=key):
                bridge = agent.ImagePasteInput(Mock(), Mock())
                with patch.object(agent, "clipboard_image", return_value=PNG) as read, patch.object(agent, "upload_clipboard_image", return_value="/image.png"):
                    output = b"".join(bridge.feed(bytes([byte])) for byte in key)
                self.assertEqual(output, b"\x1b[200~/image.png\x1b[201~")
                read.assert_called_once()

    def test_key_release_and_negotiated_keys_in_pasted_text_do_not_read_clipboard(self):
        bridge = agent.ImagePasteInput(Mock(), Mock())
        data = b"\x1b[118;5:3u\x1b[200~\x1b[118;5u\x1b[27;5;118~\x1b[201~"
        with patch.object(agent, "clipboard_image") as read:
            output = b"".join(bridge.feed(bytes([byte])) for byte in data)
        self.assertEqual(output, data)
        read.assert_not_called()

    def test_sdk_operation_timeout_does_not_cancel_future(self):
        from concurrent.futures import Future
        from cwsandbox._types import OperationRef
        future = Future()
        operation = OperationRef(future)
        with self.assertRaises(TimeoutError):
            operation.result(timeout=0)
        self.assertFalse(future.cancelled())
        future.set_result("done")
        self.assertEqual(operation.result(timeout=0), "done")

    def test_upload_stores_bytes_only_after_directory_success(self):
        sandbox = Mock()
        sandbox.exec.return_value.result.return_value = Mock(returncode=0)
        path = agent.upload_clipboard_image(sandbox, PNG)
        self.assertRegex(path, r"^/workspace/home/\.cws-agent/images/[a-f0-9]{32}\.png$")
        sandbox.write_file.assert_called_once_with(path, PNG, timeout_seconds=30)
        sandbox.reset_mock()
        sandbox.exec.return_value.result.return_value = Mock(returncode=1)
        with self.assertRaisesRegex(RuntimeError, "directory"):
            agent.upload_clipboard_image(sandbox, PNG)
        sandbox.write_file.assert_not_called()

    def test_upload_stops_waiting_when_attach_ends(self):
        stopped = threading.Event()
        operation = Mock()
        def pending(**kwargs):
            stopped.set()
            raise TimeoutError()
        operation.result.side_effect = pending
        with self.assertRaisesRegex(RuntimeError, "attach ended"):
            agent.wait_image_operation(operation, stopped, 30)
        operation.cancel.assert_called_once()
        sandbox = Mock()
        with self.assertRaisesRegex(RuntimeError, "attach ended"):
            agent.upload_clipboard_image(sandbox, PNG, stopped=stopped)
        sandbox.exec.assert_not_called()

    def test_linux_clipboard_backend_and_size_limit(self):
        def provider(command, *, stdout, **kwargs):
            self.assertEqual(command, ["wl-paste", "--no-newline", "--type", "image/png"])
            stdout.write(PNG)
            return Mock(returncode=0)
        with patch.object(sys, "platform", "linux"), patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}), patch("shutil.which", return_value="/bin/wl-paste"), patch("subprocess.run", side_effect=provider):
            self.assertEqual(agent.clipboard_image(), PNG)
            with patch.object(agent, "MAX_CLIPBOARD_IMAGE", 10):
                with self.assertRaisesRegex(ValueError, "10 MiB"):
                    agent.clipboard_image()

    @unittest.skipUnless(sys.platform == "darwin", "macOS AppKit integration")
    def test_macos_reads_private_test_pasteboard_not_system_clipboard(self):
        encoded = base64.b64encode(PNG).decode()
        script = agent.MAC_CLIPBOARD_IMAGE.replace("$.NSPasteboard.generalPasteboard", "$.NSPasteboard.pasteboardWithUniqueName")
        script = script.replace("var data =", f"pasteboard.setDataForType($.NSData.alloc.initWithBase64EncodedStringOptions('{encoded}', 0), $.NSPasteboardTypePNG);\nvar data =", 1)
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(base64.b64decode(result.stdout.strip()), PNG)
