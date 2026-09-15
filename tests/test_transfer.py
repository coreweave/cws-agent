import base64
import copy
import json
import inspect
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from test_sessions import cli


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / "source"
        self.dest = self.root / "dest"
        self.source.mkdir()
        self.dest.mkdir()

    def write(self, rel, data):
        path = self.source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def claude_bundle(self):
        image = {"type": "image", "source": {"type": "base64", "data": "abc"}}
        data = {"sessionId": "abc", "cwd": "/local/project", "message": {"content": [image]}}
        self.write(".claude/projects/-local-project/abc.jsonl", (json.dumps(data) + "\n").encode())
        row = cli.scan_native_history(str(self.source))[0]
        return cli.build_history_bundle(row, str(self.source), "/workspace/project")

    def test_claude_transfers_subagent_history_assets_and_inline_images(self):
        self.write(".claude/projects/-local-project/abc/subagents/agent-child.jsonl", b'{"sessionId":"abc","cwd":"/local/project"}\n')
        self.write(".claude/projects/-local-project/abc/images/example.png", b"image")
        self.write(".claude/file-history/abc/snapshot", b"original")
        bundle = self.claude_bundle()
        self.assertEqual(len(bundle["files"]), 4)
        self.assertEqual(cli.install_history_bundle(bundle, str(self.dest)), [])
        row = cli.scan_native_history(str(self.dest))[0]
        self.assertEqual(row["cwd"], "/workspace/project")
        self.assertIn('"data": "abc"', Path(row["path"]).read_text())
        self.assertEqual((self.dest / ".claude/file-history/abc/snapshot").read_bytes(), b"original")

    def test_codex_roundtrip_changes_only_structured_cwd(self):
        first = {"type": "session_meta", "payload": {"id": "abc", "cwd": "/local/project"}}
        second = {"type": "response_item", "payload": {"text": "/local/project is in my prompt"}}
        self.write(".codex/sessions/2026/09/05/rollout-date-abc.jsonl", (json.dumps(first) + "\n" + json.dumps(second) + "\n").encode())
        row = cli.scan_native_history(str(self.source))[0]
        bundle = cli.build_history_bundle(row, str(self.source), "/workspace/project")
        cli.install_history_bundle(bundle, str(self.dest))
        imported = cli.scan_native_history(str(self.dest))[0]
        self.assertEqual(imported["cwd"], "/workspace/project")
        self.assertIn("/local/project is in my prompt", Path(imported["path"]).read_text())
        back = cli.build_history_bundle(imported, str(self.dest), "/local/project")
        with self.assertRaisesRegex(ValueError, "destination exists"):
            cli.install_history_bundle(back, str(self.source))
        backups = cli.install_history_bundle(back, str(self.source), replace=True)
        self.assertEqual(len(backups), 1)
        self.assertTrue(Path(backups[0]).is_file())

    def test_codex_remaps_workspace_roots_and_world_state_only(self):
        records = [
            {"type": "session_meta", "payload": {"id": "abc", "cwd": "/local/project"}},
            {"type": "turn_context", "payload": {"cwd": "/local/project",
                                                 "workspace_roots": ["/local/project", "/local/other"]}},
            {"type": "world_state", "payload": {"full": True, "state": {
                "environments": {"environments": {"local": {"cwd": "/local/project", "shell": "zsh"}}},
                "roots": ["/local/project"], "note": "/local/project/sub"}}},
            {"type": "response_item", "payload": {"type": "message", "content": [
                {"type": "input_text", "text": "look at /local/project"}], "path": "/local/project"}},
        ]
        self.write(".codex/sessions/2026/09/05/rollout-date-abc.jsonl",
                   ("\n".join(json.dumps(r) for r in records) + "\n").encode())
        row = cli.scan_native_history(str(self.source))[0]
        bundle = cli.build_history_bundle(row, str(self.source), "/workspace/project")
        out = [json.loads(line) for line in base64.b64decode(bundle["files"][0]["data"]).decode().splitlines()]
        self.assertEqual(out[0]["payload"]["cwd"], "/workspace/project")
        self.assertEqual(out[1]["payload"]["cwd"], "/workspace/project")
        self.assertEqual(out[1]["payload"]["workspace_roots"], ["/workspace/project", "/local/other"])
        state = out[2]["payload"]["state"]
        self.assertEqual(state["environments"]["environments"]["local"]["cwd"], "/workspace/project")
        self.assertEqual(state["roots"], ["/workspace/project"])
        self.assertEqual(state["note"], "/local/project/sub")
        self.assertEqual(out[3], records[3])

    def test_no_clobber_and_preflight_prevents_partial_writes(self):
        bundle = self.claude_bundle()
        bundle["files"].append({"path": "../../.ssh/authorized_keys", "data": "eA=="})
        with self.assertRaisesRegex(ValueError, "unsafe bundle path"):
            cli.install_history_bundle(bundle, str(self.dest))
        self.assertEqual(list(self.dest.rglob("*")), [])

    def test_rejects_credentials_scope_and_duplicate_paths(self):
        for rel in ("settings.json", "/tmp/evil", "projects/-workspace-project/other.jsonl", "projects/-workspace-project/abc/../../settings.json"):
            bundle = self.claude_bundle()
            bundle["files"][0]["path"] = rel
            with self.assertRaises(ValueError):
                cli.install_history_bundle(bundle, str(self.dest))
        bundle = self.claude_bundle()
        bundle["files"].append(copy.deepcopy(bundle["files"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            cli.install_history_bundle(bundle, str(self.dest))

    def test_rejects_symlink_source_and_destination(self):
        bundle = self.claude_bundle()
        self.write("outside", b"sensitive")
        linked = self.source / ".claude/projects/-local-project/abc/subagents"
        linked.parent.mkdir()
        linked.symlink_to(self.source, target_is_directory=True)
        row = cli.scan_native_history(str(self.source))[0]
        with self.assertRaisesRegex(ValueError, "symlink"):
            cli.build_history_bundle(row, str(self.source), "/workspace/project")
        directory = self.dest / ".claude/projects"
        directory.mkdir(parents=True)
        (directory / "-workspace-project").symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "symlink"):
            cli.install_history_bundle(bundle, str(self.dest))

    def test_file_count_and_size_limits(self):
        bundle = self.claude_bundle()
        bundle["files"] *= 257
        with self.assertRaisesRegex(ValueError, "file count"):
            cli.install_history_bundle(bundle, str(self.dest))
        bundle = self.claude_bundle()
        bundle["files"][0]["data"] = "A" * ((45 << 20) + 1)
        with self.assertRaisesRegex(ValueError, "size limit"):
            cli.install_history_bundle(bundle, str(self.dest))

    def test_rejects_partial_jsonl_before_export(self):
        self.claude_bundle()
        path = self.source / ".claude/projects/-local-project/abc.jsonl"
        with path.open("ab") as stream:
            stream.write(b'{"partial":')
        row = cli.scan_native_history(str(self.source))[0]
        with self.assertRaises(ValueError):
            cli.build_history_bundle(row, str(self.source), "/workspace/project")

    def test_remote_serialized_helpers_work_in_clean_python_process(self):
        bundle = self.claude_bundle()
        script = ("from __future__ import annotations\n" + inspect.getsource(cli.install_history_bundle)
                  + "\nimport json, sys\ninstall_history_bundle(json.load(sys.stdin), sys.argv[1])")
        result = subprocess.run([sys.executable, "-c", script, str(self.dest)],
                                input=json.dumps(bundle), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = cli.scan_native_history(str(self.dest))[0]
        export = ("from __future__ import annotations\n" + inspect.getsource(cli.build_history_bundle)
                  + "\nimport json, sys\nprint(json.dumps(build_history_bundle(json.load(sys.stdin), sys.argv[1], '/back')))" )
        result = subprocess.run([sys.executable, "-c", export, str(self.dest)],
                                input=json.dumps(row), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["cwd"], "/back")

    def test_bad_native_header_and_duplicate_id_are_rejected(self):
        bundle = self.claude_bundle()
        invalid = copy.deepcopy(bundle)
        invalid["files"][0]["data"] = base64.b64encode(b'{"sessionId":"other","cwd":"/workspace/project"}\n').decode()
        with self.assertRaisesRegex(ValueError, "identity"):
            cli.install_history_bundle(invalid, str(self.dest))
        existing = self.dest / ".claude/projects/-other/abc.jsonl"
        existing.parent.mkdir(parents=True)
        existing.write_text("original")
        with self.assertRaisesRegex(ValueError, "different destination"):
            cli.install_history_bundle(bundle, str(self.dest), replace=True)
        self.assertEqual(existing.read_text(), "original")

    def test_upload_conflict_reports_remote_error_without_traceback(self):
        args = types.SimpleNamespace(name="dev1", agent=None, upload="abc", download=None, cwd=None, replace=False)
        row = {"agent": "claude", "id": "abc", "cwd": "/local/project", "path": "/unused"}
        traceback_text = ("Traceback (most recent call last):\n"
                          '  File "<string>", line 47, in install_history_bundle\n'
                          "ValueError: destination exists; use --replace to retain a backup and replace history\n")

        def execute(sb, command, **kwargs):
            if command[0] == "mktemp":
                return types.SimpleNamespace(returncode=0, stdout="/tmp/cws-history-abcd1234\n", stderr="")
            if command[0] == "python3":
                return types.SimpleNamespace(returncode=1, stdout="", stderr=traceback_text)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        sandbox = types.SimpleNamespace(write_file=lambda path, data: types.SimpleNamespace(result=lambda: None))
        with patch.object(cli, "require_active", return_value=sandbox), \
                patch.object(cli, "scan_native_history", return_value=[row]), \
                patch.object(cli, "build_history_bundle", return_value={"files": []}), \
                patch.object(cli, "upload_history_payload"), \
                patch.object(cli, "exec_retry", side_effect=execute), \
                patch("sys.stdout"):
            with self.assertRaises(SystemExit) as caught:
                cli.cmd_session_transfer(args)
        self.assertEqual(str(caught.exception),
                         "error: destination exists; use --replace to retain a backup and replace history")

    def test_remote_failure_message_falls_back_to_last_stderr_line(self):
        self.assertEqual(cli.remote_failure_message("python3: command not found\n", "remote import failed"),
                         "python3: command not found")
        self.assertEqual(cli.remote_failure_message("", "remote import failed"), "remote import failed")
        self.assertEqual(cli.remote_failure_message("Traceback (most recent call last):\n  x\nRuntimeError: boom\n", "f"), "boom")

    def test_rollback_restores_old_files_after_later_install_failure(self):
        bundle = self.claude_bundle()
        cli.install_history_bundle(bundle, str(self.dest))
        main = self.dest / ".claude/projects/-workspace-project/abc.jsonl"
        original = main.read_bytes()
        replacement = b'{"sessionId":"abc","cwd":"/workspace/project","message":"replacement"}\n'
        bundle["files"][0]["data"] = base64.b64encode(replacement).decode()
        bundle["files"].append({"path": "projects/-workspace-project/abc/asset", "data": "eA=="})
        import os
        original_link = os.link

        def fail_second(source, dest, **kwargs):
            if str(dest).endswith("/asset"):
                raise OSError("simulated disk failure")
            return original_link(source, dest, **kwargs)

        with patch("os.link", side_effect=fail_second), self.assertRaises(OSError):
            cli.install_history_bundle(bundle, str(self.dest), replace=True)
        self.assertEqual(main.read_bytes(), original)
        self.assertFalse(list(self.dest.rglob("*.cws-backup-*")))


if __name__ == "__main__":
    unittest.main()
