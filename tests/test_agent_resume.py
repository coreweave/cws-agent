"""Unified resume contracts, with all platform boundaries mocked."""
import contextlib
import io
import json
import types
import unittest
from unittest.mock import patch
from test_terminal import agent


class AgentResumeTests(unittest.TestCase):
    def setUp(self):
        self.sb = types.SimpleNamespace(sandbox_id='box-example')
        self.history = [{'agent':'claude','id':'chat-example','cwd':'/workspace/project'}]
        self.row = agent.resume_row('example', 'claude', self.sb.sandbox_id,
                                    conversation=self.history[0], sb=self.sb)
        self.discovery = self.enterContext(patch.object(agent, 'discover_resume', return_value=([self.row], [])))
        self.enterContext(patch.object(agent, 'read_workspace_document', return_value={}))
        self.enterContext(patch.object(agent, 'remote_native_history', return_value=self.history))
        self.enterContext(patch.object(agent, 'sync_agent_config'))
        self.enterContext(patch.object(agent, 'exec_retry', return_value=types.SimpleNamespace(returncode=0)))
        self.attach = self.enterContext(patch.object(agent, 'pty_attach', return_value=17))
        self.restore = self.enterContext(patch.object(agent, 'restore_resume_row', return_value=self.sb))
        self.output = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.stderr = self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.stdin_tty = self.enterContext(patch.object(agent.sys.stdin, 'isatty', return_value=True))
        self.enterContext(patch.object(agent.sys.stdout, 'isatty', return_value=True))

    def test_owned_terminal_error_is_actionable(self):
        self.attach.side_effect = agent.ResumeOperationError('error: connect requires a TTY')
        self.assertEqual(agent.main(['resume', 'chat-example']), 2)
        self.assertIn('connect requires a TTY', self.stderr.getvalue())

    def test_remote_errors_do_not_disclose_their_payload(self):
        for kind in (ValueError, SystemExit):
            with self.subTest(kind=kind):
                self.stderr.seek(0)
                self.stderr.truncate()
                self.attach.side_effect = kind('synthetic-private-remote-output')
                self.assertEqual(agent.main(['resume', 'chat-example']), 2)
                self.assertNotIn('synthetic-private-remote-output', self.stderr.getvalue())

    def test_invalid_env_is_rejected_without_echoing_input(self):
        self.assertEqual(agent.main(['resume', 'chat-example', '--env', 'synthetic-private-input']), 2)
        self.discovery.assert_not_called()
        self.assertIn('--env expects KEY=VALUE', self.stderr.getvalue())
        self.assertNotIn('synthetic-private-input', self.stderr.getvalue())

    def test_all_resume_spellings_share_lifecycle(self):
        for argv in (['resume','chat-example'], ['claude','--resume','chat-example'],
                     ['session','resume','example','chat-example']):
            with self.subTest(argv=argv):
                self.assertEqual(agent.main(argv), 17)
                self.assertIn('claude --resume chat-example', self.attach.call_args.args[1])
        self.restore.assert_not_called()

    def test_uuid_namespaces_are_not_inferred_by_shape(self):
        uuid = '12345678-1234-1234-1234-123456789012'
        rows = [{**self.row, 'sandbox_id':uuid}, {**self.row,'session_id':uuid}]
        found = agent.resolve_resume_rows(rows, types.SimpleNamespace(target=uuid))
        self.assertEqual(len(found), 2)
        self.assertEqual(len(agent.resolve_resume_rows(rows, types.SimpleNamespace(sandbox=uuid))), 1)

    def test_partial_discovery_never_automatically_attaches_one_match(self):
        self.stdin_tty.return_value = False
        self.discovery.return_value = ([self.row], [{'code':'workspace_discovery_failed'}])
        self.assertEqual(agent.main(['resume','chat-example','--no-attach']), 2)
        self.attach.assert_not_called()
        self.restore.assert_not_called()
        self.assertIn('Discovery may be incomplete', self.stderr.getvalue())

    def test_harness_mismatch_precedes_allocation(self):
        self.assertEqual(agent.main(['codex','--resume','chat-example']), 2)
        self.restore.assert_not_called()
        self.attach.assert_not_called()

    def test_stopped_conversation_restores_then_verifies(self):
        self.row.update(_sb=None, state='saved', snapshot_id='snapshot-example',
                        _config={'image':'example.invalid/agent:latest','cpu':'8','memory':'32Gi','disk':'20Gi','lifetime_seconds':86400})
        self.assertEqual(agent.main(['resume','chat-example','--no-attach']), 0)
        self.restore.assert_called_once()
        self.attach.assert_not_called()

    def test_running_only_does_not_restore(self):
        self.row.update(_sb=None, state='saved')
        self.assertEqual(agent.main(['resume','chat-example','--running-only']), 2)
        self.restore.assert_not_called()

    def test_cross_device_requires_explicit_default_acceptance(self):
        self.stdin_tty.return_value = False
        self.row.update(_sb=None, state='saved')
        self.assertEqual(agent.main(['resume','chat-example','--no-attach']), 2)
        self.assertIn('Original configuration is unavailable', self.stderr.getvalue())
        self.restore.assert_not_called()

    def test_cloud_runner_remains_visible_and_unresumable(self):
        row = agent.resume_row('runner', 'claude', 'box-runner', backend='claude-cloud', sb=self.sb)
        self.discovery.return_value = ([row], [])
        self.assertEqual(agent.main(['resume','runner','--no-attach']), 2)
        self.assertIn('Resume unavailable', self.stderr.getvalue())
        self.restore.assert_not_called()

    def test_missing_conversation_after_restore_does_not_start_fresh(self):
        self.row.update(_sb=None, state='saved', _config={'image':'example.invalid/agent:latest','cpu':'8','memory':'32Gi','disk':'20Gi','lifetime_seconds':86400})
        with patch.object(agent, 'remote_native_history', return_value=[]):
            self.assertEqual(agent.main(['resume','chat-example','--no-attach']), 2)
        self.attach.assert_not_called()
        self.assertIn('Conversation is absent', self.stderr.getvalue())

    def test_shortcut_preserves_only_explicit_resource_overrides(self):
        for options, expected in (([], (None, None, None)),
                                  (["--cpu", "8", "--memory", "32Gi", "--lifetime", "24h"], ("8", "32Gi", "24h"))):
            with self.subTest(options=options), patch.object(agent, "cmd_agent_resume", return_value=0) as resume:
                self.assertEqual(agent.main(["claude", "--resume", "chat-a", *options]), 0)
                args = resume.call_args.args[0]
                self.assertEqual((args.cpu, args.memory, args.lifetime), expected)

class RecoveryReplayTests(unittest.TestCase):
    def setUp(self):
        self.platform = self.enterContext(patch.object(agent, 'Sandbox'))
        self.platform.list.return_value.result.return_value = []
        self.enterContext(patch.object(agent, 'sandbox_auth', return_value='offline'))
        self.args = types.SimpleNamespace()
        self.config = dict(image='example.invalid/agent:latest', cpu='8', memory='32Gi',
                           disk='40Gi', mode='cks', lifetime_seconds=86400,
                           env_names=['EXAMPLE_TOKEN'])
        self.row = dict(workspace='example', agent='claude', snapshot_id='snapshot-example',
                        _config=self.config)

    def test_agent_replays_exact_snapshot_resources_and_required_environment(self):
        with patch.object(agent, 'build_env', return_value={}), \
                patch.dict(agent.os.environ, {'EXAMPLE_TOKEN':'synthetic-value'}), \
                patch.object(agent, 'provision_session', return_value='restored') as provision:
            self.assertEqual(agent.restore_resume_row(self.row, self.args, self.config), 'restored')
        options = provision.call_args.kwargs
        self.assertEqual(options['restore_snapshot_id'], 'snapshot-example')
        for key in ('image', 'cpu', 'memory', 'disk', 'mode', 'lifetime_seconds'):
            self.assertEqual(options[key], self.config[key])
        self.assertEqual(options['env'], {'EXAMPLE_TOKEN':'synthetic-value'})
        self.assertIsNone(options['repo_url'])

    def test_missing_required_environment_prevents_allocation(self):
        with patch.object(agent, 'build_env', return_value={}), \
                patch.dict(agent.os.environ, {}, clear=True), \
                patch.object(agent, 'provision_session') as provision, \
                self.assertRaisesRegex(ValueError, 'Required environment variable'):
            agent.restore_resume_row(self.row, self.args, self.config)
        provision.assert_not_called()

    def test_saved_configuration_and_explicit_overrides(self):
        self.args.cpu, self.args.lifetime = '4', '2h'
        config, missing = agent.recovery_config(self.row, self.args)
        self.assertFalse(missing)
        self.assertEqual((config['cpu'], config['memory'], config['lifetime_seconds']), ('4', '32Gi', 7200))
        self.assertEqual(self.config['cpu'], '8')

    def test_shell_replay_forwards_secrets_volumes_and_prepare_only(self):
        self.row['agent'] = 'shell'
        self.config.update(gpu='any:1', secrets=['EXAMPLE_SECRET'], volumes=['example-volume:/mnt/data'])
        with patch.object(agent, 'cmd_shell', return_value='restored') as shell:
            self.assertEqual(agent.restore_resume_row(self.row, self.args, self.config), 'restored')
        args = shell.call_args.args[0]
        self.assertEqual(args.snapshot, 'snapshot-example')
        self.assertTrue(args._prepare_only)
        self.assertEqual((args.image,args.cpu,args.memory,args._restore_disk,args._restore_lifetime),
                         ('example.invalid/agent:latest','8','32Gi','40Gi',86400))
        self.assertEqual(args.secret[0].name, 'EXAMPLE_SECRET')
        self.assertEqual((args.volume[0].volume_id,args.volume[0].mount_path), ('example-volume','/mnt/data'))

class ResumeReviewRegressionTests(unittest.TestCase):
    def test_shortcut_prepare_flags_require_resume(self):
        for flag in ('--no-attach', '--running-only'):
            with self.subTest(flag=flag), patch.object(agent, 'cmd_launch') as launch, \
                    patch.object(agent, 'cmd_agent_resume') as resume, \
                    contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit):
                agent.main(['claude','example',flag])
            self.assertIn('--detach', error.getvalue())
            launch.assert_not_called(); resume.assert_not_called()

    def test_invalid_resources_never_discover_or_allocate(self):
        for flag, value in (('--cpu','abc'),('--memory','bad'),('--mode','elsewhere')):
            with self.subTest(flag=flag), patch.object(agent, 'discover_resume') as discover, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                agent.main(['resume','example',flag,value])
            discover.assert_not_called()

    def test_human_discovery_failure_reports_credentials_hint(self):
        errors=[{'code':'live_discovery_failed','message':'Could not list running workspaces; check sandbox credentials and access'}]
        with patch.object(agent,'discover_resume',return_value=([],errors)), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(agent.main(['resume','example']),2)
        self.assertIn('credentials and access', error.getvalue())

    def test_no_terminal_does_not_restore_billable_compute(self):
        row=agent.resume_row('example','shell','old-box',saved=100,config={
            'image':'python:3.11','cpu':'2','memory':'4Gi','disk':'10Gi','lifetime_seconds':3600})
        with patch.object(agent,'discover_resume',return_value=([row],[])), \
                patch.object(agent.sys.stdin,'isatty',return_value=False), \
                patch.object(agent,'restore_resume_row') as restore, \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(agent.main(['resume','example']),2)
        restore.assert_not_called()
        self.assertIn('--no-attach',error.getvalue())

    def test_legacy_resume_connect_explains_restore(self):
        with patch.object(agent,'discover_resume') as discover, contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(agent.main(['resume','example','--connect']),2)
        discover.assert_not_called()
        self.assertIn('cws-agent restore NAME --connect',error.getvalue())

    def test_codex_saved_login_does_not_require_an_old_ambient_api_key(self):
        row={'workspace':'example','agent':'codex','snapshot_id':'saved-example'}
        config={'image':'example.invalid/agent:latest','cpu':'2','memory':'4Gi','disk':'10Gi',
                'mode':None,'lifetime_seconds':3600,'env_names':['OPENAI_API_KEY']}
        with patch.object(agent,'Sandbox') as platform, patch.object(agent,'sandbox_auth',return_value='offline'), \
                patch.object(agent,'build_env',return_value={}), patch.dict(agent.os.environ,{},clear=True), \
                patch.object(agent,'provision_session',return_value='restored') as provision:
            platform.list.return_value.result.return_value=[]
            self.assertEqual(agent.restore_resume_row(row,types.SimpleNamespace(),config),'restored')
        self.assertNotIn('OPENAI_API_KEY',provision.call_args.kwargs['env'])
