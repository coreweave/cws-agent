"""Exercise merge semantics against a real local filesystem, without sandbox calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch
from test_terminal import agent


class DirectoryUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.target = self.root / 'remote'
        self.stage = self.root / 'stage'
        self.stage.mkdir()
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        original = open
        def local_open(path, *args, **kwargs):
            if str(path) == '/proc/self/mountinfo':
                return io.StringIO(self.mounts)
            return original(path, *args, **kwargs)
        self.mounts = ''
        self.enterContext(patch('builtins.open', side_effect=local_open))

    def args(self, source=None, **kwargs):
        return types.SimpleNamespace(add_dir=[str(source or self.source)], remote_path=str(self.target)+'/', **kwargs)

    def stage_plan(self, args=None):
        plan = agent.directory_upload_plan(args or self.args())
        manifest = [{k:v for k,v in item.items() if k != 'local'} for item in plan]
        (self.stage/'manifest.json').write_text(json.dumps(manifest))
        with tarfile.open(self.stage/'files.tar', 'w') as archive:
            for index, item in enumerate(plan):
                if stat.S_ISREG(item['mode']):
                    with agent.open_upload_source(item['local']) as stream:
                        member = tarfile.TarInfo(str(index))
                        member.size = os.fstat(stream.fileno()).st_size
                        archive.addfile(member, stream)
        return plan

    def test_directory_contents_file_modes_and_empty_directories(self):
        (self.source/'empty').mkdir()
        (self.source/'script').write_text('example')
        (self.source/'script').chmod(0o751)
        self.stage_plan()
        agent.install_directory_upload(str(self.stage), 'apply')
        self.assertEqual((self.target/'script').read_text(), 'example')
        self.assertEqual((self.target/'script').stat().st_mode & 0o777, 0o751)
        self.assertTrue((self.target/'empty').is_dir())

    def test_preserve_remote_and_explicit_overwrite_protect_git(self):
        (self.source/'file').write_text('incoming')
        (self.source/'.git').mkdir()
        (self.source/'.git/config').write_text('incoming git')
        (self.target/'.git').mkdir(parents=True)
        (self.target/'.git/config').write_text('remote git')
        (self.target/'file').write_text('remote')
        self.stage_plan()
        agent.install_directory_upload(str(self.stage), 'apply')
        self.assertEqual((self.target/'file').read_text(), 'remote')
        agent.install_directory_upload(str(self.stage), 'apply', True)
        self.assertEqual((self.target/'file').read_text(), 'incoming')
        self.assertEqual((self.target/'.git/config').read_text(), 'remote git')

    def test_single_file_rename_and_repeated_sources_require_directory(self):
        source = self.source/'file'; source.write_text('data')
        args = self.args(source); args.remote_path = str(self.target/'renamed')
        self.stage_plan(args)
        agent.install_directory_upload(str(self.stage), 'apply')
        self.assertEqual((self.target/'renamed').read_text(), 'data')
        other = self.source/'other'; other.write_text('other')
        args.add_dir.append(str(other)); args.remote_path = str(self.root/'absent')
        self.stage_plan(args)
        with self.assertRaisesRegex(ValueError, 'destination directory'):
            agent.install_directory_upload(str(self.stage))

    def test_source_parent_replaced_by_link_never_reads_unselected_file(self):
        nested = self.source/'nested'; nested.mkdir()
        (nested/'config').write_text('selected')
        private = self.root/'private'; private.mkdir(); (private/'config').write_text('private sentinel')
        plan = agent.directory_upload_plan(self.args())
        (nested/'config').unlink(); nested.rmdir(); nested.symlink_to(private)
        item = next(item for item in plan if item['relative'].endswith('/config'))
        with self.assertRaises(OSError):
            agent.open_upload_source(item['local'])

    def test_destination_parent_swapped_after_plan_is_rejected(self):
        (self.source/'nested').mkdir(); (self.source/'nested/file').write_text('incoming')
        (self.target/'nested').mkdir(parents=True)
        private = self.root/'private'; private.mkdir(); (private/'file').write_text('keep')
        self.stage_plan(); agent.install_directory_upload(str(self.stage))
        (self.target/'nested').rmdir(); (self.target/'nested').symlink_to(private)
        with self.assertRaises((ValueError, OSError)):
            agent.install_directory_upload(str(self.stage), 'apply', True)
        self.assertEqual((private/'file').read_text(), 'keep')

    def test_protected_paths_double_slash_and_volume_escapes(self):
        for remote in ('/etc/config', '//etc/config', '/workspace/home', '/workspace/project/../home'):
            args = self.args(); args.remote_path = remote
            with self.subTest(remote=remote), self.assertRaises(ValueError):
                agent.directory_upload_plan(args)
        self.target = self.root/'data\\backup'
        (self.source/'file').write_text('data')
        self.stage_plan()
        escaped = str(self.target).replace('\\','\\134')
        self.mounts = f'1 0 0:1 / {escaped} rw - tmpfs tmpfs rw\n'
        with self.assertRaisesRegex(ValueError, 'mounted volume'):
            agent.install_directory_upload(str(self.stage))

    def test_project_harness_configuration_directories_are_regular_content(self):
        for name in ('.claude', '.codex', '.opencode'):
            (self.source/name).mkdir()
            (self.source/name/'settings.json').write_text('{}')
        self.stage_plan()
        result = agent.install_directory_upload(str(self.stage), 'apply')
        self.assertEqual(result['copied'], 3)
        self.assertEqual((self.target/'.claude/settings.json').read_text(), '{}')

    def test_preserve_type_collisions_skips_subtrees_and_keeps_unrelated_files(self):
        (self.source/'directory').mkdir()
        (self.source/'directory/child').write_text('incoming')
        (self.source/'file').write_text('incoming')
        (self.source/'new').write_text('new')
        self.target.mkdir()
        (self.target/'directory').write_text('keep file')
        (self.target/'file').mkdir()
        (self.target/'file/child').write_text('keep child')
        self.stage_plan()
        result = agent.install_directory_upload(str(self.stage), 'apply')
        self.assertEqual(result['preserved'], 2)
        self.assertEqual(result['copied'], 1)
        self.assertEqual((self.target/'directory').read_text(), 'keep file')
        self.assertEqual((self.target/'file/child').read_text(), 'keep child')
        self.assertEqual((self.target/'new').read_text(), 'new')

    def test_overwrite_type_conflict_preflight_does_not_copy_other_files(self):
        (self.source/'first').write_text('new')
        (self.source/'collision').write_text('incoming')
        (self.target/'collision').mkdir(parents=True)
        self.stage_plan()
        with self.assertRaisesRegex(ValueError, 'file/directory collision'):
            agent.install_directory_upload(str(self.stage), 'apply', True)
        self.assertFalse((self.target/'first').exists())

    def test_normalized_source_aliases_are_duplicates(self):
        link = self.root/'alias'; link.symlink_to(self.source)
        for equivalent in (str(self.source)+'/', str(link)):
            args = self.args(); args.add_dir.append(equivalent)
            with self.assertRaisesRegex(ValueError, 'equivalent paths'):
                agent.directory_upload_plan(args)

    def test_root_symlink_is_resolved_and_nested_internal_link_is_preserved(self):
        (self.source/'data.txt').write_text('data')
        (self.source/'link').symlink_to('data.txt')
        root_link = self.root/'alias'; root_link.symlink_to(self.source)
        self.stage_plan(self.args(root_link))
        agent.install_directory_upload(str(self.stage), 'apply')
        self.assertTrue((self.target/'link').is_symlink())
        self.assertEqual(os.readlink(self.target/'link'), 'data.txt')
        self.assertEqual((self.target/'link').read_text(), 'data')

    def test_external_source_link_names_the_path(self):
        link = self.source/'private-link'; link.symlink_to('../outside')
        with self.assertRaisesRegex(ValueError, 'private-link'):
            agent.directory_upload_plan(self.args())

    def test_git_directories_are_not_counted_as_preserved_files(self):
        (self.source/'.git/objects').mkdir(parents=True)
        (self.source/'.git/config').write_text('incoming')
        (self.target/'.git/objects').mkdir(parents=True)
        (self.target/'.git/config').write_text('keep')
        self.stage_plan()
        result = agent.install_directory_upload(str(self.stage), 'apply')
        self.assertEqual(result['preserved'], 1)

    def test_upload_flags_apply_to_all_entry_points(self):
        for command in ('shell', 'connect', 'restore', 'resume', 'claude'):
            with patch.object(agent, {'shell': 'cmd_shell', 'connect': 'cmd_attach',
                    'restore': 'cmd_resume', 'resume': 'cmd_unified_resume', 'claude': 'cmd_launch'}[command],
                    side_effect=lambda args: args):
                args = agent.main([command, 'demo', '--add-dir', str(self.source),
                                   '--transfer-timeout', '2h', '--no-git', '--exclude', 'secret'])
            self.assertEqual(args.transfer_timeout, 7200)
            self.assertTrue(args.no_git)
            self.assertEqual(args.exclude, ['secret'])

    def test_repeated_upload_preserves_leaf_symlinks_without_following_them(self):
        (self.source/'data.txt').write_text('data')
        (self.source/'link').symlink_to('data.txt')
        self.stage_plan()
        agent.install_directory_upload(str(self.stage), 'apply')
        result = agent.install_directory_upload(str(self.stage), 'apply')
        self.assertEqual(result['preserved'], 2)
        self.assertEqual(os.readlink(self.target/'link'), 'data.txt')
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            agent.install_directory_upload(str(self.stage), 'apply', True)

    def test_incoming_directory_over_remote_symlink_preserves_entire_subtree(self):
        (self.source/'nested').mkdir(); (self.source/'nested/file').write_text('incoming')
        outside = self.root/'outside'; outside.mkdir(); (outside/'file').write_text('keep')
        self.target.mkdir(); (self.target/'nested').symlink_to(outside)
        self.stage_plan()
        result = agent.install_directory_upload(str(self.stage), 'apply')
        self.assertEqual(result['preserved'], 1)
        self.assertEqual((outside/'file').read_text(), 'keep')

    def test_overlapping_sources_with_type_conflict_fail_before_writes_in_both_orders(self):
        (self.source/'x').write_text('file')
        other = self.root/'other'; (other/'x').mkdir(parents=True)
        (other/'x/child').write_text('child')
        for sources in ((self.source, other), (other, self.source)):
            args = self.args(); args.add_dir = list(map(str, sources))
            self.stage_plan(args)
            with self.assertRaisesRegex(ValueError, 'same file'):
                agent.install_directory_upload(str(self.stage), 'apply')
            self.assertFalse(self.target.exists())

    def test_preserved_directory_collision_skips_nested_git_before_probing_it(self):
        (self.source/'sub/.git').mkdir(parents=True)
        (self.source/'sub/.git/config').write_text('incoming')
        self.target.mkdir()
        outside = self.root/'outside'; outside.mkdir()
        for symlink in (False, True):
            target = self.target/'sub'
            if symlink:
                target.symlink_to(outside)
            else:
                target.write_text('keep')
            self.stage_plan()
            result = agent.install_directory_upload(str(self.stage), 'apply')
            self.assertEqual(result['preserved'], 1)
            self.assertEqual(list(outside.iterdir()), [])
            target.unlink()

    def test_cross_filesystem_fallback_refuses_staging_symlink_substitution(self):
        import errno
        (self.source/'file').write_text('selected')
        plan = self.stage_plan()
        (self.stage/'files.tar').unlink()
        index = next(i for i, item in enumerate(plan) if stat.S_ISREG(item['mode']))
        staged = self.stage/str(index); staged.write_text('selected')
        outside = self.root/'outside'; outside.write_text('do not copy')
        original_link = os.link
        def cross_device(source, destination, **kwargs):
            if str(source) == str(staged):
                staged.unlink(); staged.symlink_to(outside)
                raise OSError(errno.EXDEV, 'cross-device')
            return original_link(source, destination, **kwargs)
        with patch.object(os, 'link', side_effect=cross_device), self.assertRaises(OSError):
            agent.install_directory_upload(str(self.stage), 'apply')
        self.assertFalse((self.target/'file').exists())
        self.assertEqual(outside.read_text(), 'do not copy')
