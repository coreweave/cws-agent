"""Automatic local-directory disk sizing without provisioning cloud resources."""
import contextlib
import io
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from test_transfer import cli


class DiskSizingTests(unittest.TestCase):
    def inventory(self, size, count=0):
        return cli.LocalDirectoryInventory("/project", range(count), size, count, set())

    def test_floor_headroom_and_rounding(self):
        gib = 1 << 30
        self.assertEqual(cli.local_directory_disk(self.inventory(0)), "10Gi")
        self.assertEqual(cli.local_directory_disk(self.inventory(gib)), "10Gi")
        self.assertEqual(cli.local_directory_disk(self.inventory(10 * gib)), "30Gi")
        self.assertEqual(cli.local_directory_disk(self.inventory(10 * gib + 1)), "35Gi")
        self.assertEqual(cli.local_directory_disk(self.inventory(int(20.4 * gib), 546291)), "65Gi")

    def test_many_small_files_receive_filesystem_headroom(self):
        self.assertEqual(cli.local_directory_disk(self.inventory(0, 1_000_000)), "15Gi")

    def test_scan_uses_upload_exclusions_git_choice_and_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            root = Path(directory)
            for name, size in (("kept", 10), (".git/config", 5000),
                               ("node_modules/file", 10000), ("data/file", 20000)):
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"x" * size)
            (root / "link").symlink_to(root / "node_modules", target_is_directory=True)
            inventory = cli.scan_local_dir(directory, include_git=False, extra_excludes=["data"])
            self.assertEqual(inventory.total, 10)
            self.assertEqual(inventory.count, 1)
            inventory = cli.scan_local_dir(directory, include_git=True, extra_excludes=["data"])
            self.assertEqual(inventory.total, 5010)

    def launch(self, flags, inventory=None, scan_error=None):
        events = []
        with patch.object(cli, "cmd_launch", side_effect=lambda args: args):
            args = cli.main(["launch", "--name", "test", "--detach", *flags])

        def scan(args):
            if not args.add_dir:
                return []
            events.append("scan")
            if scan_error:
                raise scan_error
            self.last_plan = [{"size": inventory.total}] + [{"size": 0}] * max(0, len(inventory.entries) - 1)
            return self.last_plan

        self.provision = None
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                patch.object(cli.os.path, "isdir", return_value=True), patch.object(cli, "find_active", return_value=None), \
                patch.object(cli, "build_env", return_value={}), \
                patch.object(cli, "directory_upload_plan", side_effect=scan) as scanning, \
                patch.object(cli, "provision_session", side_effect=lambda **kw: events.append("provision") or types.SimpleNamespace(sandbox_id="sb-example")) as provision, \
                patch.object(cli, "apply_directory_upload", side_effect=lambda *a, **kw: events.append("package/upload")) as sync:
            self.provision = provision
            self.assertEqual(cli.cmd_launch(args), 0)
        return events, scanning, provision, sync, output.getvalue()

    def test_scan_and_choose_disk_before_provision_reuse_scan_for_upload(self):
        inventory = self.inventory(int(20.4 * (1 << 30)), 546291)
        for verbose in (False, True):
            with self.subTest(verbose=verbose):
                flags = ["--local-dir", "/project", "--no-git", "--exclude", "data"]
                events, scan, provision, sync, output = self.launch(flags + (["--verbose"] if verbose else []), inventory)
                self.assertEqual(events, ["scan", "provision", "package/upload"])
                self.assertEqual(provision.call_args.kwargs["disk"], "65Gi")
                scan.assert_called_once()
                self.assertTrue(scan.call_args.args[0].no_git)
                self.assertEqual(scan.call_args.args[0].exclude, ["data"])
                self.assertIs(sync.call_args.args[2], self.last_plan)
                self.assertEqual("Automatic disk: 65Gi" in output, verbose)

    def test_new_upload_forms_size_disk_before_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large-file"
            # Sparse file: exercise real stat/scan sizes without storing 12 GiB.
            with source.open("wb") as stream:
                stream.truncate(12 << 30)
            other = root / "small-file"
            other.write_text("small")
            cases = (["--add-dir", str(source)],
                     ["--add-dir", str(root), "--remote-path", "/workspace/project/custom/"],
                     ["--add-dir", str(source), "--add-dir", str(other)])
            for flags in cases:
                with self.subTest(flags=flags), contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()), \
                        patch.object(cli, "find_active", return_value=None), \
                        patch.object(cli, "build_env", return_value={}), \
                        patch.object(cli, "provision_session", return_value=types.SimpleNamespace(sandbox_id="sb-example")) as provision, \
                        patch.object(cli, "apply_directory_upload"), \
                        patch.object(cli, "automatic_snapshot"):
                    self.assertEqual(cli.main(["claude", "example", "--detach", *flags]), 0)
                self.assertEqual(provision.call_args.kwargs["disk"], "40Gi")

    def test_explicit_disk_is_not_overridden(self):
        _, _, provision, _, output = self.launch(["--local-dir", "/project", "--disk", "15Gi"], self.inventory(30 << 30))
        self.assertEqual(provision.call_args.kwargs["disk"], "15Gi")
        self.assertNotIn("Automatic disk:", output)

    def test_without_local_directory_keeps_10gi_and_does_not_scan(self):
        events, scan, provision, sync, _ = self.launch([])
        self.assertEqual(events, ["provision"])
        sync.assert_not_called()
        self.assertEqual(provision.call_args.kwargs["disk"], "10Gi")

    def test_scan_error_prevents_provisioning(self):
        with self.assertRaisesRegex(SystemExit, "not a directory"):
            self.launch(["--local-dir", "/missing"], scan_error=SystemExit("not a directory"))
        self.provision.assert_not_called()

    def test_packaging_reuses_inventory_without_rescanning(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            (Path(directory) / "file").write_text("test")
            inventory = cli.scan_local_dir(directory, include_git=True, extra_excludes=[])
            with patch.object(cli, "scan_local_dir") as scan:
                archive, count = cli.build_local_tar(directory, include_git=True, extra_excludes=[], inventory=inventory)
            self.addCleanup(Path(archive).unlink)
            scan.assert_not_called()
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
