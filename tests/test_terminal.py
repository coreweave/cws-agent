"""Offline PTY regressions: python -m unittest discover -s tests."""
import importlib.machinery
import importlib.util
import io
import os
import pty
import signal
import subprocess
import sys
import termios
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


loader = importlib.machinery.SourceFileLoader("cws_agent_terminal", str(Path(__file__).resolve().parents[1] / "cws-agent"))
spec = importlib.util.spec_from_loader(loader.name, loader)
agent = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = agent
loader.exec_module(agent)


class Immediate:
    def result(self, **kwargs):
        return None


class TerminalFile:
    def __init__(self, fd):
        self.fd = fd
        self.buffer = io.BytesIO()

    def fileno(self):
        return self.fd

    def isatty(self):
        return True


class Output:
    def __init__(self, chunks, close):
        self.chunks = chunks
        self.close = close

    def __iter__(self):
        return iter(self.chunks)


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.master, self.slave = pty.openpty()
        termios.tcsetwinsize(self.slave, (37, 123))

    def tearDown(self):
        os.close(self.master)
        os.close(self.slave)

    def test_geometry_ignores_stale_environment(self):
        with patch.dict(os.environ, {"COLUMNS": "80", "LINES": "24"}):
            self.assertEqual(agent.terminal_size(self.slave), (123, 37))
            termios.tcsetwinsize(self.slave, (50, 170))
            self.assertEqual(agent.terminal_size(self.slave), (170, 50))

    def test_unknown_terminfo_falls_back_and_unsets_dimensions(self):
        with patch.dict(os.environ, {"TERM": "xterm-nonexistent-cws-test", "COLUMNS": "80", "LINES": "24"}):
            result = subprocess.run(["sh", "-c", agent.terminal_env() + 'printf "%s:%s:%s" "$TERM" "${COLUMNS-unset}" "${LINES-unset}"'], capture_output=True, check=True)
        self.assertEqual(result.stdout, b"xterm-256color:unset:unset")

    def test_terminal_metadata_is_shell_quoted(self):
        with patch.dict(os.environ, {"TERM_PROGRAM": "Ghostty'; exit 42; #"}):
            result = subprocess.run(["sh", "-c", agent.terminal_env() + 'printf %s "$TERM_PROGRAM"'], capture_output=True, check=True)
        self.assertEqual(result.stdout, b"Ghostty'; exit 42; #")

    def test_raw_bytes_resize_and_cleanup(self):
        received = []
        resized = threading.Event()
        resized_again = threading.Event()
        forwarded = threading.Event()
        chunks = [b"\x1b[2J\x1b[H\xf0\x9f", b"\x91\xbb\r\n\xff"]
        outer = self

        class Session:
            stdin = None
            returncode = None

            def __init__(self):
                self.stdin = self
                self.cancelled = False
                self.output = Output(self.chunks(), self.close_output)

            def write(self, data):
                received.append(data)
                forwarded.set()
                return Immediate()

            def resize(self, width, height):
                if resized.is_set():
                    outer.assertEqual((width, height), (170, 50))
                    resized_again.set()
                else:
                    outer.assertEqual((width, height), (123, 37))
                    resized.set()

            def chunks(self):
                outer.assertTrue(resized.wait(2))
                os.write(outer.master, b"\x1b[A\x03\xff")
                outer.assertTrue(forwarded.wait(2))
                termios.tcsetwinsize(outer.slave, (50, 170))
                os.kill(os.getpid(), signal.SIGWINCH)
                outer.assertTrue(resized_again.wait(2))
                yield from chunks

            def wait(self, **kwargs):
                return 7

            def close_output(self):
                self.cancelled = True

        session = Session()
        class Sandbox:
            def shell(self, command, **kwargs):
                outer.assertEqual(kwargs, {"width": 123, "height": 37})
                outer.assertIn("export TERM=", command[2])
                return session

        before = termios.tcgetattr(self.slave)
        handler = signal.getsignal(signal.SIGWINCH)
        stdout = TerminalFile(self.slave)
        with patch.object(sys, "stdin", TerminalFile(self.slave)), patch.object(sys, "stdout", stdout):
            self.assertEqual(agent.pty_attach(Sandbox(), "exec bash"), 7)
        self.assertEqual(stdout.buffer.getvalue(), b"".join(chunks))
        self.assertEqual(b"".join(received), b"\x1b[A\x03\xff")
        after = termios.tcgetattr(self.slave)
        # macOS sets the kernel-maintained PENDIN bit when leaving raw mode.
        after[3] &= ~getattr(termios, "PENDIN", 0)
        before[3] &= ~getattr(termios, "PENDIN", 0)
        self.assertEqual(after, before)
        self.assertEqual(signal.getsignal(signal.SIGWINCH), handler)
        self.assertTrue(session.cancelled)
        self.assertFalse(any(t.name.endswith("(forward_stdin)") for t in threading.enumerate()))

    def test_resize_also_sets_remote_pty_size(self):
        # The service accepts the resize frame but the sandbox PTY keeps its
        # launch geometry; the attach must also run stty on the shell's fd 0.
        for stty_fails in (False, True):
            with self.subTest(stty_fails=stty_fails):
                termios.tcsetwinsize(self.slave, (37, 123))
                execs = []
                resized = threading.Event()
                stty_done = threading.Event()
                outer = self

                class Result:
                    def __init__(self, stdout):
                        self.stdout = stdout

                    def result(self, **kwargs):
                        return self

                class Sandbox:
                    def shell(self, command, **kwargs):
                        outer.assertRegex(command[2], r"echo \$\$ > /tmp/cws-attach-[0-9a-f]{32}\.pid; ")
                        return session

                    def exec(self, command, **kwargs):
                        execs.append(command[2])
                        if command[2].startswith("cat /tmp/cws-attach-"):
                            return Result("4242\n")
                        if "rows 50 cols 170" in command[2]:
                            stty_done.set()
                        if stty_fails:
                            raise RuntimeError("exec failed")
                        return Result("")

                class Session:
                    stdin = None
                    returncode = None

                    def __init__(self):
                        self.stdin = self
                        self.resizes = []
                        self.output = Output(self.chunks(), lambda: None)

                    def write(self, data):
                        return Immediate()

                    def resize(self, width, height):
                        self.resizes.append((width, height))
                        resized.set()

                    def chunks(self):
                        outer.assertTrue(resized.wait(2))
                        termios.tcsetwinsize(outer.slave, (50, 170))
                        os.kill(os.getpid(), signal.SIGWINCH)
                        outer.assertTrue(stty_done.wait(5))
                        yield b"ok"

                    def wait(self, **kwargs):
                        return 3

                session = Session()
                with patch.object(sys, "stdin", TerminalFile(self.slave)), patch.object(sys, "stdout", TerminalFile(self.slave)):
                    self.assertEqual(agent.pty_attach(Sandbox(), "exec bash"), 3)
                self.assertIn((170, 50), session.resizes)
                self.assertRegex(execs[0], r"^cat /tmp/cws-attach-[0-9a-f]{32}\.pid && rm -f /tmp/cws-attach-")
                self.assertIn("stty -F /proc/4242/fd/0 rows 50 cols 170", execs)
                self.assertFalse(any(t.name.endswith("(remote_resize)") for t in threading.enumerate()))

    def test_broken_cancel_still_restores_terminal(self):
        from cwsandbox._types import TerminalSession, StreamReader
        session = Mock(spec=TerminalSession)
        session.returncode = 0
        session.wait.return_value = 0
        close = Mock(spec=StreamReader.close, side_effect=RuntimeError("broken transport"))
        session.output = Output([b"done"], close)
        sandbox = Mock()
        sandbox.shell.return_value = session
        before = termios.tcgetattr(self.slave)
        handler = signal.getsignal(signal.SIGWINCH)
        with patch.object(sys, "stdin", TerminalFile(self.slave)), patch.object(sys, "stdout", TerminalFile(self.slave)):
            self.assertEqual(agent.pty_attach(sandbox, "exec bash"), 0)
        after = termios.tcgetattr(self.slave)
        after[3] &= ~getattr(termios, "PENDIN", 0)
        before[3] &= ~getattr(termios, "PENDIN", 0)
        self.assertEqual(after, before)
        self.assertEqual(signal.getsignal(signal.SIGWINCH), handler)

    def test_resize_and_write_errors_cancel_stream_and_restore_terminal(self):
        for failed_operation in ("resize", "write"):
            with self.subTest(operation=failed_operation):
                cancelled = threading.Event()
                session = Mock(returncode=None)
                session.stdin.write.return_value = Immediate()
                if failed_operation == "resize":
                    session.resize.side_effect = RuntimeError("resize failed")
                else:
                    session.stdin.write.side_effect = RuntimeError("write failed")
                def output():
                    os.write(self.master, b"input")
                    self.assertTrue(cancelled.wait(2))
                    raise RuntimeError("stream cancelled")
                    yield  # generator that raises as a cancelled SDK stream does
                session.output = Output(output(), cancelled.set)
                sandbox = Mock()
                sandbox.shell.return_value = session
                with patch.object(sys, "stdin", TerminalFile(self.slave)), patch.object(sys, "stdout", TerminalFile(self.slave)), patch.object(sys, "stderr", io.StringIO()) as stderr:
                    self.assertEqual(agent.pty_attach(sandbox, "exec bash"), 1)
                self.assertIn(failed_operation + " failed", stderr.getvalue())
                self.assertTrue(termios.tcgetattr(self.slave)[3] & termios.ICANON)


if __name__ == "__main__":
    unittest.main()
