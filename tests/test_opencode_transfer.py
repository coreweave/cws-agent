"""Native OpenCode export/import contracts, without credentials or cloud resources."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from test_terminal import agent


SID = "ses_cwsoffline123"


def export_data(cwd="/source"):
    return {"info": {"id": SID, "directory": cwd}, "messages": [
        {"info": {"id": "msg_example", "sessionID": SID, "role": "user"},
         "parts": [{"id": "prt_example", "sessionID": SID, "messageID": "msg_example",
                    "type": "text", "text": "Preserve /source literally — do not rewrite"}]}]}


class OpenCodeTransferTests(unittest.TestCase):
    def native(self, responses):
        def run(argv, **kwargs):
            expected, code, output, errors = responses.pop(0)
            self.assertEqual(argv[1:], expected)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            kwargs["stdout"].write(output)
            kwargs["stderr"].write(errors)
            return types.SimpleNamespace(returncode=code)
        return patch("subprocess.run", side_effect=run)

    def test_validation_rejects_wrong_ids_and_foreign_parts(self):
        original = export_data()
        self.assertEqual(agent.validate_opencode_export(json.dumps(original).encode(), SID), original)
        for change in (lambda d: d["info"].update(id="ses_else"),
                       lambda d: d["messages"][0]["info"].update(sessionID="ses_else"),
                       lambda d: d["messages"][0]["parts"][0].update(messageID="msg_else")):
            data = copy.deepcopy(original)
            change(data)
            with self.assertRaises(ValueError):
                agent.validate_opencode_export(json.dumps(data).encode(), SID)

    def test_invalid_ids_rejected_before_cloud_access(self):
        with patch.object(agent, "require_active") as active:
            with self.assertRaisesRegex(SystemExit, "invalid OpenCode"):
                agent.transfer_opencode_session(types.SimpleNamespace(upload="--help", download=None))
            active.assert_not_called()

    def test_export_spools_ascii_payload_without_rewriting_transcript(self):
        responses = [(["export", SID], 0, json.dumps(export_data(), ensure_ascii=False).encode(), b"")]
        with tempfile.TemporaryDirectory() as tmp, patch("shutil.which", return_value="/fake/opencode"), self.native(responses):
            path = Path(tmp) / "export.json"
            result = agent.opencode_history_file("export", SID, tmp, str(path))
            self.assertEqual(result, {"id": SID, "messages": 1})
            self.assertEqual(json.loads(path.read_bytes()), export_data())
            self.assertTrue(path.read_bytes().isascii())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_target_rejected_without_import(self):
        responses = [(["export", SID], 0, b"{}", b"")]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), patch("shutil.which", return_value="/fake/opencode"), self.native(responses):
            path = Path(tmp) / "export.json"
            path.write_text(json.dumps(export_data()))
            with self.assertRaisesRegex(ValueError, "already exists.*--replace"):
                agent.opencode_history_file("import", SID, tmp, str(path))
        self.assertEqual(responses, [])

    def test_import_native_relocates_metadata_preserves_all_messages(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), patch("shutil.which", return_value="/fake/opencode"):
            path = Path(tmp) / "export.json"
            path.write_text(json.dumps(export_data()))
            responses = [
                (["export", SID], 1, b"", ("Session not found: " + SID).encode()),
                (["session", "list", "--format", "json"], 0, b"[]", b""),
                (["import", str(path)], 0, b"Imported", b""),
                (["export", SID], 0, json.dumps(export_data(tmp)).encode(), b""),
            ]
            with self.native(responses):
                self.assertEqual(agent.opencode_history_file("import", SID, tmp, str(path))["messages"], 1)
            self.assertEqual(json.loads(path.read_text()), export_data())
        self.assertEqual(responses, [])

    def test_failed_verification_is_uncertain_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), patch("shutil.which", return_value="/fake/opencode"):
            path = Path(tmp) / "export.json"
            path.write_text(json.dumps(export_data()))
            responses = [
                (["export", SID], 1, b"", ("Session not found: " + SID).encode()),
                (["session", "list", "--format", "json"], 0, b"", b""),
                (["import", str(path)], 0, b"Imported", b""),
                (["export", SID], 0, json.dumps(export_data("/wrong")).encode(), b""),
            ]
            with self.native(responses), self.assertRaisesRegex(RuntimeError, "may be partial.*No automatic replay"):
                agent.opencode_history_file("import", SID, tmp, str(path))
        self.assertEqual(responses, [])

    def test_unknown_destination_failure_does_not_import(self):
        responses = [(["export", SID], 1, b"", b"network or database failure")]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), patch("shutil.which", return_value="/fake/opencode"), self.native(responses):
            path = Path(tmp) / "export.json"
            path.write_text(json.dumps(export_data()))
            with self.assertRaisesRegex(ValueError, "no import attempted"):
                agent.opencode_history_file("import", SID, tmp, str(path))

    def test_dispatch_requires_explicit_agent(self):
        args = types.SimpleNamespace(agent="opencode", upload=SID)
        with patch.object(agent, "transfer_opencode_session", return_value=0) as transfer:
            self.assertEqual(agent.cmd_session_transfer(args), 0)
        transfer.assert_called_once_with(args)


if __name__ == "__main__":
    unittest.main()
