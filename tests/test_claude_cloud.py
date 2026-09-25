"""Cloud runner isolation, readiness, dispatch, and cleanup contracts."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_terminal import agent as cli


def result(stdout='', code=0):
    return types.SimpleNamespace(stdout=stdout, stderr='private credential error', returncode=code)


class ClaudeCloudTests(unittest.TestCase):
    def test_health_requires_registration_and_recent_poll(self):
        for health in ({}, {'runner_id': 'r'}, {'runner_id': 'r', 'last_poll_age_ms': None},
                       {'runner_id': 'r', 'last_poll_age_ms': 60000},
                       {'runner_id': 'r', 'last_poll_age_ms': -1}):
            self.assertFalse(cli.cloud_ready(health))
        self.assertTrue(cli.cloud_ready({'runner_id': 'r', 'last_poll_age_ms': 500}))

    def test_managed_agents_environment_is_rejected_before_allocating(self):
        with patch.object(cli, 'provision_session') as create, self.assertRaisesRegex(SystemExit, 'ccpool_'):
            cli.main(['cloud', 'start', 'test', '--environment', 'env_wrong'])
        create.assert_not_called()

    def test_missing_secret_and_short_lifetime_do_not_allocate(self):
        for extra, pattern in (([], 'export'), (['--lifetime', '5m'], '10m')):
            with patch.dict(os.environ, {}, clear=True), patch.object(cli, 'provision_session') as create:
                with self.assertRaisesRegex(SystemExit, pattern):
                    cli.main(['cloud', 'start', 'test', '--environment', 'ccpool_test', *extra])
            create.assert_not_called()

    def test_start_only_passes_runner_secret_and_uses_isolated_git_proxy(self):
        sb = Mock(sandbox_id='sandbox-test')
        with patch.dict(os.environ, {cli.CLOUD_SECRET: 'secret', 'ANTHROPIC_API_KEY': 'private',
                                     'CLAUDE_CODE_OAUTH_TOKEN': 'private-login'}), \
             patch.object(cli, 'find_active', return_value=None), \
             patch.object(cli, 'provision_session', return_value=sb) as create, \
             patch.object(cli, 'exec_retry', return_value=result('2.1.283 (Claude Code)\n--use-anthropic-git-proxy')), \
             patch.object(cli, 'start_checked_worker') as worker, \
             patch.object(cli, 'cloud_health', return_value={'runner_id': 'r', 'last_poll_age_ms': 0}), \
             patch.object(cli.time, 'time', return_value=1000), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['cloud', 'start', 'test', '--environment', 'ccpool_test']), 0)
        self.assertEqual(create.call_args.kwargs['env'], {cli.CLOUD_SECRET: 'secret'})
        command = worker.call_args.args[3]
        self.assertIn('--capacity 1', command)
        self.assertIn('--use-anthropic-git-proxy', command)
        self.assertIn('--retire-at 29500', command)
        self.assertNotIn('secret', command)
        saved = json.loads(sb.write_file.call_args.args[1])
        self.assertEqual(saved, cli.backend_config('claude-cloud', 'ccpool_test', 1))

    def test_failed_registration_stops_allocated_compute(self):
        sb = Mock()
        with patch.dict(os.environ, {cli.CLOUD_SECRET: 'secret'}), \
             patch.object(cli, 'find_active', return_value=None), \
             patch.object(cli, 'provision_session', return_value=sb), \
             patch.object(cli, 'exec_retry', return_value=result('2.1.283 (Claude Code)\n--use-anthropic-git-proxy')), \
             patch.object(cli, 'start_checked_worker'), patch.object(cli, 'cloud_health', return_value={}), \
             patch.object(cli.time, 'sleep'), patch.object(cli, 'stop_failed_sandbox') as stop:
            with self.assertRaisesRegex(SystemExit, 'did not register'):
                cli.main(['cloud', 'start', 'test', '--environment', 'ccpool_test'])
        stop.assert_called_once_with(sb)

    def test_old_runner_and_failed_setup_stop_without_exposing_output(self):
        sb = Mock()
        for setup_failure in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                setup = Path(directory) / 'setup.sh'
                setup.write_text('exit 1\n')
                extra = ['--setup', str(setup)] if setup_failure else []
                output = result('secret-in-error', 1) if setup_failure else result('2.1.224\n--use-anthropic-git-proxy')
                with patch.dict(os.environ, {cli.CLOUD_SECRET: 'secret'}), \
                     patch.object(cli, 'find_active', return_value=None), \
                     patch.object(cli, 'provision_session', return_value=sb), \
                     patch.object(cli, 'exec_retry', return_value=output), \
                     patch.object(cli, 'stop_failed_sandbox') as stop, \
                     patch.object(cli, 'start_checked_worker') as start, \
                     self.assertRaises(SystemExit) as raised:
                    cli.main(['cloud', 'start', 'test', '--environment', 'ccpool_test', *extra])
                self.assertNotIn('secret-in-error', str(raised.exception))
                self.assertIn('setup script failed' if setup_failure else '2.1.267', str(raised.exception))
                stop.assert_called_once_with(sb)
                start.assert_not_called()

    def test_health_rejects_malformed_or_failed_probe(self):
        for output in (result('null'), result('[]'), result('invalid'), result('{}', 1)):
            with patch.object(cli, 'exec_retry', return_value=output):
                self.assertEqual(cli.cloud_health(Mock()), {})

    def test_dispatch_uses_stdin_no_shell_and_pinned_ref(self):
        args = types.SimpleNamespace(name='box', goal='fix $(do-not-execute)', repo='.', ref='main', session=None)
        with patch.object(cli.shutil, 'which', return_value='/bin/claude'), \
             patch('subprocess.run', return_value=result('{"session_id":"session_test"}')) as run, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.cloud_dispatch(args, 'ccpool_test'), 0)
        self.assertEqual(run.call_args.args[0], ['/bin/claude', '-p', '--output-format', 'json',
                                               '--environment', 'ccpool_test', '--ref', 'main'])
        self.assertEqual(run.call_args.kwargs['input'], args.goal)
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertIn('https://claude.ai/code/session_test', output.getvalue())

    def test_followup_uses_existing_session_and_rejects_ref(self):
        args = types.SimpleNamespace(name='box', goal='next', repo='.', ref=None, session='session_test')
        with patch.object(cli.shutil, 'which', return_value='/bin/claude'), \
             patch('subprocess.run', return_value=result('{"ok":true,"session_id":"session_test"}')) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            cli.cloud_dispatch(args, 'ccpool_test')
        self.assertEqual(run.call_args.args[0][-2:], ['--cloud', 'session_test'])
        args.ref = 'main'
        with patch.object(cli.shutil, 'which', return_value='/bin/claude'), \
             self.assertRaisesRegex(SystemExit, 'new cloud session'):
            cli.cloud_dispatch(args, 'ccpool_test')

    def test_ambiguous_dispatch_is_not_retried_or_reported_successful(self):
        args = types.SimpleNamespace(name='box', goal='next', repo='.', ref=None, session=None)
        with patch.object(cli.shutil, 'which', return_value='/bin/claude'), \
             patch('subprocess.run', side_effect=subprocess.TimeoutExpired('claude', 90)) as run, \
             self.assertRaisesRegex(SystemExit, 'may have been accepted'):
            cli.cloud_dispatch(args, 'ccpool_test')
        self.assertEqual(run.call_count, 1)

    def test_restore_cannot_reuse_cloud_runner_filesystem_for_new_owner(self):
        with self.assertRaisesRegex(SystemExit, 'fresh'):
            cli.start_backend(Mock(), cli.backend_config('claude-cloud', 'ccpool_test', 1), 'box', {})

    def test_run_requires_cloud_backend_and_live_poll(self):
        with patch.object(cli, 'require_active', return_value=Mock()), \
             patch.object(cli, 'read_backend_config', return_value=None), \
             self.assertRaisesRegex(SystemExit, 'not a Claude Code Cloud'):
            cli.main(['cloud', 'run', 'box', 'goal'])
        with patch.object(cli, 'require_active', return_value=Mock()), \
             patch.object(cli, 'read_backend_config', return_value=cli.backend_config('claude-cloud', 'ccpool_test', 1)), \
             patch.object(cli, 'cloud_health', return_value={}), \
             patch.object(cli, 'cloud_dispatch') as send, self.assertRaisesRegex(SystemExit, 'not polling'):
            cli.main(['cloud', 'run', 'box', 'goal'])
        send.assert_not_called()
