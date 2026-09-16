"""Execute Codex bootstrap against offline release-layout fixtures; no agent/network."""
import ast
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


tree = ast.parse((Path(__file__).resolve().parents[1] / "cws-agent.py").read_text())
BOOTSTRAP = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "CODEX_BOOTSTRAP"
                         for target in node.targets))


class CodexBootstrap(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cws-codex-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.agent = self.root / "agent"
        self.commands = self.root / "commands"
        self.commands.mkdir()
        self.archive = self.root / "package.tar.gz"
        self.url_log = self.root / "urls"
        self.executable(self.commands / "curl", '#!/bin/sh\n' +
                        'printf "%s\\n" "$2" >> "$CODEX_TEST_URL_LOG"\n' +
                        'cp "$CODEX_TEST_PACKAGE" "$4"\n')
        self.executable(self.commands / "uname", '#!/bin/sh\nprintf "%s\\n" "$CODEX_TEST_ARCH"\n')

    def executable(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)

    def package(self, *, missing=None):
        # Layout verified against the official rust-v0.153.4 package archive.
        files = {
            "bin/codex": b'#!/bin/sh\nprintf "codex fixture-version\\n"\n',
            "bin/codex-code-mode-host": b"#!/bin/sh\nexit 0\n",
            "codex-path/rg": b"#!/bin/sh\nexit 0\n",
            "codex-resources/bwrap": b"#!/bin/sh\nexit 0\n",
            "codex-resources/zsh/bin/zsh": b"#!/bin/sh\nexit 0\n",
            "codex-package.json": json.dumps({"layoutVersion": 1, "version": "fixture-version",
                                             "entrypoint": "bin/codex", "resourcesDir": "codex-resources",
                                             "pathDir": "codex-path"}).encode(),
        }
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, content in files.items():
                if name == missing:
                    continue
                entry = tarfile.TarInfo(name)
                entry.mode = 0o644 if name.endswith(".json") else 0o755
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))

    def run_bootstrap(self, architecture="x86_64"):
        script = BOOTSTRAP.replace("/opt/agent", str(self.agent)).replace(
            "/workspace", str(self.root / "workspace"))
        return subprocess.run(["sh", "-c", script], text=True, capture_output=True,
                              env={"PATH": str(self.commands) + ":/usr/bin:/bin",
                                   "OPENAI_API_KEY": "", "CODEX_TEST_ARCH": architecture,
                                   "CODEX_TEST_PACKAGE": str(self.archive),
                                   "CODEX_TEST_URL_LOG": str(self.url_log)})

    def test_complete_package_keeps_relative_resources_and_is_idempotent(self):
        self.package()
        first = self.run_bootstrap()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("codex fixture-version", first.stdout)
        for path in ("bin/codex", "bin/codex-code-mode-host", "codex-path/rg",
                     "codex-resources/bwrap", "codex-resources/zsh/bin/zsh"):
            self.assertTrue(os.access(self.agent / path, os.X_OK), path)
        metadata = json.loads((self.agent / "codex-package.json").read_text())
        self.assertEqual(metadata["entrypoint"], "bin/codex")
        self.assertEqual(self.run_bootstrap().returncode, 0)
        self.assertEqual(len(self.url_log.read_text().splitlines()), 1)
        self.assertIn("codex-package-x86_64-unknown-linux-musl.tar.gz", self.url_log.read_text())

    def test_repairs_old_single_binary_install_with_matching_package(self):
        self.package()
        self.executable(self.agent / "bin/codex", "#!/bin/sh\necho old-single-binary\n")
        result = self.run_bootstrap("aarch64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("codex fixture-version", result.stdout)
        self.assertNotIn("old-single-binary", result.stdout)
        self.assertIn("codex-package-aarch64-unknown-linux-musl.tar.gz", self.url_log.read_text())
        (self.agent / "codex-resources/bwrap").unlink()
        self.assertEqual(self.run_bootstrap().returncode, 0)
        self.assertTrue((self.agent / "codex-resources/bwrap").exists())

    def test_missing_companion_fails_bootstrap_before_claiming_success(self):
        self.package(missing="bin/codex-code-mode-host")
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("codex fixture-version", result.stdout)


if __name__ == "__main__":
    unittest.main()
