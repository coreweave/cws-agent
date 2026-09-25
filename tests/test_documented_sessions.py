"""Offline verification of this branch's documented session/snapshot/sync commands.

No SDK request or agent process runs. Git/tar checks use isolated temporary files;
failures expose documented behavior that this exact checkout does not implement.
"""
import contextlib
import asyncio
from datetime import datetime, timezone
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch


def load_documented_cli():
    sdk = types.ModuleType("cwsandbox")
    sdk.AuthStrategy = types.SimpleNamespace(WANDB="wandb", COREWEAVE_API_KEY="coreweave_api_key")
    sdk.CWSandboxAuthenticationError = type("CWSandboxAuthenticationError", (Exception,), {})
    sdk.Sandbox = type("OfflineSandbox", (), {})
    sdk.FileSystemSnapshotOptions = sdk.ResourceOptions = object
    loader = importlib.machinery.SourceFileLoader(
        "documented_sessions_cli", str(Path(__file__).resolve().parents[1] / "cws-agent.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with patch.dict(sys.modules, {"cwsandbox": sdk}):
        loader.exec_module(module)
    return module


app = load_documented_cli()


def operation(value):
    class Operation:
        def result(self, **kwargs):
            return value

        def __await__(self):
            async def completed():
                return value
            return completed().__await__()
    return Operation()


class UploadProcess(types.SimpleNamespace):
    def __await__(self):
        async def completed():
            while not getattr(self, "stdin_closed", False):
                await asyncio.sleep(0)
            return self.result()
        return completed().__await__()

    def cancel(self):
        self.stdin_closed = True


def result(stdout="", stderr="", returncode=0):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def snapshot(sid, *, status="ready", day=1, sandbox="box", agent="claude"):
    return types.SimpleNamespace(file_system_snapshot_id=sid, status=status,
                                 created_at=datetime(2026, 9, day, tzinfo=timezone.utc),
                                 source_sandbox_id=sandbox, size_bytes=1048576,
                                 request_id=f"cwsa1|dev1|{agent}|123")


class DocumentedSessionCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cws-doc-sessions-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "project"
        self.worktrees = self.root / "sessions"
        self.meta = self.root / ".cws-meta"
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        # Keep redirection scoped to each CLI call; test runner output stays visible.

    def call(self, argv):
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            return app.main(argv)

    def git(self, *args):
        completed = subprocess.run(["git", "-C", str(self.project), *args],
                                   text=True, capture_output=True)
        if completed.returncode:
            self.fail(completed.stderr)
        return completed.stdout.strip()

    def init_repo(self):
        self.project.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "offline@example.invalid")
        self.git("config", "user.name", "Offline Test")
        (self.project / "tracked.txt").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")

    def local_git_exec(self, sb, command, **kwargs):
        if command[0] == "tmux":
            return result()  # never create a host tmux process or run an agent
        script = command[-1]
        if "command -v " in script:
            return result("/opt/agent/bin/installed-agent\n")
        for remote, local in (("/workspace/project", self.project),
                              ("/workspace/sessions", self.worktrees),
                              ("/workspace/.cws-meta", self.meta),
                              ("/workspace/home", self.root / "home")):
            script = script.replace(remote, str(local))
        if "tmux has-session" in script:
            return result("WT\n" if (self.worktrees / "fix").exists() else "")
        script = re.sub(r"tmux kill-session -t [^;]+", "true", script)
        script = re.sub(r"tmux ls -F '[^']*'", "false", script)
        return subprocess.run(["sh", "-lc", script], text=True, capture_output=True)

    def test_session_start_creates_isolated_worktree_and_records_base(self):
        self.init_repo()
        with patch.object(app, "require_active", return_value=types.SimpleNamespace(sandbox_id="sb-example")), \
                patch.object(app, "active_harness", return_value=app.HARNESSES["claude"]), \
                patch.object(app, "exec_retry", side_effect=self.local_git_exec):
            self.assertEqual(self.call(["session", "start", "work", "fix", "--base", "main",
                                        "--prompt", "fix the login bug"]), 0)
        self.assertEqual((self.meta / "fix.base").read_text(), "main")
        self.assertEqual((self.meta / "fix.agent").read_text(), "claude")
        self.assertEqual((self.worktrees / "fix/tracked.txt").read_text(), "base\n")

    def test_session_start_must_not_reset_branch_retained_by_stop(self):
        self.init_repo()
        self.git("checkout", "-qb", "agent/fix")
        (self.project / "tracked.txt").write_text("valuable committed work\n")
        self.git("commit", "-qam", "retained work")
        saved = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        with patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "active_harness", return_value=app.HARNESSES["claude"]), \
                patch.object(app, "exec_retry", side_effect=self.local_git_exec):
            try:
                self.call(["session", "start", "work", "fix", "--base", "main"])
            except SystemExit:
                pass  # refusing an existing branch is safe too
        self.assertEqual(self.git("rev-parse", "agent/fix"), saved,
                         "starting a previously stopped name must preserve its retained branch")

    def test_documented_restart_after_snapshot_reuses_existing_worktree(self):
        self.init_repo()
        with patch.object(app, "require_active", return_value=types.SimpleNamespace(sandbox_id="sb-example")), \
                patch.object(app, "active_harness", return_value=app.HARNESSES["claude"]), \
                patch.object(app, "exec_retry", side_effect=self.local_git_exec):
            self.call(["session", "start", "work", "fix", "--base", "main"])
            (self.worktrees / "fix/tracked.txt").write_text("uncommitted restored work\n")
            self.assertEqual(self.call(["session", "restart", "work", "fix"]), 0)
        self.assertEqual((self.worktrees / "fix/tracked.txt").read_text(), "uncommitted restored work\n")

    def test_destructive_stop_rejects_path_traversal_without_remote_execution(self):
        for name in ("../project", "../..", ".", "/tmp", "bad/name"):
            with self.subTest(name=name), patch.object(app, "require_active", return_value=object()), \
                    patch.object(app, "exec_retry", return_value=result()) as execute:
                with self.assertRaises(SystemExit):
                    self.call(["session", "stop", "work", name])
                execute.assert_not_called()

    def test_start_rejects_invalid_name_without_cloud_lookup(self):
        with patch.object(app, "require_active") as lookup, self.assertRaises(SystemExit):
            self.call(["session", "start", "work", "../project"])
        lookup.assert_not_called()

    def test_session_ls_prints_worktree_state(self):
        with patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "exec_retry", return_value=result("fix|agent/fix|yes|2\nother|agent/other|no|0\n")):
            self.assertEqual(self.call(["session", "ls", "work"]), 0)
        self.assertIn("running", self.stdout.getvalue())
        self.assertIn("stopped", self.stdout.getvalue())
        self.assertIn("2 files", self.stdout.getvalue())

    def test_session_attach_routes_to_exact_worktree(self):
        for harness in ("claude", "codex", "devin"):
            with self.subTest(harness=harness), patch.object(app, "require_active", return_value=object()), \
                    patch.object(app, "read_session_matches", return_value=True), \
                    patch.object(app, "exec_retry", return_value=result(harness)), \
                    patch.object(app, "pty_attach", return_value=0) as attach:
                self.assertEqual(self.call(["session", "attach", "work", "fix"]), 0)
            self.assertIn("tmux attach -t =cws-fix", attach.call_args.args[1])
            self.assertEqual(attach.call_args.kwargs["image_paste"], harness == "claude")

    def test_stop_retains_dirty_worktree_without_force(self):
        self.init_repo()
        with patch.object(app, "require_active", return_value=types.SimpleNamespace(sandbox_id="sb-example")), \
                patch.object(app, "active_harness", return_value=app.HARNESSES["claude"]), \
                patch.object(app, "exec_retry", side_effect=self.local_git_exec):
            self.call(["session", "start", "work", "fix", "--base", "main"])
            dirty = self.worktrees / "fix/valuable.txt"
            dirty.write_text("uncommitted work")
            self.assertNotEqual(self.call(["session", "stop", "work", "fix"]), 0)
            self.assertEqual(dirty.read_text(), "uncommitted work")
            self.assertEqual(self.call(["session", "stop", "work", "fix", "--force"]), 0)
        self.assertFalse((self.worktrees / "fix").exists())
        self.assertTrue(self.git("rev-parse", "agent/fix"))

    def test_stop_can_delete_clean_worktree_and_explicitly_delete_branch(self):
        self.init_repo()
        with patch.object(app, "require_active", return_value=types.SimpleNamespace(sandbox_id="sb-example")), \
                patch.object(app, "active_harness", return_value=app.HARNESSES["claude"]), \
                patch.object(app, "exec_retry", side_effect=self.local_git_exec):
            self.call(["session", "start", "work", "fix", "--base", "main"])
            self.assertEqual(self.call(["session", "stop", "work", "fix", "--delete-branch"]), 0)
        self.assertFalse((self.worktrees / "fix").exists())
        self.assertFalse((self.meta / "fix.base").exists())
        self.assertFalse((self.meta / "fix.agent").exists())
        self.assertEqual(self.git("branch", "--list", "agent/fix"), "")

    def test_stop_failure_never_falls_back_to_recursive_delete(self):
        with patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "read_session_matches", return_value=True), \
                patch.object(app, "exec_retry", return_value=result(stderr="locked worktree", returncode=1)) as execute:
            self.assertEqual(self.call(["session", "stop", "work", "fix", "--force"]), 1)
        script = execute.call_args.args[1][-1]
        self.assertNotIn("rm -rf", script)
        self.assertEqual(execute.call_args.kwargs["attempts"], 1)
        self.assertNotIn("stopped", self.stdout.getvalue())

    def test_restart_uses_native_continuation_for_each_cli_harness(self):
        for agent, native in (
                ("claude", "claude --continue --dangerously-skip-permissions"),
                ("codex", "codex resume --last --dangerously-bypass-approvals-and-sandbox"),
                ("devin", "devin --continue --permission-mode bypass")):
            with self.subTest(agent=agent), patch.object(app, "require_active", return_value=object()), \
                    patch.object(app, "read_session_sessions", return_value=[{"name": "fix", "alive": False}]), \
                    patch.object(app, "active_harness", return_value=app.HARNESSES[agent]), \
                    patch.object(app, "exec_retry", return_value=result()) as execute:
                self.assertEqual(self.call(["session", "restart", "work", "fix"]), 0)
            self.assertTrue(execute.call_args.args[1][-1].endswith("exec " + native))
            self.assertEqual(execute.call_args.kwargs["attempts"], 1)

    def test_failed_worktree_listing_does_not_report_no_sessions(self):
        with patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "exec_retry", return_value=result(stderr="transport failed", returncode=1)), \
                self.assertRaisesRegex(SystemExit, "could not list"):
            self.call(["session", "ls", "work"])

    def test_exec_mutation_is_never_replayed_automatically(self):
        with patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "exec_retry", return_value=result()) as execute:
            self.assertEqual(self.call(["exec", "dev1", "git push origin HEAD:agent/fix-auth"]), 0)
        self.assertEqual(execute.call_args.kwargs["attempts"], 1)

    def test_session_diff_propagates_git_failure(self):
        with patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "read_session_matches", return_value=True), \
                patch.object(app, "exec_retry", return_value=result(stderr="fatal: bad revision", returncode=128)):
            self.assertNotEqual(self.call(["session", "diff", "work", "fix"]), 0)
        self.assertIn("fatal: bad revision", self.stderr.getvalue())

    def test_empty_diff_redirection_is_empty_patch(self):
        with patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "read_session_matches", return_value=True), \
                patch.object(app, "exec_retry", return_value=result()):
            self.assertEqual(self.call(["session", "diff", "work", "fix"]), 0)
        self.assertEqual(self.stdout.getvalue(), "")

    def test_diff_patch_roundtrip_applies_to_local_checkout(self):
        self.init_repo()
        (self.project / "tracked.txt").write_text("remote edit\n")
        diff = subprocess.run(["git", "-C", str(self.project), "diff"],
                              capture_output=True, text=True, check=True).stdout
        (self.project / "tracked.txt").write_text("base\n")
        with patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "read_session_matches", return_value=True), \
                patch.object(app, "exec_retry", return_value=result(diff)):
            self.assertEqual(self.call(["session", "diff", "work", "fix"]), 0)
        applied = subprocess.run(["git", "-C", str(self.project), "apply", "-"],
                                 input=self.stdout.getvalue(), text=True, capture_output=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual((self.project / "tracked.txt").read_text(), "remote edit\n")

    def test_exec_preserves_exact_stdout_stderr_and_exit_code(self):
        with patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "exec_retry", return_value=result("{\"ok\":true}", "warning\n", 7)):
            self.assertEqual(self.call(["exec", "dev1", "cat build/report.json"]), 7)
        self.assertEqual(self.stdout.getvalue(), '{"ok":true}')
        self.assertEqual(self.stderr.getvalue(), "warning\n")

    def test_sync_tar_carries_project_config_and_requested_git(self):
        for rel in ("CLAUDE.md", "AGENTS.md", ".claude/skills/review/SKILL.md", ".mcp.json",
                    ".git/config", "node_modules/package/index.js", "data/large", "src/main.py"):
            path = self.root / "source" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rel)
        for include_git in (False, True):
            path, count = app.build_local_tar(str(self.root / "source"), include_git=include_git,
                                              extra_excludes=["data"])
            try:
                with tarfile.open(path) as archive:
                    names = archive.getnames()
                self.assertIn("./.claude/skills/review/SKILL.md", names)
                self.assertIn("./.mcp.json", names)
                self.assertEqual("./.git/config" in names, include_git)
                self.assertNotIn("./node_modules", names)
                self.assertNotIn("./data", names)
                self.assertEqual(count, 5 + int(include_git))
            finally:
                os.unlink(path)

    def test_sync_clean_removes_double_dot_hidden_files(self):
        from test_resumable_upload import LocalSandbox
        source = self.root / "source"
        source.mkdir()
        (source / "fresh.txt").write_text("uploaded")
        self.project.mkdir()
        stale = self.project / "..stale"
        stale.write_text("old remote file")
        with contextlib.redirect_stdout(self.stdout), \
                patch("pathlib.Path.home", return_value=self.root / "private-home"), \
                patch.object(app, "PROJECT_DIR", str(self.project)), \
                patch.object(app, "UPLOAD_REMOTE_ROOT", str(self.root / "staging")), \
                patch.object(app, "UPLOAD_REMOTE", app.UPLOAD_REMOTE.replace("os.sync()", "pass")):
            app.sync_local_dir(LocalSandbox(), str(source), include_git=True, extra_excludes=[], clean=True)
        self.assertFalse(stale.exists(), "--clean must mirror deletions for ..hidden files too")
        self.assertEqual((self.project / "fresh.txt").read_text(), "uploaded")

    def test_sync_reports_failed_upload_and_retains_local_archive(self):
        source = self.root / "source"
        source.mkdir()
        archive, _ = app.build_local_tar(str(source), include_git=True, extra_excludes=[])
        with patch.object(app, "build_local_tar", return_value=(archive, 0)), \
                patch("pathlib.Path.home", return_value=self.root / "private-home"), \
                patch.object(app, "transfer_cached_upload", side_effect=app.ProjectUploadError("tar failed")), \
                contextlib.redirect_stdout(self.stdout), self.assertRaisesRegex(SystemExit, "tar failed"):
            app.sync_local_dir(object(), str(source), include_git=True, extra_excludes=[], clean=False)
        cached = list((self.root / "private-home/.local/state/cws-agent/uploads").glob("*/archive.tar.gz"))
        self.assertEqual(len(cached), 1)
        self.assertGreater(cached[0].stat().st_size, 0)


class DocumentedSnapshotCommands(unittest.TestCase):
    def setUp(self):
        self.out, self.err = io.StringIO(), io.StringIO()

    def call(self, argv):
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            return app.main(argv)

    def test_snapshot_lookup_filters_other_sandboxes_and_sorts_newest(self):
        snapshots = [snapshot("old", day=1), snapshot("new", day=5), snapshot("unrelated", day=6, sandbox="other")]
        with patch.object(app, "all_session_sandbox_ids", return_value={"box"}), \
                patch.object(app.Sandbox, "list_snapshots", return_value=operation(snapshots), create=True):
            self.assertEqual([s.file_system_snapshot_id for s in app.session_snapshots("dev1")], ["new", "old"])

    def test_resume_selects_ready_snapshot_and_restores_recorded_harness(self):
        snapshots = [snapshot("failed", status="failed", day=6), snapshot("ready", day=5, agent="codex")]
        with patch.object(app, "find_active", return_value=None), \
                patch.object(app, "session_snapshots", return_value=snapshots), \
                patch.object(app, "build_env", return_value={}), \
                patch.object(app, "read_backend_config", return_value=None), \
                patch.object(app, "sync_agent_config") as config_sync, \
                patch.object(app, "provision_session", return_value=types.SimpleNamespace(sandbox_id="sb-example")) as provision, \
                patch.object(app, "pty_attach", return_value=0) as attach:
            self.assertEqual(self.call(["resume", "dev1", "--attach"]), 0)
        self.assertEqual(provision.call_args.kwargs["restore_snapshot_id"], "ready")
        self.assertEqual(provision.call_args.kwargs["harness"].name, "codex")
        self.assertIsNone(provision.call_args.kwargs["repo_url"])
        self.assertIn("codex", attach.call_args.args[1])
        self.assertEqual(config_sync.call_args.args[1].name, "codex")

    def test_resume_refuses_existing_sandbox_without_provision(self):
        with patch.object(app, "find_active", return_value=object()), \
                patch.object(app, "provision_session") as provision, self.assertRaises(SystemExit):
            self.call(["resume", "dev1"])
        provision.assert_not_called()

    def test_resume_without_snapshot_does_not_create_sandbox(self):
        with patch.object(app, "find_active", return_value=None), \
                patch.object(app, "latest_ready_snapshot", return_value=None), \
                patch.object(app, "provision_session") as provision, self.assertRaises(SystemExit):
            self.call(["resume", "dev1"])
        provision.assert_not_called()

    def test_snapshot_and_checkpoint_alias_snapshot_without_stopping(self):
        sb = types.SimpleNamespace(sandbox_id="box", stop=Mock())
        with patch.object(app, "require_active", return_value=sb), \
                patch.object(app, "probe_session_meta", return_value=("dev1", "claude")), \
                patch.object(app, "take_snapshot", return_value="snap"), \
                patch.object(app.Sandbox, "get_snapshot", return_value=operation(snapshot("snap")), create=True):
            for command in ("snapshot", "checkpoint"):
                self.assertEqual(self.call([command, "dev1"]), 0)
        sb.stop.assert_not_called()

    def test_down_snapshots_before_stopping_and_no_snapshot_skips_it(self):
        for skip in (False, True):
            calls = []
            sb = types.SimpleNamespace(sandbox_id="box", stop=lambda: (calls.append("stop") or operation(None)))
            with patch.object(app, "require_active", return_value=sb), \
                    patch.object(app, "probe_session_meta", return_value=("dev1", "claude")), \
                    patch.object(app, "take_snapshot", side_effect=lambda *args: (calls.append("snapshot") or "snap")):
                self.assertEqual(self.call(["down", "dev1"] + (["--no-snapshot"] if skip else [])), 0)
            self.assertEqual(calls, ["stop"] if skip else ["snapshot", "stop"])

    def test_snapshot_failure_prevents_down_from_stopping(self):
        sb = types.SimpleNamespace(sandbox_id="box", stop=Mock())
        with patch.object(app, "require_active", return_value=sb), \
                patch.object(app, "probe_session_meta", return_value=("dev1", "claude")), \
                patch.object(app, "take_snapshot", side_effect=RuntimeError("snapshot failed")), \
                self.assertRaises(RuntimeError):
            self.call(["down", "dev1"])
        sb.stop.assert_not_called()

    def test_prune_deletes_only_old_ready_snapshots(self):
        snapshots = [snapshot("new", day=5), snapshot("failed", status="failed", day=4), snapshot("old", day=1)]
        with patch.object(app, "session_snapshots", return_value=snapshots), \
                patch.object(app.Sandbox, "delete_snapshot", return_value=operation(None), create=True) as delete:
            self.assertEqual(self.call(["prune", "dev1", "--keep", "1"]), 0)
        delete.assert_called_once_with("old", missing_ok=True, auth=app.sandbox_auth())

    def test_prune_rejects_negative_keep_without_deleting(self):
        with patch.object(app, "session_snapshots", return_value=[snapshot("new"), snapshot("old")]), \
                patch.object(app.Sandbox, "delete_snapshot", return_value=operation(None), create=True) as delete:
            with self.assertRaises(SystemExit):
                self.call(["prune", "dev1", "--keep", "-1"])
        delete.assert_not_called()

    def test_list_status_and_snapshots_print_names_and_ids(self):
        sb = types.SimpleNamespace(sandbox_id="box", status=types.SimpleNamespace(value="running"), started_at=None)
        with patch.object(app.Sandbox, "list", return_value=operation([sb]), create=True), \
                patch.object(app, "probe_session_meta", return_value=("dev1", "claude")), \
                patch.object(app, "find_active", return_value=sb), \
                patch.object(app, "session_snapshots", return_value=[snapshot("snap")]):
            for command in (["list"], ["status", "dev1"], ["snapshots", "dev1"]):
                self.assertEqual(self.call(command), 0)
        for expected in ("dev1", "claude", "box", "ACTIVE", "snap"):
            self.assertIn(expected, self.out.getvalue())

    @unittest.skipUnless(hasattr(time, "tzset"), "requires local timezone override")
    def test_list_renders_local_date_time_and_timezone(self):
        try:
            with patch.dict(os.environ, {"TZ": "America/Chicago"}):
                time.tzset()
                sb = types.SimpleNamespace(sandbox_id="box", status=types.SimpleNamespace(value="running"),
                    started_at=datetime(2026, 9, 7, 18, 12, 54, tzinfo=timezone.utc))
                with patch.object(app.Sandbox, "list", return_value=operation([sb]), create=True), \
                        patch.object(app, "probe_session_meta", return_value=("telegram2", "claude")):
                    self.assertEqual(self.call(["list"]), 0)
                self.assertIn("STARTED (LOCAL)", self.out.getvalue())
                self.assertIn("2026-09-07 13:12:54 CDT", self.out.getvalue())
                self.assertEqual(app.format_started_at(datetime(2026, 1, 1, 2, tzinfo=timezone.utc)),
                                 "2025-12-31 20:00:00 CST")
                self.assertEqual(app.format_started_at(datetime(2026, 9, 7, 18, 12, 54)),
                                 "2026-09-07 13:12:54 CDT")
                self.assertEqual(app.format_started_at(None), "-")
        finally:
            time.tzset()


class DocumentedSmokeCleanup(unittest.TestCase):
    """Run the smoke shell itself against a local fake CLI, never the platform."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cws-doc-smoke-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.log = self.root / "calls.jsonl"
        self.script = self.root / "smoke.sh"
        self.script.write_text((Path(__file__).resolve().parents[1] / "smoke.sh").read_text())
        fake = self.root / "cws-agent.py"
        fake.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(__file__).parent
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args[0] == os.environ.get("SMOKE_FAKE_FAIL"):
    sys.exit(7)
if args[0] == "snapshots" and os.environ.get("SMOKE_FAKE_OLD_SNAPSHOTS"):
    print("existing-snapshot ready")
if args[0] == "exec":
    if args[2].startswith("echo "):
        (root / "marker").write_text(args[2].split()[1])
    print((root / "marker").read_text())
''')
        fake.chmod(0o700)

    def run_smoke(self, *args, **env):
        return subprocess.run(["bash", str(self.script), *args], capture_output=True, text=True,
                              env={**os.environ, "CWSANDBOX_API_KEY": "offline-fake", **env})

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_failure_after_launch_cleans_up_owned_resources_and_preserves_exit(self):
        completed = self.run_smoke(SMOKE_FAKE_FAIL="snapshot")
        self.assertEqual(completed.returncode, 7, completed.stderr)
        self.assertEqual([call[0] for call in self.calls()][-2:], ["down", "prune"])
        self.assertIn("--no-snapshot", self.calls()[-2])
        self.assertEqual(self.calls()[-1][-2:], ["--keep", "0"])

    def test_failed_launch_never_stops_or_prunes_someone_elses_name(self):
        completed = self.run_smoke("explicit-name", SMOKE_FAKE_FAIL="launch")
        self.assertEqual(completed.returncode, 7)
        self.assertEqual([call[0] for call in self.calls()], ["snapshots", "launch"])

    def test_preexisting_snapshots_refuse_name_without_cleanup(self):
        completed = self.run_smoke("explicit-name", SMOKE_FAKE_OLD_SNAPSHOTS="1")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual([call[0] for call in self.calls()], ["snapshots"])

    def test_success_uses_unique_default_names_and_only_one_final_cleanup(self):
        for _ in range(2):
            completed = self.run_smoke()
            self.assertEqual(completed.returncode, 0, completed.stderr)
        launches = [call for call in self.calls() if call[0] == "launch"]
        self.assertNotEqual(launches[0][2], launches[1][2])
        for call in self.calls():
            if call[0] in ("launch", "restore", "resume"):
                self.assertEqual(call[call.index("--lifetime") + 1], "30m")
        self.assertEqual(sum(call[0] == "prune" for call in self.calls()), 2)


if __name__ == "__main__":
    unittest.main()
