"""Durable admission tests; real Linux subprocess tests use no SDK or cloud calls."""
import ast
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
import uuid
from unittest.mock import patch

SOURCE = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'
TREE = ast.parse((Path(__file__).resolve().parents[1] / 'cws-agent.py').read_text())
HELPER = next(ast.literal_eval(node.value) for node in TREE.body
              if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MANAGED_GATE_HELPER'
                                                       for t in node.targets))


def load_helper():
    module = types.ModuleType('managed_gate_test')
    exec(compile(HELPER, '<managed gate>', 'exec'), module.__dict__)
    return module


class DurableGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'gate'
        self.module = load_helper()
        self.stack = self.enterContext(contextlib.ExitStack())
        self.stack.enter_context(patch.object(self.module, 'subreaper'))
        self.flush = self.stack.enter_context(patch.object(self.module, 'flush_workspace'))
        self.stack.enter_context(patch.object(self.module, 'identity', return_value='1234'))
        mask = os.umask(0o077)
        try:
            self.gate = self.module.Gate(self.root, SOURCE, initialize=True)
        finally:
            os.umask(mask)
        self.addCleanup(self.gate.db.close)

    def test_abort_tombstone_fences_delayed_quiesce_after_reopen(self):
        operation = str(uuid.uuid4())
        self.gate.release(operation)
        self.gate.db.close()
        self.gate = self.module.Gate(self.root, SOURCE)
        self.addCleanup(self.gate.db.close)
        with self.assertRaisesRegex(self.module.GateError, 'delayed quiesce'):
            self.gate.quiesce(operation, 1)
        self.assertIsNone(self.gate.owner())
        self.flush.assert_not_called()

    def test_competing_owner_and_reinitialize_cannot_reopen_admission(self):
        self.gate.quiesce(SOURCE, 1)
        with self.assertRaisesRegex(self.module.GateError, 'another checkpoint'):
            self.gate.quiesce(OTHER, 1)
        self.gate.release(OTHER)
        gate = self.module.Gate(self.root, SOURCE, initialize=True)
        self.addCleanup(gate.db.close)
        self.assertEqual(gate.owner(), SOURCE)
        gate.release(SOURCE)
        with self.assertRaises(self.module.GateError):
            gate.quiesce(OTHER, 1)
        self.assertIsNone(gate.owner())

    def test_wrong_source_or_missing_database_never_resets_state(self):
        self.gate.quiesce(SOURCE, 1)
        with self.assertRaisesRegex(self.module.GateError, 'identity mismatch'):
            self.module.Gate(self.root, OTHER, initialize=True)
        self.assertEqual(self.gate.owner(), SOURCE)
        missing = self.root.parent / 'empty'
        missing.mkdir(mode=0o700)
        with self.assertRaises(self.module.GateError):
            self.module.Gate(missing, SOURCE)
        self.assertFalse((missing / 'state.sqlite3').exists())

    def test_pid_reuse_refuses_checkpoint_even_when_process_exists(self):
        self.gate.db.execute('INSERT INTO jobs VALUES (?, ?, ?, 0, NULL)', (OTHER, os.getpid(), 'old-start'))
        with self.assertRaisesRegex(self.module.GateError, 'lost its supervisor'):
            self.gate.quiesce(SOURCE, 1)
        self.assertEqual(self.gate.status()['unconfirmed'], [OTHER])
        self.assertEqual(self.gate.owner(), SOURCE)
        self.flush.assert_not_called()

    def test_flush_error_retains_owner(self):
        self.flush.side_effect = OSError('injected disk failure')
        with self.assertRaises(OSError):
            self.gate.quiesce(SOURCE, 1)
        self.assertEqual(self.gate.owner(), SOURCE)

    def test_receipts_are_bounded_without_discarding_tombstones(self):
        self.gate.LIMIT = 1
        self.gate.release(SOURCE)
        self.gate.release(SOURCE)  # idempotent even at capacity
        with self.assertRaisesRegex(self.module.GateError, 'receipt limit'):
            self.gate.quiesce(OTHER, 1)
        with self.assertRaisesRegex(self.module.GateError, 'delayed quiesce'):
            self.gate.quiesce(SOURCE, 1)
        self.assertIsNone(self.gate.owner())

    def test_full_command_ledger_still_allows_checkpoint(self):
        self.gate.LIMIT = 1
        self.gate.db.execute('INSERT INTO jobs VALUES (?, ?, ?, 1, 0)', (OTHER, os.getpid(), '1234'))
        with patch.object(self.module.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(self.module.GateError, 'receipt limit'):
                self.gate.run(SOURCE, ['true'], 0o022)
        spawn.assert_not_called()
        self.gate.quiesce(SOURCE, 1)
        self.flush.assert_called_once()


@unittest.skipUnless(sys.platform == 'linux', 'requires Linux /proc and child subreapers')
class LinuxGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.helper = self.root / 'helper.py'
        # Map only the workspace mount to a fixture; execute the actual helper.
        self.helper.write_text(HELPER.replace("os.open('/workspace',", f'os.open({str(self.root)!r},'))
        self.directory = self.root / 'gate'
        self.release = self.root / 'finish'
        self.processes = []
        self.addCleanup(self.cleanup_processes)
        self.assertEqual(self.call('init').returncode, 0)

    def command(self, action, *args):
        return [sys.executable, str(self.helper), '--directory', str(self.directory), action, SOURCE, *args]

    def call(self, action, *args, **kwargs):
        return subprocess.run(self.command(action, *args), text=True, capture_output=True, timeout=8, **kwargs)

    def spawn(self, action, *args, **kwargs):
        process = subprocess.Popen(self.command(action, *args), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True, **kwargs)
        self.processes.append(process)
        return process

    def cleanup_processes(self):
        self.release.touch()
        for process in self.processes:
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=3)

    def until(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail('timed out waiting for subprocess state')

    def status(self):
        result = self.call('status')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_streaming_writer_drains_and_new_writer_is_rejected(self):
        target = self.root / 'upload'
        writer = self.spawn('run', OTHER, sys.executable, '-c',
                            "import sys; from pathlib import Path; "
                            f"Path({str(target)!r}).write_text(sys.stdin.read()); print('uploaded')")
        writer.stdin.write('chunk-one\n')
        writer.stdin.flush()
        self.until(lambda: self.status()['pending'] == 1)
        quiesce = self.spawn('quiesce', SOURCE, '5')
        self.until(lambda: self.status()['owner'] == SOURCE)
        refused = self.call('run', str(uuid.uuid4()), sys.executable, '-c', "raise RuntimeError('must not run')")
        self.assertEqual(refused.returncode, 75)
        self.assertIsNone(quiesce.poll())
        writer.stdin.write('chunk-two\n')
        writer.stdin.close()
        writer.stdin = None
        stdout, stderr = writer.communicate(timeout=5)
        self.assertEqual(writer.returncode, 0, stderr)
        self.assertEqual(stdout, 'uploaded\n')
        _, stderr = quiesce.communicate(timeout=5)
        self.assertEqual(quiesce.returncode, 0, stderr)
        self.assertEqual(target.read_text(), 'chunk-one\nchunk-two\n')
        self.assertEqual(self.status()['pending'], 0)

    def test_detached_double_fork_child_is_drained_after_parent_exit(self):
        started = self.root / 'child-started'
        finished = self.root / 'child-finished'
        script = f'''import os, time
from pathlib import Path
if os.fork(): os._exit(0)
os.setsid()
if os.fork(): os._exit(0)
Path({str(started)!r}).touch()
deadline=time.monotonic()+10
while not Path({str(self.release)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)
Path({str(finished)!r}).touch()
'''
        writer = self.spawn('run', OTHER, sys.executable, '-c', script)
        self.until(started.exists)
        self.assertIsNone(writer.poll())
        quiesce = self.spawn('quiesce', SOURCE, '5')
        self.until(lambda: self.status()['owner'] == SOURCE)
        self.assertIsNone(quiesce.poll())
        self.release.touch()
        for process in (writer, quiesce):
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
        self.assertTrue(finished.exists())

    def test_lost_supervisor_keeps_unknown_child_fenced(self):
        started = self.root / 'child-started'
        writer = self.spawn('run', OTHER, sys.executable, '-c', f'''import time
from pathlib import Path
Path({str(started)!r}).touch()
deadline=time.monotonic()+10
while not Path({str(self.release)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)
''')
        self.until(started.exists)
        writer.kill()  # deliberately leave its admitted child alive
        writer.wait(timeout=3)
        outcome = self.call('quiesce', SOURCE, '1')
        self.assertEqual(outcome.returncode, 75)
        self.assertIn('lost its supervisor', outcome.stderr)
        self.assertEqual(self.status()['unconfirmed'], [OTHER])
        self.assertEqual(self.status()['owner'], SOURCE)
        self.assertEqual(self.call('release', SOURCE).returncode, 0)
        # Abort reopens admission, but never erases the uncertain job receipt.
        self.assertEqual(self.call('quiesce', str(uuid.uuid4()), '1').returncode, 75)

    def test_timeout_abort_and_delayed_quiesce_do_not_reclaim_admission(self):
        writer = self.spawn('run', OTHER, sys.executable, '-c', 'import sys; sys.stdin.read()')
        self.until(lambda: self.status()['pending'] == 1)
        self.assertEqual(self.call('quiesce', SOURCE, '.1').returncode, 75)
        self.assertEqual(self.status()['owner'], SOURCE)
        self.assertEqual(self.call('release', SOURCE).returncode, 0)
        self.assertEqual(self.call('quiesce', SOURCE, '1').returncode, 75)
        self.assertIsNone(self.status()['owner'])
        writer.communicate('', timeout=3)

    def test_release_while_draining_prevents_successful_quiesce(self):
        writer = self.spawn('run', OTHER, sys.executable, '-c', 'import sys; sys.stdin.read()')
        self.until(lambda: self.status()['pending'] == 1)
        quiesce = self.spawn('quiesce', SOURCE, '5')
        self.until(lambda: self.status()['owner'] == SOURCE)
        self.assertEqual(self.call('release', SOURCE).returncode, 0)
        writer.communicate('', timeout=3)
        _, stderr = quiesce.communicate(timeout=3)
        self.assertEqual(quiesce.returncode, 75, stderr)
        self.assertIsNone(self.status()['owner'])
        self.assertEqual(self.call('quiesce', SOURCE, '1').returncode, 75)

    def test_completed_command_cannot_be_replayed_and_exit_code_is_preserved(self):
        target = self.root / 'counter'
        command = [sys.executable, '-c', f"from pathlib import Path; Path({str(target)!r}).write_text('once'); raise SystemExit(7)"]
        self.assertEqual(self.call('run', OTHER, *command).returncode, 7)
        target.write_text('changed')
        self.assertEqual(self.call('run', OTHER, *command).returncode, 75)
        self.assertEqual(target.read_text(), 'changed')
        self.assertEqual(self.call('quiesce', SOURCE, '1').returncode, 0)

    def test_private_receipts_do_not_change_workload_file_permissions(self):
        target = self.root / 'new-file'
        result = self.call('run', OTHER, sys.executable, '-c',
                           f"from pathlib import Path; Path({str(target)!r}).touch()", umask=0o022)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.directory / 'state.sqlite3').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
