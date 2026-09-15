"""One-command launch and operator-managed bot creation, without external writes."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_messaging import app


class TelegramLaunchTests(unittest.TestCase):
    def args(self, *extra):
        with patch.object(app, "cmd_launch", side_effect=lambda args: args):
            return app.main(["launch", "--name", "tg-new", "--telegram", *extra])

    def test_launch_creates_signs_in_and_bridges_with_permission_choice(self):
        args = self.args("--dangerously-skip-permissions")
        events = []
        sb = object()
        with contextlib.redirect_stdout(io.StringIO()), \
                patch.object(app.sys.stdin, "isatty", return_value=True), \
                patch.object(app.sys.stdout, "isatty", return_value=True), \
                patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:test"}), \
                patch.object(app, "find_active", return_value=None), \
                patch.object(app, "build_env", return_value={}), \
                patch.object(app, "provision_session", side_effect=lambda **kw: events.append("create") or sb), \
                patch.object(app, "sync_agent_config"), \
                patch.object(app, "pty_attach", side_effect=lambda *a: events.append("login") or 0), \
                patch.object(app, "cmd_bridge_telegram", side_effect=lambda a: events.append("bridge") or 0) as bridge:
            self.assertEqual(app.cmd_launch(args), 0)
        self.assertEqual(events, ["create", "login", "bridge"])
        self.assertTrue(bridge.call_args.args[0].yolo)
        self.assertEqual(bridge.call_args.args[0].name, "tg-new")

    def test_existing_agent_credential_skips_separate_login(self):
        for agent, credential in [("claude", "CLAUDE_CODE_OAUTH_TOKEN"), ("codex", "OPENAI_API_KEY"),
                                  ("cursor", "CURSOR_API_KEY"), ("opencode", "WANDB_API_KEY")]:
            with self.subTest(agent=agent), patch.object(app, "pty_attach") as login, \
                    patch.object(app, "cmd_bridge_telegram", return_value=0), \
                    contextlib.redirect_stdout(io.StringIO()):
                app.start_launched_telegram(object(), app.HARNESSES[agent], self.args("--agent", agent),
                                            {credential: "test"})
            login.assert_not_called()

    def test_login_cancellation_does_not_start_bridge_or_destroy_sandbox(self):
        with patch.object(app, "pty_attach", return_value=130), \
                patch.object(app, "cmd_bridge_telegram") as bridge, \
                patch.object(app, "stop_failed_sandbox") as stop, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(app.start_launched_telegram(object(), app.HARNESSES["claude"], self.args(), {}), 130)
        bridge.assert_not_called()
        stop.assert_not_called()
        self.assertIn("cws-agent down tg-new", output.getvalue())

    def test_incompatible_flags_fail_before_cloud_calls(self):
        for flags in [("--detach",), ("--claude-env", "env_test"), ("--outpost", "outpost")]:
            with self.subTest(flags=flags), patch.object(app, "find_active") as lookup, self.assertRaises(SystemExit):
                app.cmd_launch(self.args(*flags))
            lookup.assert_not_called()
        with patch.object(app.sys.stdin, "isatty", return_value=False), \
                patch.object(app, "find_active") as lookup, self.assertRaisesRegex(SystemExit, "interactive"):
            app.cmd_launch(self.args())
        lookup.assert_not_called()

    def test_missing_telegram_token_is_requested_before_provisioning(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("pathlib.Path.home", return_value=Path(directory)), \
                patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_MANAGER_BOT_TOKEN": ""}), \
                patch.object(app.sys.stdin, "isatty", return_value=True), \
                patch.object(app.sys.stdout, "isatty", return_value=True), \
                patch.object(app, "find_active", return_value=None), \
                patch.object(app, "build_env", return_value={}), \
                patch.object(app, "telegram_token_prompt", side_effect=KeyboardInterrupt), \
                patch.object(app, "provision_session") as provision, self.assertRaises(KeyboardInterrupt):
            app.cmd_launch(self.args())
        provision.assert_not_called()


class TelegramManagedBotTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch("pathlib.Path.home", return_value=root))
        self.stack.enter_context(patch("secrets.token_hex", return_value="abcd1234abcd"))
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.qr = self.stack.enter_context(patch.object(app, "telegram_pairing_code"))
        self.event = {"update_id": 1, "managed_bot": {
            "user": {"id": 111, "is_bot": False, "username": "owner"},
            "bot": {"id": 222, "is_bot": True, "username": "cws_dev1_abcd1234abcd_bot"},
        }}
        self.batches = iter([[], [self.event]])

    def api(self, token, method, payload):
        if method == "getMe":
            return {"username": "manager_bot", "can_manage_bots": True}
        if method == "getWebhookInfo":
            return {"url": ""}
        if method == "getManagedBotToken":
            self.assertEqual(payload, {"user_id": 222})
            return "222:CHILD_SECRET"
        return next(self.batches)

    def test_creation_link_fetches_token_only_after_owner_confirmation(self):
        with patch.object(app, "telegram_api", side_effect=self.api) as api, \
                patch.object(app, "telegram_confirm", return_value=True):
            token, metadata = app.telegram_create_managed_bot("dev1", "123:MANAGER_SECRET", confirm=True)
        self.assertEqual(token, "222:CHILD_SECRET")
        self.assertEqual(metadata["managed_bot_id"], 222)
        self.assertEqual(metadata["_approved_owner"], 111)
        self.assertIn("https://t.me/newbot/manager_bot/cws_dev1_abcd1234abcd_bot?name=Agent+dev1",
                      self.qr.call_args.args[0])
        self.assertNotIn("SECRET", self.qr.call_args.args[0])
        self.assertNotIn("SECRET", self.output.getvalue())
        self.assertEqual(api.call_args.args[1], "getManagedBotToken")

    def test_declined_owner_never_fetches_token(self):
        with patch.object(app, "telegram_api", side_effect=self.api) as api, \
                patch.object(app, "telegram_confirm", return_value=False), \
                self.assertRaisesRegex(SystemExit, "not deleted"):
            app.telegram_create_managed_bot("dev1", "123:MANAGER_SECRET", confirm=True)
        self.assertFalse(any(call.args[1] == "getManagedBotToken" for call in api.call_args_list))

    def test_unsupported_manager_or_existing_webhook_fails_without_changes(self):
        with patch.object(app, "telegram_api", return_value={}), \
                self.assertRaisesRegex(app.TelegramError, "Bot Management Mode"):
            app.telegram_create_managed_bot("dev1", "123:MANAGER_SECRET")
        with patch.object(app, "telegram_api", side_effect=[
            {"username": "manager_bot", "can_manage_bots": True}, {"url": "https://example.com/SECRET"},
        ]) as api, self.assertRaisesRegex(app.TelegramError, "webhook") as error:
            app.telegram_create_managed_bot("dev1", "123:MANAGER_SECRET")
        self.assertNotIn("SECRET", str(error.exception))
        self.assertEqual(api.call_count, 2)

    def test_other_created_bots_are_not_bound_to_this_sandbox(self):
        other = {"update_id": 0, "managed_bot": {"bot": {"username": "other_bot"}}}
        self.batches = iter([[], [other, self.event]])
        with patch.object(app, "telegram_api", side_effect=self.api), \
                patch.object(app, "telegram_confirm", return_value=True) as confirm:
            app.telegram_create_managed_bot("dev1", "123:MANAGER_SECRET", confirm=True)
        confirm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
