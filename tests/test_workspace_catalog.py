"""Workspace identity and saved-state bookkeeping, without cloud access."""
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_terminal import agent


class WorkspaceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(agent, "catalog_directory", return_value=Path(self.tmp) / "catalog"))

    def test_restore_keeps_identity_and_records_replacement(self):
        previous = {"id": "a" * 32, "name": "dev1", "agent": "codex",
                    "config": {"image": "example.test/old"}, "sandbox_ids": ["old"]}
        self_agent_record = {**previous, "sandbox_id": "old", "snapshot_id": "snap-example"}
        agent.update_workspace_catalog(self_agent_record)
        sb = types.SimpleNamespace(sandbox_id="new")
        with patch.object(agent, "read_workspace_document", return_value={"version": 2, "workspace": previous}), \
                patch.object(agent, "write_workspace_document") as write:
            value = agent.save_workspace_metadata(sb, "dev1", "codex", {"image": "example.test/new"}, restore_snapshot_id="snap-example")
        self.assertEqual(value["id"], previous["id"])
        self.assertEqual(value["sandbox_ids"], ["old", "new"])
        self.assertEqual(write.call_args.args[1]["workspace"], value)
        self.assertTrue(any(record["sandbox_id"] == "new" for record in agent.read_workspace_catalog()))

    def test_existing_worker_configuration_survives_workspace_save(self):
        document = {"version": 1, "kind": "claude", "target": "env_example", "workers": 2}
        with patch.object(agent, "read_workspace_document", return_value=document.copy()), \
                patch.object(agent, "write_workspace_document") as write:
            agent.save_workspace_metadata(types.SimpleNamespace(sandbox_id="new"), "dev1", "ant", {})
        for key, value in document.items():
            self.assertEqual(write.call_args.args[1][key], value)

    def test_cli_metadata_is_not_a_worker(self):
        document = {"version": 2, "workspace": {"id": "a" * 32, "name": "dev1",
                     "agent": "shell", "config": {}}}
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps(document))
        with patch.object(agent, "exec_retry", return_value=result):
            self.assertIsNone(agent.read_backend_config(object()))

    def test_catalog_has_separate_live_and_saved_records_and_private_permissions(self):
        agent.update_workspace_catalog({"sandbox_id": "sb-example", "conversations": []})
        agent.update_workspace_catalog({"sandbox_id": "sb-example", "snapshot_id": "snap-example",
                                        "conversations": [{"id": "chat-example"}]})
        self.assertEqual(len(agent.read_workspace_catalog()), 2)
        for file in agent.catalog_directory().glob("*.json"):
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_failed_snapshot_cannot_commit_new_coverage(self):
        with patch.object(agent, "snapshot_catalog_record", return_value={"sandbox_id": "sb"}), \
                patch.object(agent, "capture_workspace_snapshot", side_effect=RuntimeError("failed")), \
                patch.object(agent, "update_workspace_catalog") as update:
            with self.assertRaises(RuntimeError):
                agent.take_snapshot(object(), "dev1", "claude")
        update.assert_not_called()

    def test_index_failure_does_not_prevent_backup(self):
        with patch.object(agent, "snapshot_catalog_record", return_value=None), \
                patch.object(agent, "capture_workspace_snapshot", return_value="snap-example"):
            self.assertEqual(agent.take_snapshot(object(), "dev1", "shell"), "snap-example")

    def test_remote_document_write_needs_no_python(self):
        sb = Mock()
        with patch.object(agent, "exec_retry", return_value=types.SimpleNamespace(returncode=0)) as execute:
            agent.write_workspace_document(sb, {"version": 2})
        self.assertNotIn("python", repr(execute.call_args_list))
        self.assertIn("mv -f", execute.call_args_list[0].args[1][2])
        self.assertEqual(json.loads(sb.write_file.call_args.args[1]), {"version": 2})

    def test_invalid_or_symlinked_catalog_records_are_ignored(self):
        root = agent.catalog_directory()
        root.mkdir()
        (root / "invalid.json").write_text("{")
        (root / "outside").write_text('{"sandbox_id":"untrusted"}')
        (root / "link.json").symlink_to(root / "outside")
        self.assertEqual(agent.read_workspace_catalog(), [])

    def test_snapshot_index_uses_only_client_authorized_configuration(self):
        sb = types.SimpleNamespace(sandbox_id="sb-example")
        forged = {"workspace": {"id": "a" * 32, "name": "dev1", "agent": "claude",
                  "sandbox_ids": [], "config": {"env_names": ["UNAPPROVED_TOKEN"],
                                                "secrets": ["unapproved-secret"]}}}
        trusted = {"image": "example.invalid/agent:latest", "env_names": ["EXAMPLE"]}
        for records, expected in (([], None), ([{"sandbox_id": sb.sandbox_id, "config": trusted}], trusted)):
            with self.subTest(records=records), \
                    patch.object(agent, "read_workspace_document", return_value=forged), \
                    patch.object(agent, "read_workspace_catalog", return_value=records), \
                    patch.object(agent, "remote_native_history", return_value=[]):
                record = agent.snapshot_catalog_record(sb, "dev1", "claude")
            self.assertEqual(record["config"], expected)

    def test_remote_metadata_cannot_authorize_identity_lineage_or_catalog_keys(self):
        forged = {"id": "f" * 32, "name": "dev1", "agent": "codex", "config": {},
                  "sandbox_ids": ["victim-box"], "snapshot_id": "victim-snapshot",
                  "conversations": [{"id": "forged-chat"}]}
        self.assertEqual(set(agent.workspace_metadata({"workspace": forged})),
                         {"id", "name", "agent", "config", "sandbox_ids"})
        sb = types.SimpleNamespace(sandbox_id="new-box")
        with patch.object(agent, "read_workspace_document", return_value={"workspace": forged}), \
                patch.object(agent, "write_workspace_document"), \
                patch.object(agent, "remote_native_history", return_value=[]):
            value = agent.save_workspace_metadata(sb, "dev1", "codex", {})
            self.assertNotEqual(value["id"], forged["id"])
            self.assertEqual(value["sandbox_ids"], ["new-box"])
            record = agent.snapshot_catalog_record(sb, "dev1", "codex")
        self.assertNotIn("snapshot_id", record)
        self.assertEqual(record["id"], value["id"])
        self.assertEqual(record["sandbox_ids"], ["new-box"])

    def test_invalid_remote_lineage_is_rejected(self):
        for values in ([None], [{}], [1], ["bad\nline"]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                agent.workspace_metadata({"workspace": {"id": "a" * 32, "name": "dev1",
                    "agent": "claude", "config": {}, "sandbox_ids": values}})
