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
