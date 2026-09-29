import contextlib
import io
import json
import tempfile
from pathlib import Path
import types
import unittest
from unittest.mock import patch
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from test_terminal import agent


class ResumeInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.rows = [agent.resume_row('alpha','claude','box-a',conversation={'id':'chat-a','title':'Fix parser'},sb=object()),
                     agent.resume_row('beta','codex','box-b',conversation={'id':'chat-b','title':'Write tests'},sb=object())]

    def pick(self, keys, rows=None):
        with create_pipe_input() as source, create_app_session(input=source,output=DummyOutput()):
            source.send_text(keys)
            return agent.resume_picker(rows or self.rows)

    def test_json_input_and_schema(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'input.json'
            path.write_text(json.dumps({'schema_version':1,'list':True}))
            with patch.object(agent,'discover_resume',return_value=(self.rows,[])), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(agent.main(['resume','--input-json','@'+str(path)]),0)
            self.assertEqual(len(json.loads(output.getvalue())['rows']),2)
            path.write_text('{"list":true,"list":false}')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(agent.main(['resume','--input-json','@'+str(path)]),2)
            self.assertEqual(json.loads(output.getvalue())['errors'][0]['code'],'invalid_input')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(agent.main(['schema','resume']),0)
        schema=json.loads(output.getvalue())
        self.assertFalse(schema['input']['additionalProperties'])
        self.assertIn('remote_path',schema['input']['properties'])

    def test_json_shortcut_list_never_launches(self):
        with patch.object(agent,'discover_resume',return_value=(self.rows,[])), patch.object(agent,'cmd_launch') as launch, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(agent.main(['claude','--list','--json']),0)
            launch.assert_not_called()

    def test_shortcut_preserves_only_explicit_resource_overrides(self):
        for options, expected in (([], (None, None, None)),
                                  (["--cpu", "8", "--memory", "32Gi", "--lifetime", "24h"], ("8", "32Gi", "24h"))):
            with self.subTest(options=options), patch.object(agent, "cmd_agent_resume", return_value=0) as resume:
                self.assertEqual(agent.main(["claude", "--resume", "chat-a", *options]), 0)
                args = resume.call_args.args[0]
                self.assertEqual((args.cpu, args.memory, args.lifetime), expected)

    def test_shortcut_json_overrides_defaults_but_not_explicit_flags(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "input.json"
            path.write_text(json.dumps({"session_id": "chat-a", "cpu": "8", "no_attach": True}))
            with patch.object(agent, "cmd_agent_resume", return_value=0) as resume:
                self.assertEqual(agent.main(["claude", "--input-json", "@" + str(path)]), 0)
                args = resume.call_args.args[0]
                self.assertEqual(args.cpu, "8")
                self.assertIsNone(args.memory)
                self.assertTrue(args.json)
            with patch.object(agent, "cmd_agent_resume") as resume, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(agent.main(["claude", "--cpu", "4", "--input-json", "@" + str(path)]), 2)
                resume.assert_not_called()
                self.assertEqual(json.loads(output.getvalue())["errors"][0]["code"], "invalid_input")

    def test_bare_schema_discovers_the_same_resume_contract_without_cloud_access(self):
        with patch.object(agent.Sandbox, "list", side_effect=AssertionError("schema must be local")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(agent.main(["schema"]), 0)
            catalog = json.loads(output.getvalue())
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(agent.main(["schema", "resume"]), 0)
            self.assertEqual(catalog["commands"]["resume"], json.loads(output.getvalue()))
            self.assertEqual(catalog["schema_version"], 1)


class ResumeInputContractTests(unittest.TestCase):
    def call_json(self, argv):
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            status = agent.main(argv)
        return status, json.loads(output.getvalue())

    def test_invalid_cli_input_is_json_before_discovery(self):
        cases = [
            ['--cpu', 'abc'], ['--memory', '-1'], ['--mode', 'other'],
            ['--cwd', 'relative'], ['--disk', '0Gi'], ['--lifetime', 'never'],
            ['--transfer-timeout', 'never'],
            ['--env', 'private-fixture'], ['--add-dir', '/nonexistent-cws-test-source'],
            ['--remote-path', 'relative'], ['--list', '--dry-run'],
            ['--agent', 'unknown'], ['--unknown-option', 'private-fixture'],
        ]
        for options in cases:
            with self.subTest(options=options), patch.object(agent, 'discover_resume') as discover:
                status, result = self.call_json(['resume', '--json', *options])
                self.assertEqual(status, 2)
                self.assertEqual(result['errors'][0]['code'], 'invalid_input')
                self.assertNotIn('private-fixture', json.dumps(result))
                discover.assert_not_called()

    def test_json_input_uses_resource_validation_and_shortcut_identity(self):
        cases = [{'agent': 'codex', 'list': True}, {'cpu': 'abc', 'list': True},
                 {'memory': '-1', 'list': True}, {'mode': 'other', 'list': True},
                 {'cwd': 'relative', 'list': True}, {'list': True, 'dry_run': True}]
        for data in cases:
            with self.subTest(data=data), patch.object(agent.sys, 'stdin', io.StringIO(json.dumps(data))), \
                    patch.object(agent, 'discover_resume') as discover:
                status, result = self.call_json(['claude', '--input-json', '-'])
                self.assertEqual(status, 2)
                self.assertEqual(result['errors'][0]['code'], 'invalid_input')
                discover.assert_not_called()
        with patch.object(agent.sys, 'stdin', io.StringIO('{"agent":"claude","list":true}')), \
                patch.object(agent, 'discover_resume', return_value=([], [])):
            self.assertEqual(self.call_json(['claude', '--input-json', '-'])[0], 0)

    def test_json_upload_sources_are_validated_as_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'project.txt').write_text('example')
            payload = json.dumps({'add_dir': [directory], 'list': True})
            with patch.object(agent.sys, 'stdin', io.StringIO(payload)), \
                    patch.object(agent, 'discover_resume', return_value=([], [])):
                status, result = self.call_json(['resume', '--input-json', '-'])
            self.assertEqual(status, 0)
            self.assertEqual(result['errors'], [])

    def test_human_listing_reports_each_discovery_error_once(self):
        message = 'Check workspace credentials and access'
        with patch.object(agent, 'discover_resume', return_value=([], [{'code': 'live_discovery_failed', 'message': message}])), \
                contextlib.redirect_stderr(io.StringIO()) as error, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(agent.main(['resume', '--list']), 2)
        self.assertEqual(error.getvalue().count(message), 1)

    def test_equivalent_cli_and_json_resources_do_not_conflict(self):
        with patch.object(agent.sys, 'stdin', io.StringIO('{"memory":"4096","list":true}')), \
                patch.object(agent, 'discover_resume', return_value=([], [])):
            status, result = self.call_json(['resume', '--memory', '4096', '--input-json', '-'])
        self.assertEqual(status, 0)
        self.assertEqual(result['errors'], [])

    def test_deep_json_input_returns_one_invalid_input_document(self):
        with patch.object(agent.sys, 'stdin', io.StringIO('[' * 3000 + '0' + ']' * 3000)), \
                patch.object(agent, 'discover_resume') as discover:
            status, result = self.call_json(['resume', '--input-json', '-'])
        self.assertEqual(status, 2)
        self.assertEqual(result['errors'][0]['code'], 'invalid_input')
        discover.assert_not_called()

    def test_parser_errors_with_json_input_also_return_one_document(self):
        with patch.object(agent, 'discover_resume') as discover:
            status, result = self.call_json(['resume', '--input-json', '-', '--cpu', 'abc'])
            self.assertEqual(status, 2)
            self.assertEqual(result['errors'][0]['code'], 'invalid_input')
            discover.assert_not_called()

    def test_runtime_errors_are_not_mislabeled_as_input_or_exposed(self):
        for error in (ValueError('private-fixture'), SystemExit('private-fixture')):
            with self.subTest(error=type(error)), patch.object(agent, 'discover_resume', side_effect=error):
                status, result = self.call_json(['resume', '--list', '--json'])
                self.assertEqual(status, 2)
                self.assertEqual(result['errors'][0]['code'], 'resume_failed')
                self.assertNotIn('private-fixture', json.dumps(result))

    def test_nonexistent_target_without_machine_mode_flag_returns_not_found(self):
        with patch.object(agent, 'discover_resume', return_value=([], [])), \
                patch.object(agent, 'restore_resume_row') as restore:
            status, result = self.call_json(['resume', 'missing', '--json'])
            self.assertEqual(status, 2)
            self.assertEqual(result['errors'][0]['code'], 'not_found')
            restore.assert_not_called()

    def test_existing_target_without_machine_mode_flag_never_allocates(self):
        row = agent.resume_row('saved', 'claude', 'box', conversation={'id': 'chat'})
        with patch.object(agent, 'discover_resume', return_value=([row], [])), \
                patch.object(agent, 'restore_resume_row') as restore:
            status, result = self.call_json(['resume', 'saved', '--json'])
            self.assertEqual(status, 2)
            self.assertEqual(result['errors'][0]['code'], 'terminal_required')
            self.assertEqual(len(result['rows']), 1)
            restore.assert_not_called()

    def test_invalid_remote_timestamps_serialize_as_null(self):
        for stamp in ('yesterday', {}, [], True, float('inf'), float('nan'), -1, 1e30):
            with self.subTest(stamp=stamp):
                row = agent.resume_row('example', 'claude', 'box')
                row.update(updated_at=stamp, saved_at=stamp)
                result = agent.public_resume_row(row)
                self.assertIsNone(result['updated_at'])
                self.assertIsNone(result['saved_at'])

    def test_schema_enumerates_agents_and_requires_transfer(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(agent.main(['schema', 'resume']), 0)
        schema = json.loads(output.getvalue())
        self.assertEqual(set(schema['input']['properties']['agent']['enum']), {*agent.HARNESSES, 'shell'})
        self.assertIn('transfer', schema['output']['required'])

    def test_human_dry_run_shows_conversation_and_action_without_json(self):
        row = agent.resume_row('example', 'claude', 'box', conversation={'id': 'chat', 'title': 'Fix parser'}, sb=object())
        with patch.object(agent, 'discover_resume', return_value=([row], [])), \
                patch.object(agent, 'restore_resume_row') as restore, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(agent.main(['resume', 'chat', '--dry-run']), 0)
        self.assertIn('Fix parser', output.getvalue())
        self.assertIn('Would resume', output.getvalue())
        self.assertNotIn('schema_version', output.getvalue())
        restore.assert_not_called()

    def test_generic_picker_vocabulary_and_wrapping(self):
        result = agent.resume_picker_lines([], 0, title='Upload conflicts', query='', warning='',
                                           columns=38, height=24, search_label='Filter', empty_label='No matching choices')
        text = ''.join(part for _, part in result)
        self.assertIn('Filter:', text)
        self.assertIn('No matching choices', text)
        self.assertNotIn('conversations', text)
        row = agent.resume_row('example', 'claude', 'box', conversation={'id': 'chat', 'title': 'a' * 500})
        result = agent.resume_picker_lines([row], 0, title='Resume', query='', warning='', columns=38, height=24)
        self.assertNotIn('……', ''.join(part for _, part in result))
