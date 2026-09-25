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
loader = importlib.machinery.SourceFileLoader("cws_imports", str(Path(__file__).parents[1] / "cws-agent.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
loader.exec_module(app)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "local"
        self.project = self.root / "project"
        self.remote = self.root / "remote"
        for directory in (self.home, self.project, self.remote):
            directory.mkdir()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def discover(self, agent="claude"):
        return app.discover_imports(agent, self.home, self.project)

    def apply(self, items, agent="claude"):
        script = app.IMPORT_APPLY_SCRIPT.replace('pathlib.Path("/workspace/home")', 'pathlib.Path(' + repr(str(self.remote)) + ')')
        return subprocess.run([sys.executable, "-c", script], input=json.dumps({"agent": agent, "items": items}), text=True, capture_output=True)

    def test_discovery_excludes_caches_and_blocks_links(self):
        self.write(self.home / ".claude/skills/review/SKILL.md", "review")
        self.write(self.home / ".claude/skills/review/node_modules/x", "dependency")
        self.write(self.home / ".claude/skills/review/.env", "TOKEN=secret")
        item = self.discover()[0]
        self.assertEqual(item["files"], {"SKILL.md": "review"})
        (self.home / ".claude/skills/review/link").symlink_to(self.home / ".claude/skills/review/SKILL.md")
        self.assertTrue(self.discover()[0]["blocked"])

    def test_dotenv_and_claude_settings_supply_only_referenced_values(self):
        self.write(self.project / ".mcp.json", json.dumps({"mcpServers": {"grafana": {
            "command": "docker", "env": {"TOKEN": "${CW_GRAFANA_MCP_TOKEN}", "URL": "${NODEBOT_GRAFANA_URL}"}}}}))
        self.write(self.project / ".env", 'CW_GRAFANA_MCP_TOKEN="dotenv-secret"\nUNRELATED=not-copied\n')
        self.write(self.home / ".claude/settings.json", json.dumps({"env": {"NODEBOT_GRAFANA_URL": "https://grafana.example"}}))
        with patch.dict(os.environ, {}, clear=True):
            item = self.discover()[0]
        self.assertEqual(item["missing_env"], [])
        self.assertEqual(item["environment"], {"CW_GRAFANA_MCP_TOKEN": "dotenv-secret", "NODEBOT_GRAFANA_URL": "https://grafana.example"})
        self.assertEqual(item["env_sources"]["CW_GRAFANA_MCP_TOKEN"], str(self.project / ".env"))
        self.assertEqual(self.apply([item]).returncode, 0)
        self.assertNotIn("not-copied", (self.remote / ".cws-import-env.json").read_text())

    def test_environment_file_precedence_and_rotation(self):
        self.write(self.home / ".claude/skills/review/SKILL.md", "$TOKEN")
        paths = [self.project / ".env", self.project / ".env.local", self.home / ".claude/settings.json",
                 self.project / ".claude/settings.json", self.project / ".claude/settings.local.json"]
        for index, path in enumerate(paths):
            value = "value-" + str(index)
            self.write(path, json.dumps({"env": {"TOKEN": value}}) if path.suffix == ".json" else "TOKEN=" + value)
        with patch.dict(os.environ, {"TOKEN": "process"}, clear=True):
            self.assertEqual(self.discover()[0]["environment"]["TOKEN"], "value-4")
            explicit = self.root / "selected.env"
            self.write(explicit, "TOKEN=explicit")
            old = app.discover_imports("claude", self.home, self.project, env_files=[explicit])[0]
            self.assertEqual(old["environment"]["TOKEN"], "explicit")
            self.write(explicit, "TOKEN=rotated")
            new = app.discover_imports("claude", self.home, self.project, env_files=[explicit])[0]
            self.assertNotEqual(old["hash"], new["hash"])
            for path in paths[2:]:path.unlink()
            self.assertEqual(self.discover()[0]["environment"]["TOKEN"], "process")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.discover()[0]["environment"]["TOKEN"], "value-1")

    def test_dotenv_parsing_does_not_execute_shell_and_resolves_aliases(self):
        marker = self.root / "executed"
        self.write(self.home / ".claude/skills/review/SKILL.md", "$TOKEN $EMPTY $OPTION $BAD $CYCLE")
        literal = "$(touch " + str(marker) + ")"
        self.write(self.project / ".env", "export RAW='" + literal + "'\nTOKEN=${RAW}\nEMPTY=\nOPTION=${ABSENT:-default}\nBAD=${ABSENT}\nCYCLE=${CYCLE}\n")
        with patch.dict(os.environ, {}, clear=True):
            item = self.discover()[0]
        self.assertEqual(item["environment"], {"TOKEN": literal, "EMPTY": "", "OPTION": "default"})
        self.assertEqual(item["missing_env"], ["BAD", "CYCLE"])
        self.assertFalse(marker.exists())
        self.assertNotIn("RAW", item["environment"])

    def test_explicit_missing_env_file_fails_without_values_in_error(self):
        with self.assertRaisesRegex(SystemExit, "unreadable or invalid --env-file"):
            app.discover_imports("claude", self.home, self.project, env_files=[self.root / "missing.env"])

    def test_native_settings_values_are_not_interpolated(self):
        self.write(self.home / ".claude/skills/review/SKILL.md", "$TOKEN")
        self.write(self.home / ".claude/settings.json", json.dumps({"env": {"TOKEN": "literal${secret}"}}))
        self.assertEqual(self.discover()[0]["environment"], {"TOKEN": "literal${secret}"})

    def test_mcp_headers_are_importable_but_unsafe_commands_and_urls_are_not(self):
        servers = {"ok": {"type": "http", "url": "https://example.com/mcp"},
                   "auth": {"url": "https://example.com", "headers": {"Authorization": "secret"}},
                   "command": {"command": "npx", "args": ["secret"]},
                   "query": {"url": "https://example.com?token=secret"},
                   "disabled": {"url": "https://example.com", "disabled": True}}
        self.write(self.home / ".claude.json", json.dumps({"mcpServers": servers, "oauth": "secret"}))
        items = self.discover()
        self.assertEqual([item["name"] for item in items if not item["blocked"]], ["auth", "ok"])
        self.assertEqual(items[0]["config"]["headers"], {"Authorization": "secret"})
        self.assertNotIn("secret", json.dumps([item for item in items if item["blocked"]]))

    def test_all_three_agents_discover_native_skills(self):
        for agent, folder in [("claude", ".claude/skills"), ("codex", ".agents/skills"), ("devin", ".config/devin/skills")]:
            self.write(self.home / folder / "review/SKILL.md", agent)
            self.assertTrue(any(item["kind"] == "skill" for item in self.discover(agent)))

    def test_updates_replace_owned_files_and_remove_deleted_source_file(self):
        source = self.home / ".claude/skills/review"
        self.write(source / "SKILL.md", "v1")
        self.write(source / "old.txt", "old")
        self.assertEqual(self.apply(self.discover()).returncode, 0)
        self.write(source / "SKILL.md", "v2")
        (source / "old.txt").unlink()
        self.assertEqual(self.apply(self.discover()).returncode, 0)
        self.assertEqual((self.remote / ".claude/skills/review/SKILL.md").read_text(), "v2")
        self.assertFalse((self.remote / ".claude/skills/review/old.txt").exists())

    def test_remote_modifications_block_batch_before_other_writes(self):
        self.write(self.home / ".claude/skills/review/SKILL.md", "v1")
        self.assertEqual(self.apply(self.discover()).returncode, 0)
        self.write(self.remote / ".claude/skills/review/SKILL.md", "remote edit")
        self.write(self.home / ".claude/skills/review/SKILL.md", "v2")
        self.write(self.home / ".claude/skills/aaa/SKILL.md", "new")
        self.assertNotEqual(self.apply(self.discover()).returncode, 0)
        self.assertFalse((self.remote / ".claude/skills/aaa/SKILL.md").exists())
        self.assertEqual((self.remote / ".claude/skills/review/SKILL.md").read_text(), "remote edit")

    def test_native_mcp_merge_preserves_unrelated_config_and_updates(self):
        for agent in ("claude", "codex", "devin"):
            with self.subTest(agent=agent):
                if agent == "codex":
                    self.write(self.remote / ".codex/config.toml", 'model = "example"\n')
                elif agent == "claude":
                    self.write(self.remote / ".claude.json", '{"theme":"dark"}')
                items = [{"id": "mcp:docs", "kind": "mcp", "name": "docs", "url": "https://example.com/mcp", "hash": "v1"}]
                result = self.apply(items, agent)
                self.assertEqual(result.returncode, 0, result.stderr)
                items[0].update(url="https://example.com/v2", hash="v2")
                result = self.apply(items, agent)
                self.assertEqual(result.returncode, 0, result.stderr)
                if agent == "codex":
                    self.assertIn('model = "example"', (self.remote / ".codex/config.toml").read_text())
                elif agent == "claude":
                    self.assertEqual(json.loads((self.remote / ".claude.json").read_text())["theme"], "dark")

    def test_noninteractive_default_never_writes(self):
        self.write(self.home / ".claude/skills/review/SKILL.md", "review")
        items = self.discover()
        sb = types.SimpleNamespace(exec=unittest.mock.Mock())
        with patch.object(app, "discover_imports", return_value=items), \
                patch.object(app, "exec_retry", return_value=types.SimpleNamespace(stdout="{}")), \
                patch.object(sys.stdin, "isatty", return_value=False):
            app.sync_agent_config(sb, app.HARNESSES["claude"], types.SimpleNamespace())
        sb.exec.assert_not_called()

    def test_detached_launch_skips_interactive_import(self):
        self.write(self.home / ".claude/skills/review/SKILL.md", "review")
        items = self.discover()
        output = io.StringIO()
        with patch.object(app, "discover_imports", return_value=items), \
                patch.object(app, "exec_retry", return_value=types.SimpleNamespace(stdout="{}")), \
                patch.object(app, "find_active", return_value=None), \
                patch.object(app, "build_env", return_value={}), \
                patch.object(app, "provision_session", return_value=types.SimpleNamespace(sandbox_id="sb-example", exec=unittest.mock.Mock())), \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input") as prompt, \
                contextlib.redirect_stdout(output):
            self.assertEqual(app.main(["launch", "--name", "dev1", "--detach"]), 0)
        prompt.assert_not_called()
        self.assertIn("skills/MCP import skipped for --detach; run: cws-agent config sync dev1", output.getvalue())

    def test_stdio_copies_literal_environment_without_execution_or_manifest_secrets(self):
        self.write(self.home / ".claude.json", json.dumps({"mcpServers": {
            "portable": {"command": "npx", "args": ["-y", "@example/mcp"], "env": {"API_TOKEN": "SECRET-VALUE"}},
            "laptop": {"command": "/Users/me/bin/mcp"}}}))
        items = self.discover()
        portable = next(item for item in items if item["name"] == "portable")
        self.assertFalse(portable["blocked"])
        self.assertEqual(portable["config"]["env"], {"API_TOKEN": "SECRET-VALUE"})
        result = self.apply([portable])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("SECRET-VALUE", (self.remote / ".cws-imports.json").read_text())
        self.assertNotIn("SECRET-VALUE", result.stdout + result.stderr)
        self.assertEqual(json.loads((self.remote / ".claude.json").read_text())["mcpServers"]["cws-import-portable"]["env"]["API_TOKEN"], "SECRET-VALUE")
        self.assertTrue(next(item for item in items if item["name"] == "laptop")["blocked"])

    def test_reference_values_survive_fresh_remote_process_without_shell_execution(self):
        self.write(self.project / ".mcp.json", json.dumps({"mcpServers": {
            "grafana": {"url": "https://example.com", "headers": {"Authorization": "Bearer ${CW_GRAFANA_MCP_TOKEN}"}},
            "slack": {"command": "node", "args": ["server"], "env": {
                "TOKEN": "${SLACK_MCP_XOXC_TOKEN}", "OPTION": "${UNSET_OPTION:-}", "EMPTY": "${EMPTY}"}}}}))
        danger = "a'\n$(touch " + str(self.root / "executed") + ") `whoami` $HOME \\ end"
        with patch.dict(os.environ, {"CW_GRAFANA_MCP_TOKEN": "grafana-secret", "SLACK_MCP_XOXC_TOKEN": danger, "EMPTY": "", "UNRELATED_SECRET": "do-not-copy"}, clear=True):
            items = self.discover()
        self.assertFalse(any(item["missing_env"] for item in items))
        result = self.apply(items)
        self.assertEqual(result.returncode, 0, result.stderr)
        wrapper = app.AGENT_ENV.replace("/workspace/home", str(self.remote))
        command = wrapper + 'exec ' + sys.executable + ' -c ' + app.shlex.quote('import os, json; print(json.dumps(dict(os.environ)))')
        result = subprocess.run(["sh", "-c", command], text=True, capture_output=True, env={"PATH": os.environ["PATH"]})
        self.assertEqual(result.returncode, 0, result.stderr)
        environment = json.loads(result.stdout)
        self.assertEqual(environment["SLACK_MCP_XOXC_TOKEN"], danger)
        self.assertEqual(environment["CW_GRAFANA_MCP_TOKEN"], "grafana-secret")
        self.assertEqual(environment["EMPTY"], "")
        self.assertNotIn("UNRELATED_SECRET", environment)
        self.assertFalse((self.root / "executed").exists())
        for filename in (".cws-import-env.json", ".cws-import-env.sh", ".cws-imports.json", ".claude.json"):
            self.assertEqual((self.remote / filename).stat().st_mode & 0o777, 0o600)
        self.assertNotIn("grafana-secret", (self.remote / ".cws-imports.json").read_text())

    def test_skills_capture_references_and_explicit_variables_without_copying_local_home(self):
        self.write(self.home / ".claude/skills/review/SKILL.md", 'Use $SHELL_TOKEN and ${DEFAULTED:-fallback} under $HOME.')
        self.write(self.home / ".claude/skills/review/tool.py", 'os.getenv("PY_TOKEN")\nos.environ["PY_KEY"]')
        self.write(self.home / ".claude/skills/review/tool.js", 'process.env.JS_TOKEN; process.env["JS_KEY"]')
        values = dict.fromkeys(["SHELL_TOKEN", "PY_TOKEN", "PY_KEY", "JS_TOKEN", "JS_KEY", "EXPLICIT"], "fixture-value")
        with patch.dict(os.environ, {**values, "HOME": "/laptop", "UNRELATED": "private"}, clear=True):
            items = app.discover_imports("claude", self.home, self.project, env_vars=["EXPLICIT"])
        self.assertEqual(items[0]["environment"], values)
        self.assertEqual(items[0]["missing_env"], [])
        self.assertEqual(self.apply(items).returncode, 0)

    def test_rotated_environment_changes_hash_and_updates_remote_value(self):
        self.write(self.home / ".claude.json", json.dumps({"mcpServers": {"docs": {
            "url": "https://example.com", "headers": {"Authorization": "Bearer ${TOKEN}"}}}}))
        with patch.dict(os.environ, {"TOKEN": "first"}, clear=True):
            old = self.discover()
        self.assertEqual(self.apply(old).returncode, 0)
        with patch.dict(os.environ, {"TOKEN": "second"}, clear=True):
            new = self.discover()
        self.assertNotEqual(old[0]["hash"], new[0]["hash"])
        self.assertEqual(self.apply(new).returncode, 0)
        self.assertIn("second", (self.remote / ".cws-import-env.sh").read_text())
        self.assertNotIn("first", (self.remote / ".cws-import-env.sh").read_text())

    def test_missing_required_values_are_reported_but_defaults_are_optional(self):
        self.write(self.home / ".claude.json", json.dumps({"mcpServers": {"nodebot": {
            "command": "node", "env": {"API_TOKEN": "${NODEBOT_GRAFANA_API_TOKEN}", "OPTION": "${OPTION:-}"}}}}))
        with patch.dict(os.environ, {}, clear=True):
            item = self.discover()[0]
        self.assertEqual(item["missing_env"], ["NODEBOT_GRAFANA_API_TOKEN"])
        self.assertEqual(item["config"]["env"]["OPTION"], "${OPTION:-}")

    def test_unset_local_credential_preserves_saved_value_until_reference_is_removed(self):
        source = self.home / ".claude/skills/review/SKILL.md"
        self.write(source, "$TOKEN")
        with patch.dict(os.environ, {"TOKEN": "saved-secret"}, clear=True):
            self.assertEqual(self.apply(self.discover()).returncode, 0)
        with patch.dict(os.environ, {}, clear=True):
            self.write(source, "Updated skill using $TOKEN")
            self.assertEqual(self.apply(self.discover()).returncode, 0)
            self.assertIn("saved-secret", (self.remote / ".cws-import-env.sh").read_text())
            self.write(source, "Skill no longer needs credentials")
            self.assertEqual(self.apply(self.discover()).returncode, 0)
            self.assertNotIn("saved-secret", (self.remote / ".cws-import-env.sh").read_text())

    def test_conflicting_shared_environment_and_remote_edits_block_whole_batch(self):
        self.write(self.home / ".claude/skills/review/SKILL.md", "$TOKEN")
        with patch.dict(os.environ, {"TOKEN": "first"}, clear=True):
            old = self.discover()
        self.assertEqual(self.apply(old).returncode, 0)
        self.write(self.home / ".claude/skills/new/SKILL.md", "$TOKEN")
        with patch.dict(os.environ, {"TOKEN": "second"}, clear=True):
            items = self.discover()
        result = self.apply([item for item in items if item["name"] == "new"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicting imported environment variable: TOKEN", result.stderr)
        self.assertFalse((self.remote / ".claude/skills/new").exists())
        self.assertEqual(self.apply(items).returncode, 0)
        self.write(self.remote / ".cws-import-env.sh", "remote edit")
        result = self.apply(old)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.remote / ".cws-import-env.sh").read_text(), "remote edit")

    def test_codex_and_devin_environment_keep_native_fields(self):
        self.write(self.home / ".codex/config.toml", '[mcp_servers.docs]\ncommand="node"\nenv={TOKEN="literal"}\nenv_vars=["OTHER_TOKEN"]\n')
        self.write(self.home / ".config/devin/mcp_config.json", json.dumps({"mcpServers": {"docs": {
            "command": "node", "env": {"TOKEN": "${env:OTHER_TOKEN}"}}}}))
        for agent in ("codex", "devin"):
            with self.subTest(agent=agent), patch.dict(os.environ, {"OTHER_TOKEN": "reference"}, clear=True):
                item = self.discover(agent)[0]
                self.assertEqual(item["environment"], {"OTHER_TOKEN": "reference"})
                result = self.apply([item], agent)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("reference", (self.remote / ".cws-imports.json").read_text())

    def test_legacy_manifest_migrates_and_still_detects_remote_mcp_edits(self):
        config = {"command": "node", "env": {"TOKEN": "${TOKEN}"}}
        self.write(self.remote / ".claude.json", json.dumps({"mcpServers": {"cws-import-docs": config}}))
        self.write(self.remote / ".cws-imports.json", json.dumps({"claude": {"mcp:docs": {"hash": "old", "config": config}}}))
        self.write(self.home / ".claude.json", json.dumps({"mcpServers": {"docs": {"command": "node", "env": {"TOKEN": "literal-secret"}}}}))
        items = self.discover()
        self.assertEqual(self.apply(items).returncode, 0)
        manifest = (self.remote / ".cws-imports.json").read_text()
        self.assertNotIn("literal-secret", manifest)
        self.assertIn("config_hash", manifest)
        self.write(self.remote / ".claude.json", json.dumps({"mcpServers": {"cws-import-docs": config}}))
        self.assertNotEqual(self.apply(items).returncode, 0)

    def test_codex_auth_headers_use_native_environment_fields(self):
        self.write(self.home / ".codex/config.toml", '[mcp_servers.docs]\nurl="https://example.com"\nhttp_headers={Authorization="Bearer ${TOKEN}", "X-Key"="${KEY}"}\n')
        item = self.discover("codex")[0]
        self.assertFalse(item["blocked"])
        self.assertEqual(item["config"]["bearer_token_env_var"], "TOKEN")
        self.assertEqual(item["config"]["env_http_headers"], {"X-Key": "KEY"})
        self.assertNotIn("http_headers", item["config"])
        result = self.apply([item], "codex")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_docker_env_passthrough_names_are_references_not_literal_credentials(self):
        self.write(self.home / ".claude.json", json.dumps({"mcpServers": {
            "slack": {"command": "docker", "args": ["run", "--rm", "-e", "API_TOKEN", "--env", "OTHER_TOKEN", "example/mcp"],
                      "env": {"API_TOKEN": "${LOCAL_TOKEN}"}},
            "unsafe": {"command": "docker", "args": ["run", "-e", "API_TOKEN=literal-secret", "example/mcp"]}}}))
        with patch.dict(os.environ, {"LOCAL_TOKEN": "local", "OTHER_TOKEN": "other"}, clear=True):
            items = self.discover()
        slack = next(item for item in items if item["name"] == "slack")
        self.assertFalse(slack["blocked"])
        self.assertEqual(slack["environment"], {"LOCAL_TOKEN": "local", "OTHER_TOKEN": "other"})
        self.assertTrue(next(item for item in items if item["name"] == "unsafe")["blocked"])

    def test_oversized_skill_and_changed_remote_symlink_are_rejected(self):
        source = self.home / ".claude/skills/review/SKILL.md"
        self.write(source, "x" * (app.IMPORT_MAX_FILE + 1))
        self.assertTrue(self.discover()[0]["blocked"])
        self.write(source, "review")
        (self.remote / ".claude").symlink_to(self.home)
        self.assertNotEqual(self.apply(self.discover()).returncode, 0)

    def test_remote_manifest_path_traversal_cannot_delete_files(self):
        self.write(self.home / ".claude/skills/review/SKILL.md", "review")
        self.write(self.remote / ".cws-imports.json", json.dumps({"claude": {"skill:review": {"files": {"../../other": "hash"}}}}))
        result = self.apply(self.discover())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid previously imported path", result.stderr)


class ImportPromptTests(unittest.TestCase):
    def setUp(self):
        self.items = [self.item("mcp", "docs"), self.item("skill", "review"),
                      self.item("mcp", "github", "literal headers are blocked")]

    def item(self, kind, name, blocked=""):
        return dict(id=kind + ":" + name, kind=kind, name=name, blocked=blocked,
                    bytes=42, hash="abcdef0123456789", source="/local/private/config",
                    files={"SKILL.md": "review"} if kind == "skill" else {},
                    url="https://example.com/mcp" if kind == "mcp" and not blocked else None)

    def run_prompt(self, answers=(), checklist_selection=None, previous=None, **options):
        output = io.StringIO()
        operation = types.SimpleNamespace(result=lambda **kw: None)
        proc = types.SimpleNamespace(
            stdin=types.SimpleNamespace(write=unittest.mock.Mock(return_value=operation),
                                        close=lambda: operation),
            result=lambda **kw: types.SimpleNamespace(returncode=0, stdout=""))
        sb = types.SimpleNamespace(exec=unittest.mock.Mock(return_value=proc))
        self.proc, self.sb, self.output = proc, sb, output
        with contextlib.redirect_stdout(output), \
                patch.object(app, "discover_imports", return_value=self.items), \
                patch.object(app, "exec_retry", return_value=types.SimpleNamespace(stdout=json.dumps(previous or {}))), \
                patch.object(app, "terminal_ui", return_value=checklist_selection is not None), \
                patch.object(app, "import_checklist", return_value=checklist_selection) as checklist, \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", side_effect=answers) as prompt:
            self.prompt, self.checklist = prompt, checklist
            app.sync_agent_config(sb, app.HARNESSES["claude"], types.SimpleNamespace(**options))
        return output.getvalue()

    def imported_ids(self):
        return [item["id"] for item in json.loads(self.proc.stdin.write.call_args.args[0])["items"]]

    def test_default_preview_names_only_and_verbose_restores_metadata(self):
        output = self.run_prompt(preview=True)
        self.assertIn("Tools (MCP):\n  docs\n  github (skipped)", output)
        self.assertIn("Skills:\n  review", output)
        for detail in ("42 bytes", "abcdef012345", "/local/private", "https://example.com", "literal headers"):
            self.assertNotIn(detail, output)
        output = self.run_prompt(preview=True, verbose=True)
        for detail in ("42 bytes", "abcdef012345", "/local/private", "https://example.com", "literal headers"):
            self.assertIn(detail, output)
        self.sb.exec.assert_not_called()

    def test_preview_and_confirmation_hide_values_and_only_send_selected_environment(self):
        self.items[0].update(environment={"TOKEN": "selected-secret"}, missing_env=["MISSING"],
                             config={"env": {"LITERAL": "inline-secret"}, "headers": {"Authorization": "header-secret"}})
        self.items[1].update(environment={"UNSELECTED": "unselected-secret"})
        for options, answers in (({"preview": True, "verbose": True}, []), ({}, ["docs"])):
            output = self.run_prompt(answers, **options)
            for secret in ("selected-secret", "inline-secret", "header-secret", "unselected-secret"):
                self.assertNotIn(secret, output)
            self.assertIn("TOKEN", output)
            self.assertIn("MISSING", output)
        payload = json.loads(self.proc.stdin.write.call_args.args[0])
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["environment"], {"TOKEN": "selected-secret"})
        self.assertNotIn("unselected-secret", json.dumps(payload))
        self.assertNotIn("selected-secret", str(self.sb.exec.call_args))

    def test_all_single_letter_and_quoted_legacy_input(self):
        for value in ("a", "A", "'all'", '"a"', "all"):
            with self.subTest(value=value):
                self.run_prompt([value])
                self.assertEqual(self.imported_ids(), ["mcp:docs", "skill:review"])

    def test_typo_and_blocked_selection_reprompt_then_import_once(self):
        output = self.run_prompt(["typo", "github", "'review', docs"])
        self.assertEqual(self.prompt.call_count, 3)
        self.assertEqual(self.sb.exec.call_count, 1)
        self.assertEqual(self.imported_ids(), ["mcp:docs", "skill:review"])
        self.assertEqual(output.count("Try again."), 2)

    def test_explicit_selection_confirmation_reprompts(self):
        self.run_prompt(["maybe", "'y'"], select=["all"])
        self.assertEqual(self.prompt.call_count, 2)
        self.assertEqual(self.sb.exec.call_count, 1)

    def test_checklist_applies_once_without_dumping_names_or_asking_again(self):
        output = self.run_prompt(checklist_selection={"skill:review"})
        self.prompt.assert_not_called()
        self.assertEqual(self.sb.exec.call_count, 1)
        self.assertEqual(self.imported_ids(), ["skill:review"])
        self.assertNotIn("docs", output)
        self.assertNotIn("review", output)
        self.assertIn("Imported 1 items", output)

    def test_checklist_skip_does_not_write(self):
        self.run_prompt(checklist_selection=set())
        self.sb.exec.assert_not_called()
        self.prompt.assert_not_called()

    def test_checklist_only_offers_changed_items(self):
        self.run_prompt(checklist_selection={"skill:review"},
                        previous={"claude": {"mcp:docs": {"hash": self.items[0]["hash"]}}})
        offered = self.checklist.call_args.args[0]
        self.assertNotIn("mcp:docs", [item["id"] for item in offered])
        self.assertEqual(self.imported_ids(), ["skill:review"])

    def test_enter_uploads_all_in_one_step(self):
        self.run_prompt([""])
        self.assertEqual(self.prompt.call_count, 1)
        self.assertEqual(self.imported_ids(), ["mcp:docs", "skill:review"])

    def test_skip_and_eof_never_write(self):
        for answers in (["s"], ["'skip'"], [EOFError()]):
            with self.subTest(answers=answers):
                self.run_prompt(answers)
                self.sb.exec.assert_not_called()

    def test_ambiguous_name_requests_exact_id(self):
        self.items.append(self.item("mcp", "review"))
        output = self.run_prompt(["review", "skill:review"])
        self.assertIn("ambiguous", output)
        self.assertEqual(self.imported_ids(), ["skill:review"])

    def test_size_limit_reprompts_for_smaller_selection(self):
        self.items[0]["bytes"] = app.IMPORT_MAX_BYTES + 1
        output = self.run_prompt(["a", "review"])
        self.assertIn("choose fewer items", output)
        self.assertEqual(self.imported_ids(), ["skill:review"])

    def test_explicit_invalid_selection_fails_without_prompt_or_write(self):
        with self.assertRaisesRegex(SystemExit, "unavailable or blocked"):
            self.run_prompt(select=["typo"], yes=True)
        self.prompt.assert_not_called()
        self.sb.exec.assert_not_called()

    def test_verbose_parser_before_or_after_command(self):
        for argv, handler in [
            (["launch", "--name", "dev1", "--verbose"], "cmd_launch"),
            (["-v", "launch", "--name", "dev1"], "cmd_launch"),
            (["attach", "dev1", "-v"], "cmd_attach"),
            (["resume", "dev1", "--verbose"], "cmd_resume"),
            (["config", "preview", "dev1", "-v"], "cmd_config"),
            (["config", "-v", "sync", "dev1"], "cmd_config"),
        ]:
            with self.subTest(argv=argv), patch.object(app, handler, side_effect=lambda args: args):
                self.assertTrue(app.main(argv).verbose)


if __name__ == "__main__":
    unittest.main()
