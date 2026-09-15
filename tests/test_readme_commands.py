"""Keep user documentation examples valid without cloud calls or agent execution."""
import argparse
import ast
import contextlib
import io
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_documented_sessions import load_documented_cli


ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
FENCE_PATTERN = r"^```([^\n]*)\n(.*?)^```\s*$"
DOCS = {path: path.read_text() for path in
        [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]}
FENCES = [(language, block) for body in DOCS.values()
          for language, block in re.findall(FENCE_PATTERN, body, re.MULTILINE | re.DOTALL)]
REPO_URL = "https://github.com/coreweave/cws-agent.git"
APP = load_documented_cli()


def documented_commands():
    """Join shell continuations and strip redirects; never execute an example."""
    for language, block in FENCES:
        if language.strip() not in {"bash", "sh", "shell"}:
            continue
        joined = re.sub(r"\\\r?\n", "", block)
        for line in joined.splitlines():
            words = shlex.split(line, comments=True)
            if not words or words[0] not in {"./cws-agent", "cws-agent"}:
                continue
            for index, word in enumerate(words):
                if word in {">", ">>", "<", "|", "||", "&&", ";"}:
                    words = words[:index]
                    break
            yield line, words


class ReadmeCommands(unittest.TestCase):
    def test_checkout_examples_use_the_default_branch(self):
        clones = 0
        for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
            fences = re.findall(FENCE_PATTERN, path.read_text(), re.MULTILINE | re.DOTALL)
            for language, block in fences:
                if language.strip() not in {"bash", "sh", "shell"}:
                    continue
                for line in re.sub(r"\\\r?\n", "", block).splitlines():
                    words = shlex.split(line, comments=True)
                    if words[:2] == ["git", "clone"]:
                        clones += 1
                        with self.subTest(file=path.name, command=line):
                            self.assertNotIn("--branch", words)
                            self.assertEqual(words[-1], REPO_URL)
        self.assertGreaterEqual(clones, 1)

    def test_combined_features_have_documented_examples(self):
        commands = [words[1:] for _, words in documented_commands()]
        for prefix in [("session", "history"), ("session", "resume"),
                       ("session", "restart"), ("session", "start"),
                       ("session", "transfer"), ("config", "preview"),
                       ("config", "sync"), ("bridge", "telegram")]:
            with self.subTest(feature=prefix):
                self.assertTrue(any(tuple(words[:len(prefix)]) == prefix for words in commands))
        for flag in ("--claude-env", "--outpost", "--upload", "--download",
                     "--dangerously-skip-permissions", "--permission-mode", "--no-config-sync"):
            with self.subTest(flag=flag):
                self.assertTrue(any(flag in words for words in commands))
        for term in ("Ctrl+V", "cws-copy", "100 KB", "10 MiB", "5 MiB"):
            self.assertIn(term, "\n".join(DOCS.values()))

    def test_local_documentation_links_resolve(self):
        for path, body in DOCS.items():
            for link in re.findall(r"\]\(([^)]+)\)", body):
                if "://" in link:
                    continue
                relative, _, anchor = link.partition("#")
                target = (path.parent / relative).resolve() if relative else path
                with self.subTest(file=path.name, link=link):
                    self.assertTrue(target.is_file(), f"Missing link target: {target}")
                    if anchor:
                        headings = re.findall(r"^#{1,6} (.+)$", target.read_text(), re.MULTILINE)
                        anchors = {re.sub(r"[^\w -]", "", h.lower()).replace(" ", "-")
                                   for h in headings}
                        self.assertIn(anchor, anchors)

    def test_examples_use_installed_command_and_document_path_setup(self):
        for path, body in DOCS.items():
            with self.subTest(file=path.name):
                self.assertNotIn("./cws-agent", body)
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', README)
        self.assertIn("~/.zshrc", README)
        self.assertLess(len(README.splitlines()), 220, "Keep the quickstart concise")
        self.assertIn("no Managed Agents chat command", README)

    def test_install_link_is_absolute_and_does_not_overwrite(self):
        install = re.search(r"^ln -s .+$", README, re.MULTILINE)
        self.assertIsNotNone(install)
        with tempfile.TemporaryDirectory(prefix="cws docs install ") as directory:
            destination = Path(directory) / "cws-agent"
            command = install.group().replace('"$HOME/.local/bin/cws-agent"',
                                              shlex.quote(str(destination)))
            result = subprocess.run(["bash", "-c", command], cwd=ROOT,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(destination.is_symlink())
            self.assertEqual(destination.readlink(), ROOT / "cws-agent")
            destination.unlink()
            destination.write_text("existing installation\n")
            result = subprocess.run(["bash", "-c", command], cwd=ROOT,
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(destination.read_text(), "existing installation\n")

    def test_shell_fences_have_valid_bash_syntax(self):
        blocks = [(language, block) for language, block in FENCES
                  if language.strip() in {"bash", "sh", "shell"}]
        self.assertTrue(blocks, "README has no shell examples to check")
        for index, (_, block) in enumerate(blocks, 1):
            with self.subTest(block=index):
                result = subprocess.run(["bash", "-n"], input=block, text=True,
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_documented_cli_examples_parse_without_dispatch(self):
        commands = list(documented_commands())
        self.assertTrue(commands, "README has no CLI examples to check")
        # Stub handlers before main builds its parser, so argparse resolves the
        # actual command/flag definitions but can never perform their actions.
        handlers = {name: (lambda args: args) for name in vars(APP)
                    if name.startswith("cmd_") and callable(getattr(APP, name))}
        with patch.multiple(APP, **handlers):
            for line, words in commands:
                with self.subTest(command=line), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(words[0], "cws-agent")
                    try:
                        parsed = APP.main(words[1:])
                    except SystemExit as error:
                        if "--help" in words and error.code == 0:
                            continue
                        self.fail(f"README command rejected by parser (exit {error.code}): {line}")
                    self.assertIsInstance(parsed, argparse.Namespace)

    def test_python_fences_have_valid_syntax(self):
        blocks = [block for language, block in FENCES if language.strip() == "python"]
        for index, block in enumerate(blocks, 1):
            with self.subTest(block=index):
                ast.parse(block, filename=f"README Python block {index}")


if __name__ == "__main__":
    unittest.main()
