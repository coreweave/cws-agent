import base64
import io
import os
import pathlib
import pty
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_terminal import agent


class ClipboardTests(unittest.TestCase):
    def run_helper(self, data, env):
        return subprocess.run([sys.executable, "-c", agent.CLIPBOARD_HELPER],
                              input=data, capture_output=True, env=env, timeout=5)

    def test_copy_reaches_pty_when_stdout_captured_and_no_controlling_tty(self):
        master, slave = pty.openpty()
        try:
            env = dict(os.environ, CWS_CLIPBOARD_TTY=os.ttyname(slave))
            env.pop("TMUX", None)
            data = "hello 👻\nsecond line".encode()
            result = self.run_helper(data, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"")
            self.assertTrue(select.select([master], [], [], 2)[0])
            self.assertEqual(os.read(master, 4096), b"\x1b]52;c;" + base64.b64encode(data) + b"\x07")
        finally:
            os.close(master)
            os.close(slave)

    def test_setup_falls_back_to_shell_stdin_when_tty_is_unknown(self):
        # In the sandbox `tty` prints "not a tty" (devpts is not mounted).
        script = agent.clipboard_setup()
        script = script[script.index('CWS_CLIPBOARD_TTY="$('):]  # skip the helper install
        for stub, expected in [("tty() { echo 'not a tty'; return 1; }; ", "/proc/$$/fd/0"),
                               ("tty() { echo /dev/pts/7; }; ", "/dev/pts/7")]:
            with self.subTest(expected=expected):
                result = subprocess.run(["sh", "-c", stub + script + f'printf "%s:%s" "{expected}" "$CWS_CLIPBOARD_TTY:$CWS_CLIPBOARD_COMMAND"'], capture_output=True, check=True)
                path, actual = result.stdout.decode().split(":", 1)
                self.assertEqual(actual, f"{path}:cws-copy")

    @unittest.skipUnless(os.path.exists("/proc/self/fd/0"), "needs procfs")
    def test_proc_fd_path_reaches_pty_from_a_new_session(self):
        master, slave = pty.openpty()
        try:
            env = dict(os.environ, CWS_CLIPBOARD_TTY=f"/proc/{os.getpid()}/fd/{slave}")
            env.pop("TMUX", None)
            result = subprocess.run([sys.executable, "-c", agent.CLIPBOARD_HELPER], input=b"tool runner",
                                    capture_output=True, env=env, timeout=5, start_new_session=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(select.select([master], [], [], 2)[0])
            self.assertEqual(os.read(master, 4096), b"\x1b]52;c;" + base64.b64encode(b"tool runner") + b"\x07")
        finally:
            os.close(master)
            os.close(slave)

    def test_install_refreshes_own_pbcopy_but_keeps_a_real_one(self):
        for existing, replaced in [(None, True), ("#!/usr/bin/env python3\n# old cws-copy build\n", True),
                                   ("#!/bin/sh\nexec /usr/bin/real-pbcopy\n", False)]:
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as home:
                bin_dir = pathlib.Path(home, "bin")
                if existing is not None:
                    bin_dir.mkdir()
                    (bin_dir / "pbcopy").write_text(existing)
                    (bin_dir / "pbcopy").chmod(0o755)
                with patch.object(agent, "AGENT_HOME", home):
                    setup = agent.clipboard_setup()
                subprocess.run(["sh", "-c", setup], check=True, capture_output=True)
                self.assertEqual((bin_dir / "cws-copy").read_text(), agent.CLIPBOARD_HELPER)
                self.assertTrue(os.access(bin_dir / "pbcopy", os.X_OK))
                self.assertEqual((bin_dir / "pbcopy").read_text() == agent.CLIPBOARD_HELPER, replaced)

    def test_rejects_terminal_paths_outside_dev_and_proc_fd(self):
        for target in ("/proc/self/fd/0", "/proc/12/fd/0/../../../etc/passwd", "/etc/passwd", "relative"):
            with self.subTest(target=target):
                env = dict(os.environ, CWS_CLIPBOARD_TTY=target)
                env.pop("TMUX", None)
                result = self.run_helper(b"text", env)
                self.assertEqual(result.returncode, 1)
                self.assertIn(b"invalid terminal path", result.stderr)

    def test_oversized_binary_and_unattached_input_fail(self):
        for data, expected in [(b"x" * 100001, b"100 KB"), (b"\xff", b"UTF-8"), (b"text", b"attached clipboard")]:
            env = dict(os.environ, CWS_CLIPBOARD_TTY="/dev/cws-no-such-terminal")
            env.pop("TMUX", None)
            result = self.run_helper(data, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)
            self.assertEqual(result.stdout, b"")

    def test_tmux_copy_targets_only_originating_session_client(self):
        namespace = {"__name__": "clipboard_test"}
        exec(agent.CLIPBOARD_HELPER, namespace)
        with patch.dict(os.environ, {"TMUX": "socket,1,0", "TMUX_PANE": "%3"}), patch.object(sys, "argv", ["cws-copy"]), patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"hello"))), patch("subprocess.check_output", side_effect=[b"$2\n", b"/dev/pts/4\n"]) as check, patch("subprocess.run") as run:
            namespace["main"]()
        self.assertEqual(check.call_args_list[1].args[0], ["tmux", "list-clients", "-t", "$2", "-F", "#{client_name}"])
        run.assert_called_once_with(["tmux", "load-buffer", "-w", "-t", "/dev/pts/4", "-"], input=b"hello", check=True, timeout=5)

    def test_tmux_refuses_ambiguous_clients(self):
        namespace = {"__name__": "clipboard_test"}
        exec(agent.CLIPBOARD_HELPER, namespace)
        with patch.dict(os.environ, {"TMUX": "socket,1,0", "TMUX_PANE": "%3"}), patch.object(sys, "argv", ["cws-copy"]), patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"hello"))), patch("subprocess.check_output", side_effect=[b"$2\n", b"/dev/pts/4\n/dev/pts/5\n"]), patch("subprocess.run") as run:
            with self.assertRaisesRegex(SystemExit, "exactly one"):
                namespace["main"]()
            run.assert_not_called()
