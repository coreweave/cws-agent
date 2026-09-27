import contextlib
import io
import json
import types
import unittest
from unittest.mock import patch
from test_terminal import agent


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(agent, 'sandbox_auth', return_value='offline'))
        self.platform = self.enterContext(patch.object(agent, 'Sandbox'))
        self.platform.list.return_value.result.return_value = []
        self.platform.list_snapshots.return_value.result.return_value = []
        self.catalog = self.enterContext(patch.object(agent, 'read_workspace_catalog', return_value=[]))
        self.enterContext(patch.object(agent, 'update_workspace_catalog'))
        self.enterContext(patch.object(agent, 'read_workspace_document', return_value={}))
        self.enterContext(patch.object(agent, 'probe_session_meta', return_value=('example','claude')))
        self.history = self.enterContext(patch.object(agent, 'remote_native_history', return_value=[]))

    def snap(self, identifier, stamp=123, **kwargs):
        return types.SimpleNamespace(file_system_snapshot_id=identifier, source_sandbox_id='box-example',
                                     status='ready', request_id=f'cwsa1|example|claude|{stamp}', **kwargs)

    def test_unrelated_snapshots_excluded_and_one_list_call(self):
        unrelated = self.snap('unrelated'); unrelated.request_id='external'
        self.platform.list_snapshots.return_value.result.return_value = [self.snap('saved'), unrelated]
        rows, errors = agent.discover_resume()
        self.assertFalse(errors)
        self.assertEqual([r['snapshot_id'] for r in rows], ['saved'])
        self.platform.list_snapshots.assert_called_once_with(status='ready', auth='offline')

    def test_known_live_source_suppresses_snapshot_even_if_history_fails(self):
        box = types.SimpleNamespace(sandbox_id='box-example',status=types.SimpleNamespace(value='running'))
        self.platform.list.return_value.result.return_value = [box]
        self.platform.list_snapshots.return_value.result.return_value = [self.snap('saved')]
        self.history.side_effect = RuntimeError('unavailable')
        rows, errors = agent.discover_resume()
        self.assertFalse(any(r['state']=='saved' for r in rows))
        self.assertEqual(errors[0]['code'], 'workspace_discovery_failed')

    def test_indexed_and_unindexed_snapshots_choose_newest(self):
        self.platform.list_snapshots.return_value.result.return_value = [self.snap('older'),self.snap('newer',124)]
        self.catalog.return_value = [{'snapshot_id':'newer','sandbox_id':'box-example','id':'a'*32,
                                      'name':'example','agent':'claude','config':{},'conversations':[]}]
        rows, _ = agent.discover_resume()
        self.assertEqual([r['snapshot_id'] for r in rows], ['newer'])

    def test_remote_metadata_cannot_expand_client_credential_authorization(self):
        sb = types.SimpleNamespace(sandbox_id='box-example')
        trusted = {'sandbox_id':sb.sandbox_id,'config':{'image':'python:3.11','env_names':['EXAMPLE']}}
        self.catalog.return_value = [trusted]
        forged = {'workspace':{'id':'a'*32,'name':'example','agent':'claude','sandbox_ids':[],
                             'config':{'env_names':['CWSANDBOX_API_KEY'],'secrets':['OTHER_SECRET']}}}
        with patch.object(agent,'read_workspace_document',return_value=forged):
            record = agent.snapshot_catalog_record(sb,'example','claude')
        self.assertEqual(record['config'],trusted['config'])

    def test_scoped_discovery_uses_name_filter_and_opencode_directory(self):
        box = types.SimpleNamespace(sandbox_id='box-example',status=types.SimpleNamespace(value='running'))
        self.platform.list.return_value.result.return_value = [box]
        agent.discover_resume(name='example', opencode_cwd='/workspace/other')
        self.platform.list.assert_called_once_with(tags=[agent.SESSION_TAG,agent.name_tag('example')],status='running',auth='offline')
        self.history.assert_called_once_with(box,opencode_cwd='/workspace/other')

class UntrustedDiscoveryTests(unittest.TestCase):
    def test_remote_metadata_cannot_overwrite_or_hide_saved_workspace(self):
        import tempfile
        from pathlib import Path
        sb=types.SimpleNamespace(sandbox_id='live-box',status=types.SimpleNamespace(value='running'))
        snapshot=types.SimpleNamespace(file_system_snapshot_id='saved-snapshot',source_sandbox_id='saved-box',
                                      request_id='cwsa1|saved|claude|123',status='ready')
        forged={'id':'f'*32,'sandbox_ids':['saved-box'],'name':'live','agent':'claude','config':{},
                'snapshot_id':'saved-snapshot','conversations':[{'id':'forged'}]}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(agent,'catalog_directory',return_value=Path(directory)), \
                patch.object(agent,'Sandbox') as platform, \
                patch.object(agent,'sandbox_auth',return_value='offline'), \
                patch.object(agent,'probe_session_meta',return_value=('live','claude')), \
                patch.object(agent,'read_workspace_document',return_value={'workspace':forged}), \
                patch.object(agent,'remote_native_history',return_value=[]):
            agent.update_workspace_catalog({'id':'f'*32,'sandbox_id':'saved-box','snapshot_id':'saved-snapshot',
                                            'name':'saved','agent':'claude','conversations':[]})
            platform.list.return_value.result.return_value=[sb]
            platform.list_snapshots.return_value.result.return_value=[snapshot]
            rows,errors=agent.discover_resume()
            self.assertFalse(errors)
            self.assertEqual({row['workspace'] for row in rows},{'live','saved'})
            record=next(r for r in agent.read_workspace_catalog() if r.get('snapshot_id')=='saved-snapshot')
            self.assertEqual(record['sandbox_id'],'saved-box')
            self.assertEqual(record['conversations'],[])

    def test_untrusted_activity_never_breaks_sort_or_human_display(self):
        for stamp in ('bad',[],{},True,float('nan'),float('inf'),10**300,-1):
            with self.subTest(stamp=stamp):
                row=agent.resume_row('example','claude','box-example',sb=object(),
                    conversation={'id':'chat-example','updated_at':stamp,'updated_source':'message'})
                self.assertIsNone(row['updated_at'])
                self.assertEqual(row['updated_source'],'unknown')
                self.assertEqual(agent.updated_label(row),'—')
                with contextlib.redirect_stdout(io.StringIO()):
                    agent.render_resume_table([row])
