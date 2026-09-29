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


class ResumePickerTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(workspace='alpha',agent='claude',sandbox_id='box-a',session_id='chat-a',title='Fix parser',resumable=True),
                     dict(workspace='beta',agent='codex',sandbox_id='box-b',session_id='chat-b',title='Write tests',resumable=True)]

    def pick(self, keys, rows=None):
        with create_pipe_input() as source, create_app_session(input=source,output=DummyOutput()):
            source.send_text(keys)
            return agent.resume_picker(rows or self.rows)

    def test_arrows_enter_filter_and_escape(self):
        self.assertEqual(self.pick('\x1b[B\r')['session_id'],'chat-b')
        self.assertEqual(self.pick('parser\r')['session_id'],'chat-a')
        self.assertIsNone(self.pick('\x03'))

    def test_unavailable_row_is_focusable_but_enter_does_not_accept(self):
        blocked = {**self.rows[0], 'resumable':False, 'reason':'Resume unavailable'}
        selected = self.pick('\r\x1b[B\r',[blocked,self.rows[1]])
        self.assertEqual(selected['session_id'],'chat-b')

    def test_no_color_and_rich_markup_are_inert(self):
        with patch.dict('os.environ',{'NO_COLOR':''}):
            self.assertFalse(agent.resume_style().style_rules)
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            agent.cli_table(('Conversation',),[('[red]literal[/red]\x1b[31m',)])
        self.assertIn('[red]literal[/red]',output.getvalue())
        self.assertNotIn('\x1b',output.getvalue())

    def test_layout_gives_conversation_full_width_and_fits_unicode(self):
        from prompt_toolkit.utils import get_cwidth
        row = {**self.rows[0], "title": "A" * 200,
               "workspace": "工作区-demo", "excerpt": "Details about the conversation"}
        for columns in (40, 80, 140):
            with self.subTest(columns=columns):
                result = agent.resume_picker_lines([row], 0, title="Resume", query="", warning="",
                                                   columns=columns, height=24)
                lines = [text.rstrip("\n") for _, text in result]
                selected = next(line for line in lines if line.startswith("› "))
                self.assertGreaterEqual(selected.count("A"), columns - 5)
                self.assertTrue(all(get_cwidth(line) <= columns - 1 for line in lines))
                self.assertIn("工作区-demo", "\n".join(lines))
                self.assertLessEqual(len(lines), 24)

    def test_short_terminal_keeps_focused_row_and_expanded_excerpt_visible(self):
        rows = [{**self.rows[0], "title": f"Conversation {i}", "excerpt": "A detailed final response " * 30}
                for i in range(30)]
        result = agent.resume_picker_lines(rows, 29, title="Resume", query="", warning="",
                                           columns=80, height=24, expanded=True)
        text = "".join(value for _, value in result)
        self.assertIn("› Conversation 29", text)
        self.assertGreater(text.count("A detailed final response"), 5)
        self.assertLessEqual(len(result), 24)

    def test_detail_toggle_keeps_selection_and_unavailable_reason(self):
        self.assertEqual(self.pick('\x0f\x1b[B\r')['session_id'], 'chat-b')
        row = {**self.rows[0], "resumable":False, "reason":"Resume unavailable: see Cloud Runner docs"}
        result = agent.resume_picker_lines([row], 0, title="Resume", query="", warning="",
                                           columns=80, height=24)
        self.assertIn(row["reason"], "".join(text for _, text in result))
