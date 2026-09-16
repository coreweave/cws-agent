"""Native OpenCode/Cursor configuration imports without agents or network calls."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sdk = types.ModuleType("cwsandbox")
sdk.AuthStrategy = types.SimpleNamespace(WANDB="wandb", COREWEAVE_API_KEY="coreweave_api_key")
sdk.CWSandboxAuthenticationError = type("CWSandboxAuthenticationError", (Exception,), {})
sdk.FileSystemSnapshotOptions = sdk.ResourceOptions = sdk.Sandbox = object
sys.modules.setdefault("cwsandbox", sdk)
loader = importlib.machinery.SourceFileLoader("cws_native_imports", str(Path(__file__).parents[1] / "cws-agent.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
loader.exec_module(app)


class NativeImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home, self.project, self.remote = [self.root / name for name in ("home", "project", "remote")]
        for path in (self.home, self.project, self.remote):
            path.mkdir()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))

    def discover(self, agent):
        with patch.dict(os.environ, {}, clear=True):
            return app.discover_imports(agent, self.home, self.project)

    def apply(self, agent, items):
        script = app.IMPORT_APPLY_SCRIPT.replace('pathlib.Path("/workspace/home")',
                                                'pathlib.Path(' + repr(str(self.remote)) + ')')
        return subprocess.run([sys.executable, "-c", script],
                              input=json.dumps({"agent": agent, "items": items}),
                              capture_output=True, text=True)

    def test_opencode_jsonc_and_native_mcp_round_trip(self):
        self.write(self.home / ".config/opencode/opencode.jsonc", '''{
            // Native config comments and trailing commas.
            "mcp": {
                "local": {"type": "local", "command": ["npx", "-y", "example-mcp"],
                          "environment": {"TOKEN": "{env:TOOL_TOKEN}"}, "timeout": 9000,},
                "remote": {"type": "remote", "url": "https://example.com/mcp",
                           "headers": {"Authorization": "Bearer {env:TOOL_TOKEN}"}, "oauth": false,},
            },
        }''')
        self.write(self.project / ".env", "TOOL_TOKEN=private-fixture\nUNRELATED=never-import\n")
        items = self.discover("opencode")
        self.assertEqual([item["blocked"] for item in items], ["", ""])
        self.assertEqual(items[0]["config"]["command"], ["npx", "-y", "example-mcp"])
        self.assertEqual(items[0]["config"]["environment"], {"TOKEN": "{env:TOOL_TOKEN}"})
        self.assertEqual(items[0]["environment"], {"TOOL_TOKEN": "private-fixture"})
        result = self.apply("opencode", items)
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.remote / ".config/opencode/opencode.json"
        native = json.loads(target.read_text())["mcp"]
        self.assertEqual(native["cws-import-local"], items[0]["config"])
        self.assertEqual(native["cws-import-remote"]["type"], "remote")
        self.assertIs(native["cws-import-remote"]["oauth"], False)
        self.assertFalse((self.remote / ".config/devin").exists())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("never-import", (self.remote / ".cws-import-env.json").read_text())
        self.assertEqual(self.apply("opencode", items).returncode, 0)

    def test_cursor_native_mcp_round_trip(self):
        self.write(self.home / ".cursor/mcp.json", {"mcpServers": {
            "remote": {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ${env:TOOL_TOKEN}"}},
            "local": {"command": "npx", "args": ["example-mcp"], "env": {"TOKEN": "${env:TOOL_TOKEN}"}},
        }})
        self.write(self.project / ".env", "TOOL_TOKEN=fixture\n")
        items = self.discover("cursor")
        self.assertEqual([item["blocked"] for item in items], ["", ""])
        self.assertEqual(items[0]["config"]["type"], "stdio")
        result = self.apply("cursor", items)
        self.assertEqual(result.returncode, 0, result.stderr)
        native = json.loads((self.remote / ".cursor/mcp.json").read_text())["mcpServers"]
        self.assertEqual(native["cws-import-remote"]["headers"]["Authorization"], "Bearer ${env:TOOL_TOKEN}")
        self.assertFalse((self.remote / ".claude.json").exists())

    def test_native_skills_and_project_precedence(self):
        for agent, folder, project_folder in (("opencode", ".config/opencode", ".opencode"), ("cursor", ".cursor", ".cursor")):
            with self.subTest(agent=agent):
                self.write(self.home / folder / "skills/review/SKILL.md", "global")
                self.write(self.project / project_folder / "skills/review/SKILL.md", "project")
                items = self.discover(agent)
                self.assertEqual(items[0]["files"]["SKILL.md"], "project")
                result = self.apply(agent, items)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.remote / folder / "skills/review/SKILL.md").read_text(), "project")

    def test_compatible_skills_are_discovered(self):
        self.write(self.home / ".claude/skills/shared/SKILL.md", "shared")
        self.write(self.project / ".agents/skills/common/SKILL.md", "common")
        for agent in ("opencode", "cursor"):
            self.assertEqual([item["name"] for item in self.discover(agent)], ["common", "shared"])

    def test_cursor_category_skills_do_not_follow_symlink_directories(self):
        self.write(self.home / ".cursor/skills/shipping/review/SKILL.md", "nested")
        self.write(self.home / "outside/hidden/SKILL.md", "outside")
        (self.home / ".cursor/skills/linked").symlink_to(self.home / "outside", target_is_directory=True)
        items = self.discover("cursor")
        self.assertEqual([item["name"] for item in items], ["review"])
        self.assertEqual(self.apply("cursor", items).returncode, 0)
        self.assertEqual((self.remote / ".cursor/skills/review/SKILL.md").read_text(), "nested")

    def test_runtime_config_locations_and_inline_config_are_not_importable_environment(self):
        names = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "CURSOR_CONFIG_DIR", "CURSOR_DATA_DIR",
                 "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT")
        self.write(self.home / ".agents/skills/location/SKILL.md", " ".join("$" + name for name in names))
        self.write(self.project / ".env", "\n".join(name + "=local-only" for name in names))
        for agent in ("opencode", "cursor"):
            with self.subTest(agent=agent):
                item = self.discover(agent)[0]
                self.assertEqual(item["environment"], {})
                self.assertEqual(item["needs_env"], [])
                for name in names:
                    with self.assertRaisesRegex(SystemExit, "portable environment variable"):
                        app.discover_imports(agent, self.home, self.project, env_vars=[name])

    def test_disabled_and_nonportable_opencode_configs_are_blocked(self):
        self.write(self.project / "opencode.json", {"mcp": {
            "disabled": {"enabled": False},
            "bad-array": {"type": "local", "command": "npx"},
            "local-path": {"type": "local", "command": ["/Users/me/tool"]},
            "file-reference": {"type": "remote", "url": "https://example.com", "headers": {"Token": "{file:secret}"}},
            "cwd": {"type": "local", "command": ["npx"], "cwd": "/Users/me"},
        }})
        items = self.discover("opencode")
        self.assertTrue(all(item["blocked"] for item in items))
        self.assertEqual(next(item for item in items if item["name"] == "disabled")["blocked"], "disabled locally")

    def test_cursor_workspace_and_env_file_require_manual_mapping(self):
        self.write(self.project / ".cursor/mcp.json", {"mcpServers": {
            "path": {"command": "node", "args": ["${workspaceFolder}/mcp.js"]},
            "env-file": {"command": "node", "envFile": ".env"},
        }})
        self.assertTrue(all(item["blocked"] for item in self.discover("cursor")))

    def test_opencode_oauth_references_are_imported_not_printed(self):
        self.write(self.project / "opencode.json", {"mcp": {"auth": {
            "type": "remote", "url": "https://example.com/mcp",
            "oauth": {"clientId": "id", "clientSecret": "{env:CLIENT_SECRET}"},
        }}})
        self.write(self.project / ".env", "CLIENT_SECRET=private-oauth-fixture")
        item = self.discover("opencode")[0]
        self.assertEqual(item["environment"], {"CLIENT_SECRET": "private-oauth-fixture"})
        self.assertEqual(item["config"]["oauth"]["clientSecret"], "{env:CLIENT_SECRET}")
        with patch.object(app, "discover_imports", return_value=[item]), patch.object(app, "exec_retry", return_value=types.SimpleNamespace(stdout="{}")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                app.sync_agent_config(object(), types.SimpleNamespace(name="opencode"),
                                      types.SimpleNamespace(preview=True, verbose=True))
        self.assertIn("OAuth configuration included", output.getvalue())
        self.assertNotIn("private-oauth-fixture", output.getvalue())

    def test_verbose_opencode_command_preview_handles_array(self):
        self.write(self.project / "opencode.json", {"mcp": {"local": {
            "type": "local", "command": ["npx", "-y", "mcp-tool"],
        }}})
        items = self.discover("opencode")
        with patch.object(app, "discover_imports", return_value=items), patch.object(app, "exec_retry", return_value=types.SimpleNamespace(stdout="{}")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                app.sync_agent_config(object(), types.SimpleNamespace(name="opencode"),
                                      types.SimpleNamespace(preview=True, verbose=True))
        self.assertIn("mcp-tool", output.getvalue())

    def test_jsonc_parser_preserves_strings(self):
        self.assertEqual(app.import_jsonc('{/* comment */"url":"https://example.com/a//b", "text":",}",}'),
                         {"url": "https://example.com/a//b", "text": ",}"})

    def test_modified_remote_native_config_is_not_overwritten(self):
        for agent, path, key, config in (
            ("opencode", ".config/opencode/opencode.json", "mcp", {"type": "remote", "url": "https://example.com"}),
            ("cursor", ".cursor/mcp.json", "mcpServers", {"url": "https://example.com"}),
        ):
            with self.subTest(agent=agent):
                self.write(self.home / path, {key: {"tool": config}})
                items = self.discover(agent)
                self.assertEqual(self.apply(agent, items).returncode, 0)
                remote = self.remote / path
                self.write(remote, {key: {"cws-import-tool": {"url": "https://changed.example"}}})
                before = remote.read_text()
                self.assertNotEqual(self.apply(agent, items).returncode, 0)
                self.assertEqual(remote.read_text(), before)


if __name__ == "__main__":
    unittest.main()
