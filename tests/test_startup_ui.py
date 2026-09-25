"""Exercise terminal controls in memory; no cloud resources or local app UI."""
import contextlib
import io
import os
import types
import unittest
from unittest.mock import patch

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.data_structures import Size

import test_config_import

app = test_config_import.app
DOWN, UP, RIGHT, LEFT = "\x1b[B", "\x1b[A", "\x1b[C", "\x1b[D"


class RecordingOutput(DummyOutput):
    def __init__(self, columns=80):
        self.text = ""
        self.columns = columns

    def write(self, data):
        self.text += data

    def get_size(self):
        return Size(rows=24, columns=self.columns)


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            dict(id="skill:review", name="review", kind="skill", blocked="", bytes=42, files={}),
            dict(id="mcp:docs", name="docs", kind="mcp", blocked="", bytes=42),
            dict(id="mcp:unavailable", name="unavailable", kind="mcp", blocked="disabled locally", bytes=0),
        ]

    def choose(self, keys, columns=80):
        self.output = RecordingOutput(columns)
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=self.output):
            pipe.send_text(keys + "\x04")  # EOF also makes rejected-submit failures terminate.
            return app.import_checklist(self.items)

    def test_enter_accepts_both_collapsed_sections_without_blocked_items(self):
        self.assertEqual(self.choose("\r"), {"skill:review", "mcp:docs"})
        self.assertIn("Skills", self.output.text)
        self.assertIn("Tools (MCP)", self.output.text)
        self.assertNotIn("[x] review", self.output.text)

    def test_expand_toggle_item_and_collapse_preserves_selection(self):
        self.assertEqual(self.choose(RIGHT + DOWN + " " + LEFT + "\r"), {"mcp:docs"})

    def test_sections_toggle_independently_and_can_be_reselected(self):
        self.assertEqual(self.choose(" " + DOWN + "  " + "\r"), {"mcp:docs"})
        self.assertEqual(self.choose(" " + DOWN + " " + "\r"), set())

    def test_blocked_item_cannot_be_selected(self):
        self.assertEqual(self.choose(DOWN + " " + RIGHT + DOWN + DOWN + " " + "\r"), {"skill:review"})

    def test_skip_eof_and_cancel(self):
        for key in ("s", "\x04"):
            self.assertEqual(self.choose(key), set())
        with self.assertRaises(KeyboardInterrupt):
            self.choose("\x03")

    def test_oversize_submit_stays_open_and_allows_smaller_selection(self):
        self.items[0]["bytes"] = app.IMPORT_MAX_BYTES + 1
        self.assertEqual(self.choose("\r \r"), {"mcp:docs"})

    def test_file_limit_also_blocks_submit(self):
        self.items[0]["files"] = {str(n): "" for n in range(app.IMPORT_MAX_FILES + 1)}
        self.assertEqual(self.choose("\r \r"), {"mcp:docs"})

    def test_scroll_long_list_in_narrow_terminal(self):
        self.items = [dict(id=f"skill:item-{n}", name=f"item-{n}", kind="skill",
                           blocked="", bytes=1) for n in range(80)]
        chosen = self.choose(RIGHT + DOWN * 80 + " " + "\r", columns=40)
        self.assertEqual(len(chosen), 79)
        self.assertNotIn("skill:item-79", chosen)

    def test_no_color_preserves_keyboard_controls(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertEqual(self.choose(RIGHT + DOWN + " " + "\r"), {"mcp:docs"})


class StartupOutputTests(unittest.TestCase):
    def test_redirected_progress_and_summary_have_no_escapes(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with app.startup_step("Creating sandbox"):
                pass
            app.session_summary("Session ready", [("Name", "example"), ("Sandbox", "sb-example")])
        self.assertIn("Name: example", output.getvalue())
        self.assertIn("Sandbox: sb-example", output.getvalue())
        self.assertNotIn("\x1b", output.getvalue())

    def test_dumb_terminal_disables_live_display(self):
        with patch.dict(os.environ, {"TERM": "dumb"}):
            self.assertFalse(app.terminal_ui(types.SimpleNamespace(isatty=lambda: True)))

    def test_animated_step_reports_failure_and_restores_cursor(self):
        for error in (RuntimeError("example"), KeyboardInterrupt()):
            output = io.StringIO()
            with contextlib.redirect_stderr(output), patch.object(app, "terminal_ui", return_value=True), \
                    patch.dict(os.environ, {"TERM": "xterm", "FORCE_COLOR": "1"}):
                with self.assertRaises(type(error)):
                    with app.startup_step("Creating sandbox"):
                        raise error
            self.assertIn("interrupted or failed", output.getvalue())
            self.assertNotIn("✓", output.getvalue())
            self.assertIn("\x1b[?25h", output.getvalue())


if __name__ == "__main__":
    unittest.main()
