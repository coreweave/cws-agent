"""Exercise installation in temporary homes without network or shell changes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATH_LINE = 'export PATH="$HOME/.local/bin:$PATH"'


class Installer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home with spaces"
        self.home.mkdir()
        self.bin = self.home / ".local/bin"
        self.bin.mkdir(parents=True)
        self.installed = self.bin / "cws-agent"
        self.mocks = Path(self.temp.name) / "mocks"
        self.mocks.mkdir()
        self.env = {"HOME": str(self.home), "PATH": f"{self.mocks}:/usr/bin:/bin"}
        self.executable(self.mocks / "uv", "#!/bin/sh\nexit 0\n")
        self.executable(self.mocks / "curl", "#!/bin/sh\necho 'unexpected network request' >&2\nexit 99\n")

    def executable(self, path, content):
        path.write_text(content)
        path.chmod(0o755)

    def install(self, local=True):
        command = ["/bin/sh", str(ROOT / "install.sh")]
        if local:
            command.append(str(ROOT / "cws-agent.py"))
        return subprocess.run(command, env=self.env, capture_output=True, text=True)

    def test_local_install_is_independent_and_repeatable(self):
        rc = self.home / ".zshrc"
        rc.write_text("# existing settings without a trailing newline")
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installed.read_bytes(), (ROOT / "cws-agent.py").read_bytes())
        self.assertFalse(self.installed.is_symlink())
        self.assertTrue(os.access(self.installed, os.X_OK))
        self.assertTrue(rc.read_text().startswith("# existing settings"))
        for name in (".zshrc", ".bashrc", ".bash_profile"):
            self.assertEqual((self.home / name).read_text().count(PATH_LINE), 1)
        self.assertEqual(list(self.bin.glob(".cws-agent.*")), [])
        result = subprocess.run(
            ["/bin/sh", "-c", '. "$HOME/.bash_profile"; command -v cws-agent'],
            env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), str(self.installed))

    def test_preserves_checkout_behind_existing_symlink(self):
        checkout = self.home / "checkout-command"
        checkout.write_text("old checkout")
        self.installed.symlink_to(checkout)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(checkout.read_text(), "old checkout")
        self.assertFalse(self.installed.is_symlink())

    def test_honors_custom_zsh_directory_and_existing_bash_login_file(self):
        zdotdir = self.home / "zsh config"
        self.env["ZDOTDIR"] = str(zdotdir)
        profile = self.home / ".profile"
        profile.write_text("# existing login settings\n")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(PATH_LINE, (zdotdir / ".zshrc").read_text())
        self.assertIn(PATH_LINE, profile.read_text())
        self.assertFalse((self.home / ".bash_profile").exists())

    def test_failed_download_preserves_previous_install_and_shell_config(self):
        self.installed.write_text("old command")
        result = self.install(local=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.installed.read_text(), "old command")
        self.assertFalse((self.home / ".zshrc").exists())
        self.assertEqual(list(self.bin.glob(".cws-agent.*")), [])

    def test_remote_install_bootstraps_uv(self):
        (self.mocks / "uv").unlink()
        self.executable(self.mocks / "curl", '''#!/bin/sh
set -eu
test "$1" = -fsSL
test "$3" = -o
case "$2" in
  https://raw.githubusercontent.com/coreweave/cws-agent/main/cws-agent.py)
    printf '#!/bin/sh\\necho command works\\n' > "$4"
    ;;
  https://astral.sh/uv/install.sh)
    cat > "$4" <<'INSTALL'
#!/bin/sh
set -eu
test "$UV_NO_MODIFY_PATH" = 1
if IFS= read -r unexpected; then exit 99; fi
printf '#!/bin/sh\\nexit 0\\n' > "$UV_INSTALL_DIR/uv"
chmod 755 "$UV_INSTALL_DIR/uv"
INSTALL
    ;;
  *) exit 99 ;;
esac
''')
        script = (ROOT / "install.sh").read_text() + "\nprintf 'stream-finished\\n'\n"
        result = subprocess.run(["/bin/sh"], input=script, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stream-finished", result.stdout)
        self.assertTrue(os.access(self.bin / "uv", os.X_OK))
        result = subprocess.run([str(self.installed)], capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "command works")

    def test_truncated_stream_does_not_change_installation_or_shell_files(self):
        self.installed.write_text("old command")
        script = (ROOT / "install.sh").read_text()
        for truncated in (script[:script.index("mv -f")], script[:script.rindex('main "$@"')]):
            with self.subTest(length=len(truncated)):
                subprocess.run(["/bin/sh"], input=truncated, env=self.env,
                               capture_output=True, text=True)
                self.assertEqual(self.installed.read_text(), "old command")
                self.assertFalse((self.home / ".zshrc").exists())
                self.assertFalse((self.home / ".bashrc").exists())
                self.assertFalse((self.home / ".bash_profile").exists())
                self.assertEqual(list(self.bin.glob(".cws-agent.*")), [])

    def test_directory_at_command_path_is_not_modified(self):
        self.installed.mkdir()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is a directory", result.stderr)
        self.assertEqual(list(self.installed.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
