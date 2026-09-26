"""Shell CLI contracts: offline option matrix, uploads, snapshots, and lifecycle."""
import argparse
import contextlib
import io
import itertools
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cwsandbox import AuthStrategy, FileSystemSnapshot, FileSystemSnapshotStatus
from test_consolidation import cli_parser
from test_terminal import agent


def ready_snapshot(**kwargs):
    return FileSystemSnapshot(**{
        "file_system_snapshot_id": "fss-example", "status": FileSystemSnapshotStatus.READY,
        "request_id": "saved-work", "size_bytes": 42, **kwargs,
    })


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.stdout))
        self.stack.enter_context(contextlib.redirect_stderr(self.stderr))
        self.stdin_tty = self.stack.enter_context(patch.object(agent.sys.stdin, "isatty", return_value=True))
        self.stdout_tty = self.stack.enter_context(patch.object(agent.sys.stdout, "isatty", return_value=True))
        self.auth = self.stack.enter_context(patch.object(agent, "sandbox_auth", return_value=AuthStrategy.WANDB))
        self.sb = Mock()
        self.sb.sandbox_id = "sb-example"
        self.sb.exec.return_value.result.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        self.list = self.stack.enter_context(patch.object(agent.Sandbox, "list"))
        self.list.return_value.result.return_value = []
        self.run = self.stack.enter_context(patch.object(agent.Sandbox, "run", return_value=self.sb))
        self.snapshots = self.stack.enter_context(patch.object(agent.Sandbox, "list_snapshots"))
        self.snapshots.return_value.result.return_value = [ready_snapshot()]
        self.latest = self.stack.enter_context(patch.object(agent, "latest_ready_snapshot", return_value=None))
        self.pty = self.stack.enter_context(patch.object(agent, "pty_attach", return_value=17))
        self.bootstrap = self.stack.enter_context(patch.object(agent, "run_bootstrap"))
        self.config = self.stack.enter_context(patch.object(agent, "sync_agent_config"))
        self.parser = cli_parser()
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.file = self.root / "sample.txt"
        self.file.write_bytes(b"hello\n")
        self.uploads = {}
        def upload(path, source):
            self.uploads[path] = b"".join(source)
            return Mock()
        self.sb.write_file_streaming.side_effect = upload

    def invoke(self, *argv):
        args = self.parser.parse_args(["shell", *argv])
        return args.func(args)

    def test_default_creates_plain_shell_without_agent_or_credentials(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-not-a-key", "WANDB_API_KEY": "synthetic-not-a-key"}):
            self.assertEqual(self.invoke("dev1"), 17)
        kwargs = self.run.call_args.kwargs
        self.assertEqual(kwargs["container_image"], "python:3.11")
        self.assertEqual(kwargs["placement_mode"], "serverless")
        self.assertEqual(kwargs["max_lifetime_seconds"], 28800)
        self.assertEqual(kwargs["resources"].requests, {"cpu": "2", "memory": "4Gi"})
        self.assertIsNone(kwargs["resources"].gpu)
        self.assertEqual(set(kwargs["environment_variables"]), {"CWS_AGENT_NAME", "CWS_AGENT_HARNESS", "CWS_AGENT_DISK"})
        self.assertEqual(kwargs["file_system_snapshot"].mount_path, "/workspace")
        self.assertIn("exec bash", self.pty.call_args.args[1])
        self.assertIn("exec sh", self.pty.call_args.args[1])
        self.assertEqual(self.pty.call_args.kwargs, {"image_paste": False, "plain_shell": True})
        self.sb.stop.assert_not_called()
        self.bootstrap.assert_not_called()
        self.config.assert_not_called()
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertIn("Creating shell sandbox", self.stderr.getvalue())
        self.assertIn("Preparing shell", self.stderr.getvalue())
        self.assertIn("Shell ready", self.stderr.getvalue())
        self.assertIn("sb-example", self.stderr.getvalue())

    def test_bare_shell_generates_reconnectable_name(self):
        self.invoke()
        name = self.run.call_args.kwargs["environment_variables"]["CWS_AGENT_NAME"]
        self.assertRegex(name, r"^shell-[a-f0-9]{8}$")
        self.assertIn(name, self.stderr.getvalue())

    def test_connect_does_not_create_or_modify_existing_sandbox(self):
        self.list.return_value.result.return_value = [self.sb]
        self.invoke("dev1", "--cmd", "nvidia-smi")
        self.run.assert_not_called()
        self.sb.exec.assert_not_called()
        self.sb.stop.assert_not_called()
        self.assertIn("nvidia-smi", self.pty.call_args.args[1])
        self.assertEqual(self.stderr.getvalue(), "")

    def test_new_sandbox_explains_lifecycle_before_command(self):
        def attach(*args, **kwargs):
            self.assertIn("will keep running after this command exits", self.stderr.getvalue())
            self.assertIn("cws-agent down dev1 --no-snapshot", self.stderr.getvalue())
            return 0
        self.pty.side_effect = attach
        self.assertEqual(self.invoke("dev1"), 0)

    def test_secret_errors_identify_authentication_or_volume_conflict(self):
        self.auth.return_value = AuthStrategy.COREWEAVE_API_KEY
        with self.assertRaises(SystemExit) as error:
            self.invoke("dev1", "--secret", "HF_TOKEN")
        self.assertIn("CWSANDBOX_API_KEY is set; unset it", str(error.exception))
        self.assertNotIn("--volume", str(error.exception))
        for auth in (AuthStrategy.COREWEAVE_API_KEY, AuthStrategy.WANDB):
            self.auth.return_value = auth
            with self.assertRaisesRegex(SystemExit, "cannot be combined with --volume"):
                self.invoke("dev1", "--secret", "HF_TOKEN", "--volume", "data")
        self.list.assert_not_called()
        self.run.assert_not_called()

    def test_all_creation_option_subsets_for_both_auth_modes_and_lifecycles(self):
        # Every presence/absence combination, not just a pairwise sample.
        flags = [("--image", "alpine:3.21"), ("--cpu", "500m"), ("--gpu", "any:8"),
                 ("--memory", "2048"), ("--add-local", str(self.file)),
                 ("--snapshot", "saved-work"), ("--secret", "HF_TOKEN"),
                 ("--volume", "workspace:/mnt/data")]
        for enabled, auth, existing, command, mode in itertools.product(
                itertools.product((False, True), repeat=len(flags)),
                (AuthStrategy.WANDB, AuthStrategy.COREWEAVE_API_KEY), (False, True), (False, True),
                (None, "serverless", "cks")):
            argv = ["dev1", *(value for on, pair in zip(enabled, flags) if on for value in pair)]
            if command:
                argv += ["--cmd", "nvidia-smi"]
            if mode:
                argv += ["--mode", mode]
            secret, volume = enabled[6:8]
            placement = mode or ("cks" if volume else "serverless")
            invalid_auth = (secret and auth != AuthStrategy.WANDB) or (placement == "cks" and auth != AuthStrategy.COREWEAVE_API_KEY)
            invalid_placement = (secret and placement == "cks") or (volume and placement == "serverless")
            invalid = invalid_auth or invalid_placement or (existing and (any(enabled) or mode))
            self.auth.return_value = auth
            self.list.return_value.result.return_value = [self.sb] if existing else []
            self.run.reset_mock()
            self.pty.reset_mock()
            with self.subTest(enabled=enabled, auth=auth, existing=existing, command=command, mode=mode):
                if invalid:
                    with self.assertRaises(SystemExit):
                        self.invoke(*argv)
                    self.run.assert_not_called()
                    self.pty.assert_not_called()
                else:
                    self.assertEqual(self.invoke(*argv), 17)
                    if existing:
                        self.run.assert_not_called()
                    else:
                        kwargs = self.run.call_args.kwargs
                        self.assertEqual(kwargs["resources"].gpu, {"count": 8} if enabled[2] else None)
                        self.assertEqual(kwargs["resources"].requests["cpu"], "500m" if enabled[1] else "2")
                        self.assertEqual(kwargs["resources"].requests["memory"], "2048Mi" if enabled[3] else "4Gi")
                        self.assertEqual(kwargs["resources"].limits, kwargs["resources"].requests)
                        self.assertEqual(kwargs["placement_mode"], placement)
                        self.assertEqual(kwargs["file_system_snapshot"].file_system_snapshot_id, "fss-example" if enabled[5] else None)
                        if volume:
                            self.assertNotEqual(kwargs["volumes"][0].name, "workspace")
                        if secret:
                            self.assertEqual((kwargs["secrets"][0].store, kwargs["secrets"][0].name), ("wandb", "HF_TOKEN"))
        self.sb.stop.assert_not_called()

    def test_placement_conflicts_fail_before_network(self):
        cases = [
            (AuthStrategy.COREWEAVE_API_KEY, ["--mode", "serverless", "--volume", "data"], "--volume requires CKS"),
            (AuthStrategy.WANDB, ["--mode", "cks"], "CKS placement requires a CoreWeave"),
            (AuthStrategy.WANDB, ["--mode", "cks", "--secret", "HF_TOKEN"], "cannot be combined with --mode cks"),
        ]
        for auth, flags, message in cases:
            self.auth.return_value = auth
            with self.subTest(auth=auth, flags=flags), self.assertRaisesRegex(SystemExit, message):
                self.invoke("dev1", *flags)
        self.list.assert_not_called()
        self.run.assert_not_called()

    def test_explicit_mode_cannot_change_running_sandbox(self):
        self.auth.return_value = AuthStrategy.COREWEAVE_API_KEY
        self.list.return_value.result.return_value = [self.sb]
        for mode in ("serverless", "cks"):
            with self.subTest(mode=mode), self.assertRaisesRegex(SystemExit, "creation options.*--mode"):
                self.invoke("dev1", "--mode", mode)
        self.run.assert_not_called()
        self.pty.assert_not_called()

    def test_nonterminal_stdin_does_not_replace_placement_mode(self):
        self.auth.return_value = AuthStrategy.COREWEAVE_API_KEY
        for mode, stdout_tty in itertools.product((None, "serverless", "cks"), (False, True)):
            self.stdout_tty.return_value = stdout_tty
            for path in (os.devnull, self.file):
                with self.subTest(mode=mode, stdout_tty=stdout_tty, path=path), \
                        open(path) as stream, patch.object(agent.sys, "stdin", stream):
                    self.invoke("dev1", "-c", "true", *(["--mode", mode] if mode else []))
                self.assertEqual(self.run.call_args.kwargs["placement_mode"], mode or "serverless")

    def test_gpu_counts_and_invalid_values(self):
        for count in range(1, 9):
            self.assertEqual(agent.shell_gpu(f"any:{count}"), {"count": count})
        self.assertEqual(agent.shell_gpu("any"), {"count": 1})
        for value in ["any:0", "any:9", "any:-1", "any:1.5", "any:", "any:01", "Any", "a100", "", "rtxp6000", "rtxp6000-v2:8"]:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                agent.shell_gpu(value)

    def test_resource_and_name_validation_happens_before_network(self):
        for flag, values in [("--cpu", ["0", "-1", "NaN", "inf", "cpu", "0m"]),
                             ("--mode", ["", "auto", "CKS", "other"]),
                             ("--memory", ["0", "-1", "0Gi", "4GB", "4K", "nan", "1e3"]),
                             ("--secret", ["", "KEY=value", "a/b", "a-b", "CWS_AGENT_HARNESS"]),
                             ("--add-local", ["", " ", "bad\x00path"]),
                             ("--image", ["", " ", "bad\x00image"]),
                             ("--cmd", ["", " ", "bad\x00command"]),
                             ("--volume", ["", "ID", "id:", "id:/", "id:/mnt", "id:/mnt/../home", "id:/workspace", "id:/mnt//x", "id:/mnt/a/"])]:
            for value in values:
                with self.subTest(flag=flag, value=value), self.assertRaises(SystemExit):
                    self.invoke("dev1", f"{flag}={value}")
        for name in ["", "bad_name", "Bad", "a" * 41]:
            with self.subTest(name=name), self.assertRaises(SystemExit):
                self.invoke(name)
        self.list.assert_not_called()
        self.run.assert_not_called()

    def test_all_tty_combinations_and_cmd_aliases(self):
        self.list.return_value.result.return_value = [self.sb]
        for stdin, stdout, cmd in itertools.product((False, True), (False, True), (None, "--cmd", "-c")):
            self.stdin_tty.return_value, self.stdout_tty.return_value = stdin, stdout
            self.pty.reset_mock()
            self.sb.exec.reset_mock()
            with self.subTest(stdin=stdin, stdout=stdout, cmd=cmd):
                if cmd is None and not (stdin and stdout):
                    with self.assertRaisesRegex(SystemExit, "terminal"):
                        self.invoke("dev1")
                    self.sb.exec.assert_not_called()
                else:
                    self.assertEqual(self.invoke("dev1", *([cmd, "printf hello"] if cmd else [])), 17 if stdin and stdout else 0)
                    self.assertEqual(self.pty.called, stdin and stdout)

    def test_noninteractive_keeps_streams_and_exit_status(self):
        self.stdout_tty.return_value = False
        self.list.return_value.result.return_value = [self.sb]
        self.sb.exec.return_value.result.return_value = SimpleNamespace(returncode=23, stdout="result\n", stderr="failure\n")
        self.assertEqual(self.invoke("dev1", "-c", "printf result; exit 23"), 23)
        self.assertEqual(self.stdout.getvalue(), "result\n")
        self.assertTrue(self.stderr.getvalue().endswith("failure\n"))
        command = self.sb.exec.call_args.args[0]
        self.assertIn(shlex.quote("printf result; exit 23"), command[2])
        self.assertTrue(command[2].endswith(" </dev/null"))
        self.assertEqual(self.sb.exec.call_args.kwargs["timeout_seconds"], 300)

    def test_piped_and_file_stdin_warn_without_consuming_input(self):
        self.list.return_value.result.return_value = [self.sb]
        read_fd, write_fd = os.pipe()
        with os.fdopen(write_fd, "w") as writer:
            writer.write("hello\n")
        with os.fdopen(read_fd) as pipe, self.file.open() as source:
            for stream in (pipe, source):
                self.stderr.seek(0)
                self.stderr.truncate()
                with patch.object(agent.sys, "stdin", stream):
                    self.assertEqual(self.invoke("dev1", "-c", "cat"), 0)
                self.assertIn("input will be ignored", self.stderr.getvalue())
                self.assertEqual(stream.read(), "hello\n")
        self.pty.assert_not_called()

    def test_devnull_stdin_and_redirected_stdout_stay_quiet(self):
        self.list.return_value.result.return_value = [self.sb]
        self.stdout_tty.return_value = False
        with open(os.devnull) as stream, patch.object(agent.sys, "stdin", stream):
            self.assertEqual(self.invoke("dev1", "-c", "true"), 0)
        self.assertEqual(self.stderr.getvalue(), "")
        self.assertEqual(self.invoke("dev1", "-c", "true"), 0)
        self.assertEqual(self.stderr.getvalue(), "")

    def test_custom_image_command_does_not_require_python(self):
        command = agent.shell_command("exec sh -c 'exit 23'")
        self.assertNotIn("python", command)
        self.assertNotIn(".cws-import-env", command)
        self.assertIn("cd /workspace/project || exit", command)

    def test_copy_file_directory_empty_directory_and_permissions(self):
        directory = self.root / "with spaces"
        directory.mkdir()
        (directory / "empty").mkdir()
        script = directory / "script.sh"
        script.write_bytes(b"#!/bin/sh\nexit 0\n")
        script.chmod(0o751)
        self.invoke("dev1", "--add-local", str(self.file), "--add-local", str(directory))
        self.assertEqual(self.uploads["/mnt/sample.txt"], b"hello\n")
        self.assertEqual(self.uploads["/mnt/with spaces/script.sh"], script.read_bytes())
        commands = [call.args[0] for call in self.sb.exec.call_args_list]
        self.assertIn(["mkdir", "-p", "--", "/mnt/with spaces/empty"], commands)
        self.assertIn(["chmod", "751", "--", "/mnt/with spaces/script.sh"], commands)

    def test_local_symlinks_missing_and_special_files_rejected_before_network(self):
        link = self.root / "link"
        link.symlink_to(self.file)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for value in [link, fifo, self.root / "missing", Path("/"), self.root]:
            with self.subTest(value=value), self.assertRaises(SystemExit):
                self.invoke("dev1", "--add-local", str(value))
        self.list.assert_not_called()
        self.run.assert_not_called()

    def test_mount_conflicts_and_repeated_secrets_rejected_before_network(self):
        self.auth.return_value = AuthStrategy.COREWEAVE_API_KEY
        cases = [["--volume", "data", "--volume", "data:/mnt/elsewhere"],
                 ["--volume", "a:/mnt/same", "--volume", "b:/mnt/same"],
                 ["--volume", "a:/mnt/data", "--volume", "b:/mnt/data/nested"],
                 ["--volume", "a:/mnt/sample.txt", "--add-local", str(self.file)],
                 ["--add-local", str(self.file), "--add-local", str(self.file)]]
        for flags in cases:
            with self.subTest(flags=flags), self.assertRaises(SystemExit):
                self.invoke("dev1", *flags)
        self.auth.return_value = AuthStrategy.WANDB
        with self.assertRaisesRegex(SystemExit, "each --secret"):
            self.invoke("dev1", "--secret", "HF_TOKEN", "--secret", "HF_TOKEN")
        self.list.assert_not_called()

    def test_repeated_nonconflicting_volumes_and_secrets(self):
        self.auth.return_value = AuthStrategy.COREWEAVE_API_KEY
        self.invoke("dev1", "--volume", "workspace", "--volume", "models:/mnt/models")
        volumes = self.run.call_args.kwargs["volumes"]
        self.assertEqual([v.mount_path for v in volumes], ["/mnt/workspace", "/mnt/models"])
        self.assertEqual([v.name for v in volumes], ["shell-volume-0", "shell-volume-1"])
        self.auth.return_value = AuthStrategy.WANDB
        self.invoke("dev1", "--secret", "HF_TOKEN", "--secret", "OTHER_TOKEN")
        self.assertEqual([s.name for s in self.run.call_args.kwargs["secrets"]], ["HF_TOKEN", "OTHER_TOKEN"])

    def test_setup_failures_stop_new_compute(self):
        for error in [RuntimeError("synthetic failure"), KeyboardInterrupt(), SystemExit("setup failed")]:
            self.sb.exec.side_effect = error
            self.sb.stop.reset_mock()
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.invoke("dev1")
            self.sb.stop.assert_called_once_with(missing_ok=True)
            self.pty.assert_not_called()

    def test_existing_remote_upload_destination_is_not_overwritten(self):
        self.sb.exec.side_effect = [Mock(result=Mock(return_value=SimpleNamespace(returncode=0))),
                                    Mock(result=Mock(return_value=SimpleNamespace(returncode=1)))]
        with self.assertRaisesRegex(SystemExit, "destination already exists"):
            self.invoke("dev1", "--add-local", str(self.file))
        self.sb.write_file_streaming.assert_not_called()
        self.sb.stop.assert_called_once()

    def test_duplicate_active_names_fail_without_side_effects(self):
        self.list.return_value.result.return_value = [self.sb, self.sb]
        with self.assertRaisesRegex(SystemExit, "multiple running"):
            self.invoke("dev1")
        self.run.assert_not_called()
        self.pty.assert_not_called()

    def test_snapshot_id_request_name_and_session_name(self):
        snapshot = ready_snapshot()
        self.snapshots.return_value.result.return_value = [snapshot]
        for reference in [snapshot.file_system_snapshot_id, snapshot.request_id]:
            self.assertIs(agent.shell_snapshot(reference), snapshot)
        self.latest.assert_not_called()
        self.latest.return_value = snapshot
        self.assertIs(agent.shell_snapshot("previous-session"), snapshot)
        self.latest.assert_called_once_with("previous-session")

    def test_snapshot_id_precedes_request_name_and_ambiguity_is_rejected(self):
        snapshot = ready_snapshot()
        self.snapshots.return_value.result.return_value = [snapshot, ready_snapshot(file_system_snapshot_id="other", request_id="fss-example")]
        self.assertIs(agent.shell_snapshot("fss-example"), snapshot)
        self.snapshots.return_value.result.return_value = [snapshot, snapshot]
        with self.assertRaisesRegex(SystemExit, "ambiguous"):
            agent.shell_snapshot("saved-work")

    def test_missing_unready_and_managed_snapshots_are_rejected(self):
        for snapshot in [None, ready_snapshot(status=FileSystemSnapshotStatus.FAILED),
                         ready_snapshot(request_id="cwcp1|dev1|claude|operation")]:
            self.snapshots.return_value.result.return_value = [snapshot] if snapshot else []
            with self.subTest(snapshot=snapshot), self.assertRaises(SystemExit):
                self.invoke("dev1", "--snapshot", "fss-example")
        self.run.assert_not_called()

    def test_restore_retains_recorded_workspace_size(self):
        self.snapshots.return_value.result.return_value = [ready_snapshot(request_id="cwsa1|dev1|shell|123|disk=20Gi")]
        self.invoke("dev1", "--snapshot", "fss-example")
        self.assertEqual(self.run.call_args.kwargs["file_system_snapshot"].size, "20Gi")

    def test_shell_snapshot_needs_no_python_metadata_helper(self):
        self.sb.snapshot.return_value.result.return_value = "fss-example"
        self.sb.exec.return_value.result.return_value = SimpleNamespace(returncode=0, stdout="20Gi")
        with patch.object(agent, "snapshot_metadata") as metadata:
            self.assertEqual(agent.take_snapshot(self.sb, "dev1", "shell"), "fss-example")
        metadata.assert_not_called()
        self.assertEqual(agent.harness_from_request_id(self.sb.snapshot.call_args.kwargs["request_id"]), "shell")
        self.assertTrue(self.sb.snapshot.call_args.kwargs["request_id"].endswith("|disk=20Gi"))

    def test_agent_commands_do_not_mistake_shell_for_claude(self):
        with patch.object(agent, "probe_session_meta", return_value=("dev1", "shell")):
            with self.assertRaisesRegex(SystemExit, "shell sandbox"):
                agent.active_harness(self.sb)
        with patch.object(agent, "find_active", return_value=None), \
                patch.object(agent, "latest_ready_snapshot", return_value=ready_snapshot(request_id="cwsa1|dev1|shell|123")):
            args = self.parser.parse_args(["restore", "dev1"])
            with self.assertRaisesRegex(SystemExit, "shell dev1 --snapshot dev1"):
                args.func(args)
        self.run.assert_not_called()

    def test_help_aliases_unknown_flags_and_ordering(self):
        for flag in ["-h", "--help"]:
            with self.assertRaises(SystemExit) as result:
                agent.main(["shell", flag])
            self.assertEqual(result.exception.code, 0)
        for flag in ["--pty", "--add-python", "-m", "--imag", "--unknown"]:
            with self.assertRaises(SystemExit) as result:
                self.parser.parse_args(["shell", "dev1", flag])
            self.assertEqual(result.exception.code, 2)
        # Every ordering of these distinct parser forms, with name before/after flags.
        for options in itertools.permutations([("--cpu", "2"), ("--gpu=any:8",), ("-c", "nvidia-smi")]):
            flags = [v for pair in options for v in pair]
            for argv in [["dev1", *flags], [*flags, "dev1"]]:
                self.invoke(*argv)
        self.assertIn("--snapshot", self.stdout.getvalue())

    def test_cli_suppresses_sdk_error_details(self):
        self.run.side_effect = RuntimeError("synthetic-secret-content")
        self.assertEqual(agent.main(["shell", "dev1"]), 1)
        self.assertNotIn("synthetic-secret-content", self.stderr.getvalue())
        self.assertIn("RuntimeError", self.stderr.getvalue())

    def test_cli_preserves_sdk_authentication_and_entitlement_messages(self):
        for failing_call in (self.list, self.run):
            for message in ("Permission denied: organization is not entitled to create GPU sandboxes",
                            "Authentication failed: invalid API key"):
                self.stderr.seek(0)
                self.stderr.truncate()
                failing_call.side_effect = agent.CWSandboxAuthenticationError(message)
                with self.subTest(call=failing_call, message=message):
                    self.assertEqual(agent.main(["shell", "dev1", "--gpu", "any:1"]), 1)
                    self.assertIn(f"error: {message}\n", self.stderr.getvalue())
                    self.assertNotIn("wandb login", self.stderr.getvalue())
                failing_call.side_effect = None


if __name__ == "__main__":
    unittest.main()
