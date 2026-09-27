import json
import tempfile
import unittest
from pathlib import Path
from test_sessions import cli


class PreviewTests(unittest.TestCase):
    def test_claude_preview_ignores_meta_and_reads_tail(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / '.claude/projects/project/abc.jsonl'
            path.parent.mkdir(parents=True)
            def row(text, **extra):
                return dict(sessionId='abc', cwd='/workspace/project', type='user',
                            message={'role': 'user', 'content': text}, **extra)
            records = [row('injected', isMeta=True), row('<system-reminder>hidden</system-reminder>Fix login', timestamp='2026-09-01T10:00:00Z')]
            records += [dict(type='tool', padding='x'*1000)] * 300
            records += [row('Done\x1b[31m', timestamp='2026-09-02T10:00:00Z')]
            path.write_text('\n'.join(map(json.dumps, records))+'\n{partial')
            found = cli.scan_native_history(root)[0]
            self.assertEqual(found['title'], 'Fix login')
            self.assertNotIn('\x1b', found['excerpt'])
            self.assertEqual(found['updated_source'], 'message')
            self.assertEqual(found['updated_at'], 1788343200.0)

    def test_codex_first_user_and_unknown_activity(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / '.codex/sessions/rollout-test.jsonl'
            path.parent.mkdir(parents=True)
            path.write_text('\n'.join(map(json.dumps, [
                {'type':'session_meta', 'payload':{'id':'abc','cwd':'/workspace/project'}},
                {'type':'response_item','payload':{'role':'user','content':[{'type':'input_text','text':'Review the parser'}]}},
            ]))+'\n')
            found = cli.scan_native_history(root)[0]
            self.assertEqual(found['title'], 'Review the parser')
            self.assertEqual(found['updated_source'], 'file_mtime')

    def test_long_real_messages_remain_available_to_the_expanded_preview(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / '.claude/projects/project/abc.jsonl'
            path.parent.mkdir(parents=True)
            prompt = 'Review the interaction between restore and native history. ' * 5
            answer = 'Preserve the existing conversation before attaching. ' * 12
            path.write_text('\n'.join(json.dumps({
                'sessionId':'abc', 'cwd':'/workspace/project', 'type':role,
                'message':{'role':role,'content':text},
            }) for role, text in [('user',prompt),('assistant',answer)])+'\n')
            row = cli.scan_native_history(root)[0]
            self.assertEqual(row['title'], prompt.strip())
            self.assertEqual(row['excerpt'], answer.strip())
