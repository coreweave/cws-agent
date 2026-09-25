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

    def choose(self, keys, columns=80, **kwargs):
        self.output = RecordingOutput(columns)
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=self.output):
            pipe.send_text(keys + "\x04")  # EOF also makes rejected-submit failures terminate.
            return app.import_checklist(self.items, **kwargs)

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

    def test_launch_cancellation_label_explains_cleanup(self):
        self.choose("s", cancel_label="abort launch (stops sandbox)")
        self.assertIn("abort launch (stops sandbox)", self.output.text)

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
    def test_spinner_preserves_stdout_even_when_stderr_is_a_terminal(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch.object(stderr, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM": "xterm"}):
            with app.startup_step("Working"):
                print("command output")
        self.assertEqual(stdout.getvalue(), "command output\n")
        self.assertNotIn("command output", stderr.getvalue())

    def test_bootstrap_failure_keeps_script_output_on_its_original_stream(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        result = types.SimpleNamespace(returncode=1, stdout="script output", stderr="script error")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch.object(stderr, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM": "xterm"}), \
                patch.object(app, "exec_retry", return_value=result), \
                self.assertRaises(SystemExit):
            app.run_bootstrap(object(), app.HARNESSES["claude"], None)
        self.assertEqual(stdout.getvalue(), "  script output\n")
        self.assertIn("script error", stderr.getvalue())
        self.assertNotIn("script output", stderr.getvalue())
        self.assertNotIn("✓", stderr.getvalue())

    def test_sandbox_id_is_visible_before_bootstrap_failure(self):
        output = io.StringIO()
        sb = types.SimpleNamespace(sandbox_id="sb-example")
        def fail(*args):
            self.assertIn("sb-example", output.getvalue())
            raise RuntimeError("bootstrap failure")
        with contextlib.redirect_stderr(output), \
                patch.object(output, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM": "xterm"}), \
                patch.object(app, "create_session_sandbox", return_value=sb), \
                patch.object(app, "run_bootstrap", side_effect=fail), \
                patch.object(app, "stop_failed_sandbox") as stop, \
                self.assertRaises(RuntimeError):
            app.provision_session(name="example", harness=app.HARNESSES["claude"], repo_url=None)
        stop.assert_called_once_with(sb)
        self.assertIn("[claude]", output.getvalue())

    def test_launch_summary_follows_snapshot_and_auth_notes(self):
        output = io.StringIO()
        sb = types.SimpleNamespace(sandbox_id="sb-example")
        with contextlib.redirect_stdout(output), \
                patch.object(app, "find_active", return_value=None), \
                patch.object(app, "build_env", return_value={}), \
                patch.object(app, "scan_local_dir", return_value=app.LocalDirectoryInventory("/project", [], 0, 0, set())), \
                patch.object(app, "provision_session", return_value=sb), \
                patch.object(app, "sync_local_dir"), \
                patch.object(app, "take_snapshot", side_effect=lambda *a: print("snapshot finished") or "fss-example"):
            self.assertEqual(app.main(["claude", "example", "--local-dir", "/project", "--detach"]), 0)
        text = output.getvalue()
        self.assertLess(text.index("snapshot finished"), text.index("Session ready"))
        self.assertLess(text.index("note: no Claude token"), text.index("Session ready"))

    def test_checklist_abort_cleans_up_new_sandbox_and_labels_that_action(self):
        sb = types.SimpleNamespace(sandbox_id="sb-example")
        with contextlib.redirect_stdout(io.StringIO()), \
                patch.object(app, "find_active", return_value=None), \
                patch.object(app, "build_env", return_value={}), \
                patch.object(app, "provision_session", return_value=sb), \
                patch.object(app, "sync_agent_config", side_effect=KeyboardInterrupt()) as sync, \
                patch.object(app, "stop_failed_sandbox") as stop:
            self.assertEqual(app.main(["claude", "example"]), 130)
        self.assertEqual(sync.call_args.kwargs["cancel_label"], "abort launch (stops sandbox)")
        stop.assert_called_once_with(sb)

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
