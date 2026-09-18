"""Managed mode routing and checkpoint integration; no cloud calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from test_documented_sessions import load_documented_cli, operation, result

app = load_documented_cli()
SOURCE = '00000000-0000-4000-8000-000000000001'
IMAGE = 'example.test/agent@sha256:' + 'a' * 64


class ManagedHeadlessTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        container = types.SimpleNamespace(image=IMAGE, environment_variables={
            app.MANAGED_HEADLESS_ENV: '1', 'CWS_AGENT_NAME': 'example',
            'CWS_AGENT_HARNESS': 'claude', 'CWS_AGENT_DISK': '2Gi'},
            volume_mounts=[types.SimpleNamespace(volume='workspace', mount_path='/workspace',
                                                 sub_path=None, read_only=False)],
            resources=types.SimpleNamespace(requests={'cpu': '2', 'memory': '2Gi'}))
        self.raw = types.SimpleNamespace(sandbox_id=SOURCE, containers=[container], status='running',
                                         exec=Mock(return_value=operation(result())),
                                         stop=Mock(return_value=operation(None)),
                                         write_file=Mock(return_value=operation(None)))
        self.sb = app.managed_headless_sandbox(self.raw)
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.output))
        self.enterContext(patch.object(app, 'sandbox_auth', return_value='test-auth'))
        self.api = types.SimpleNamespace(list=Mock(return_value=operation([self.raw])),
                                         from_id=Mock(return_value=operation(self.raw)))
        self.enterContext(patch.object(app, 'Sandbox', self.api))

    def call(self, *args):
        return app.main(list(args))

    def assert_supervised(self, call):
        self.assertEqual(call.args[0][:4], ['python3', app.MANAGED_GATE_PATH, 'run', SOURCE])

    def test_discovery_run_and_exec_use_same_remote_admission(self):
        self.assertIsInstance(app.find_active('example'), app.ManagedHeadlessSandbox)
        self.assertEqual(self.call('exec', 'example', 'printf hello'), 0)
        self.assertEqual(self.call('run', 'example', 'test the project'), 0)
        calls = self.raw.exec.call_args_list
        self.assertEqual(len(calls), 2)  # metadata lookup does not execute a shell
        for call in calls:
            self.assert_supervised(call)
        self.assertNotEqual(calls[0].args[0][4], calls[1].args[0][4])

    def test_ambiguous_managed_exec_is_never_automatically_replayed(self):
        self.raw.exec.side_effect = TimeoutError('unavailable')
        with self.assertRaises(TimeoutError):
            app.exec_retry(self.sb, ['true'], attempts=5)
        self.raw.exec.assert_called_once()

    def test_upload_commands_preserve_streaming_process_and_stdin(self):
        process = object()
        self.raw.exec.return_value = process
        for action in ('status', 'put', 'extract'):
            command = app.upload_command({'source': '/private/local', 'chunks': []}, action)
            self.assertIs(self.sb.exec(command, stdin=True, timeout_seconds=30), process)
            call = self.raw.exec.call_args
            self.assert_supervised(call)
            self.assertEqual(call.args[0][5:], command)
            self.assertEqual(call.kwargs, {'stdin': True, 'timeout_seconds': 30})
            self.assertNotIn('/private/local', str(call))

    def test_config_import_is_supervised_and_keeps_payload_on_stdin(self):
        item = dict(id='skill:review', kind='skill', name='review', blocked='', bytes=10,
                    hash='abcdef0123456789', source='example', files={'SKILL.md': 'Review code.'})
        process = types.SimpleNamespace(stdin=types.SimpleNamespace(
            write=Mock(return_value=operation(None)), close=Mock(return_value=operation(None))),
            result=lambda **kw: result())
        self.raw.exec.side_effect = [operation(result(stdout='{}')), process]
        with patch.object(app, 'discover_imports', return_value=[item]), \
                patch.object(app.sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=['all', 'y']):
            app.sync_agent_config(self.sb, app.HARNESSES['claude'], types.SimpleNamespace())
        for call in self.raw.exec.call_args_list:
            self.assert_supervised(call)
        self.assertTrue(self.raw.exec.call_args.kwargs['stdin'])
        self.assertEqual(json.loads(process.stdin.write.call_args.args[0])['items'][0]['id'], 'skill:review')

    def test_unsupported_interactive_and_tmux_paths_fail_before_exec(self):
        commands = [('connect', 'example'), ('login', 'example'), ('rc', 'example'),
                    ('session', 'start', 'example', 'task'), ('session', 'restart', 'example', 'task'),
                    ('session', 'attach', 'example', 'task'), ('session', 'stop', 'example', 'task'),
                    ('session', 'resume', 'example', SOURCE), ('bridge', 'telegram', 'example'),
                    ('snapshot', 'example'), ('down', 'example')]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.call(*command), 1)
        self.raw.exec.assert_not_called()
        self.raw.stop.assert_not_called()
        with self.assertRaises(app.CheckpointError):
            app.pty_attach(self.sb, 'exec bash')
        with self.assertRaises(app.CheckpointError):
            self.sb.write_file('/workspace/file', b'unsupported')
        with self.assertRaises(app.CheckpointError):
            self.sb.snapshot()

    def test_discard_without_snapshot_remains_explicit(self):
        self.assertEqual(self.call('down', 'example', '--no-snapshot'), 0)
        self.raw.stop.assert_called_once()
        self.raw.exec.assert_not_called()

    def test_launch_rejects_unpinned_image_and_workers_before_allocation(self):
        for flags in ([], ['--image', 'example.test/agent:latest'],
                      ['--image', IMAGE, '--claude-env', 'env_example'],
                      ['--image', IMAGE, '--telegram'], ['--image', IMAGE, '--agent', 'openai']):
            with self.subTest(flags=flags):
                self.assertEqual(self.call('launch', 'example', '--managed-headless', *flags), 1)
        self.api.list.assert_not_called()

    def test_launch_is_detached_and_provision_supervises_bootstrap(self):
        self.api.list.return_value = operation([])
        with patch.object(app, 'provision_session', return_value=self.sb) as provision, \
                patch.object(app, 'build_env', return_value={}), \
                patch.object(app, 'pty_attach') as pty:
            self.assertEqual(self.call('launch', 'example', '--managed-headless', '--image', IMAGE), 0)
        self.assertTrue(provision.call_args.kwargs['managed_headless'])
        pty.assert_not_called()
        events = []
        with patch.object(app, 'create_session_sandbox', return_value=self.raw) as create, \
                patch.object(app, 'run_bootstrap', side_effect=lambda *a: events.append('bootstrap')) as bootstrap:
            self.raw.write_file.side_effect = lambda *a: (events.append('install'), operation(None))[1]
            wrapped = app.provision_session(name='example', harness=app.HARNESSES['claude'],
                                            repo_url=None, managed_headless=True, env={})
        self.assertIsInstance(wrapped, app.ManagedHeadlessSandbox)
        self.assertEqual(events, ['install', 'bootstrap'])
        self.assertIsInstance(bootstrap.call_args.args[0], app.ManagedHeadlessSandbox)
        self.assertEqual(create.call_args.kwargs['env'][app.MANAGED_HEADLESS_ENV], '1')
        self.assertEqual(self.raw.exec.call_args.args[0], ['python3', app.MANAGED_GATE_PATH, 'init', SOURCE])

    def test_reserved_mode_cannot_be_spoofed_via_env(self):
        with self.assertRaises(app.CheckpointError):
            app.build_env(app.HARNESSES['claude'], [app.MANAGED_HEADLESS_ENV + '=1'], [])

    def test_ordinary_sources_still_require_external_hook(self):
        with self.assertRaises(app.CheckpointError):
            app.checkpoint_plan(self.raw, 'example', '/example/hook')
        self.raw.containers[0].environment_variables.pop(app.MANAGED_HEADLESS_ENV)
        with self.assertRaises(app.CheckpointError):
            app.checkpoint_plan(self.raw, 'example', app.BUILTIN_WRITER_GATE)
        self.assertEqual(app.checkpoint_plan(self.raw, 'example', '/example/hook')['gate'], '/example/hook')

    def test_builtin_gate_failure_does_not_snapshot_or_stop(self):
        self.raw.exec.return_value = operation(result(returncode=75, stderr='private command details'))
        self.raw.snapshot = Mock()
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(self.call('down', 'example', '--checkpoint-dir', str(Path(temporary) / 'checkpoint')), 1)
        self.raw.snapshot.assert_not_called()
        self.raw.stop.assert_not_called()
        self.assertEqual(self.raw.exec.call_args.args[0][:4], ['python3', app.MANAGED_GATE_PATH, 'quiesce', SOURCE])
        self.assertNotIn('private command details', self.output.getvalue())

    def test_status_uses_control_path_while_admission_is_closed(self):
        self.raw.exec.return_value = operation(result(stdout=json.dumps({'owner': SOURCE, 'pending': 1, 'unconfirmed': [SOURCE]})))
        with patch.object(app, 'session_snapshots', return_value=[]):
            self.assertEqual(self.call('status', 'example'), 0)
        self.assertEqual(self.raw.exec.call_args.args[0], ['python3', app.MANAGED_GATE_PATH, 'status', SOURCE])
        self.assertIn('admission: closed', self.output.getvalue())
        self.assertIn('unconfirmed: 1', self.output.getvalue())

    def test_manifest_restore_preserves_managed_mode_and_rejects_attach(self):
        plan = app.checkpoint_plan(self.raw, 'example', app.BUILTIN_WRITER_GATE)
        snapshot = types.SimpleNamespace(request_id=plan['request_id'], file_system_snapshot_id=SOURCE, size_bytes=1)
        with patch.object(app, 'find_active', return_value=None), \
                patch.object(app, 'checkpoint_restore', return_value=(snapshot, plan)), \
                patch.object(app, 'provision_session', return_value=self.sb) as provision, \
                patch.object(app, 'read_backend_config', return_value=None), \
                patch.object(app, 'sync_agent_config'), patch.object(app, 'build_env', return_value={}):
            self.assertEqual(self.call('restore', 'example', '--checkpoint-dir', './checkpoint'), 0)
            self.assertTrue(provision.call_args.kwargs['managed_headless'])
            provision.reset_mock()
            self.assertEqual(self.call('restore', 'example', '--checkpoint-dir', './checkpoint', '--connect'), 1)
            provision.assert_not_called()


if __name__ == '__main__':
    unittest.main()
