"""Pairing security and restart tests: no real bot, clipboard, or cloud calls."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from test_messaging import app


TOKEN = "123:private-test-token"
CHALLENGE = "test-pairing-challenge"


def update(number, text=None, **changes):
    message = {"chat": {"id": 11, "type": "private"},
               "from": {"id": 22, "is_bot": False, "first_name": "Tester"},
               "text": text if text is not None else "/start " + CHALLENGE}
    message.update(changes)
    return {"update_id": number, "message": message}


class TelegramPairingTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.stack.enter_context(patch("pathlib.Path.home", return_value=self.root))
        self.stack.enter_context(patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN}))
        self.stack.enter_context(patch.object(app.sys.stdin, "isatty", return_value=True))
        self.stack.enter_context(patch.object(app.sys.stdout, "isatty", return_value=True))
        self.stack.enter_context(patch.object(app, "require_active", return_value=object()))
        self.stack.enter_context(patch.object(app, "active_harness", return_value=app.HARNESSES["claude"]))
        self.qr = self.stack.enter_context(patch.object(app, "telegram_pairing_code"))
        self.stack.enter_context(patch("secrets.token_urlsafe", return_value=CHALLENGE))
        self.agent = self.stack.enter_context(patch.object(app, "telegram_agent_reply", return_value="done"))
        self.calls = []
        self.batches = iter([[], [update(1)], [], [update(2, "hello")]])
        self.api = self.stack.enter_context(patch.object(app, "telegram_api", side_effect=self.api_call))

    def args(self, **kwargs):
        return types.SimpleNamespace(**dict(dict(name="dev1", timeout=10, allow_chat=None,
                                                allow_user=None, setup=False, confirm_pairing=True), **kwargs))

    def api_call(self, token, method, payload, **kwargs):
        self.calls.append((method, payload))
        if method == "getMe":
            return {"id": 123, "is_bot": True, "username": "cws_test_bot"}
        if method == "getWebhookInfo":
            return {"url": ""}
        if method in {"sendMessage", "sendChatAction", "editMessageText"}:
            return {}
        try:
            return next(self.batches)
        except StopIteration:
            raise KeyboardInterrupt

    def run_until_interrupt(self, args=None, answers=("y", "y")):
        with patch("builtins.input", side_effect=answers), self.assertRaises(KeyboardInterrupt):
            app.cmd_bridge_telegram(args or self.args())

    def profile_path(self):
        return self.root / ".local/state/cws-agent/telegram/connections" / (
            hashlib.sha256(b"dev1").hexdigest()[:24] + ".json")

    def test_pair_save_private_token_and_restart_without_ids_or_env(self):
        self.run_until_interrupt()
        self.qr.assert_called_once_with("https://t.me/cws_test_bot?start=" + CHALLENGE)
        self.assertNotIn(TOKEN, self.output.getvalue())
        self.agent.assert_called_once()
        profile = json.loads(self.profile_path().read_text())
        self.assertEqual(profile["allow_chat"], [11])
        self.assertEqual(profile["allow_user"], [22])
        self.assertEqual(profile["token"], TOKEN)
        self.assertEqual(self.profile_path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.profile_path().parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn(CHALLENGE, self.profile_path().read_text())
        self.batches = iter([[update(3, "next")]])
        self.agent.reset_mock()
        self.qr.reset_mock()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}), patch("builtins.input") as ask:
            self.run_until_interrupt(answers=())
        self.qr.assert_not_called()
        self.agent.assert_called_once()
        ask.assert_not_called()

    def test_no_token_storage_still_saves_pairing_and_reuses_environment(self):
        self.run_until_interrupt(answers=("yes", ""))
        self.assertNotIn("token", json.loads(self.profile_path().read_text()))
        self.assertNotIn(TOKEN, self.profile_path().read_text())
        self.batches = iter([])
        self.qr.reset_mock()
        self.run_until_interrupt(answers=())
        self.qr.assert_not_called()

    def test_default_pairing_and_token_storage_need_no_approval_questions(self):
        with patch("builtins.input", side_effect=AssertionError("unexpected approval")), self.assertRaises(KeyboardInterrupt):
            app.cmd_bridge_telegram(self.args(confirm_pairing=False))
        profile = json.loads(self.profile_path().read_text())
        self.assertEqual(profile["allow_user"], [22])
        self.assertEqual(profile["token"], TOKEN)
        self.agent.assert_called_once()

    def test_default_pairing_can_opt_out_of_token_storage_without_prompt(self):
        self.run_until_interrupt(self.args(confirm_pairing=False, no_save_token=True), answers=())
        self.assertNotIn("token", json.loads(self.profile_path().read_text()))

    def test_automatic_pairing_preserves_first_prompt_after_start(self):
        prompt = update(2, "first prompt")
        self.batches = iter([[], [update(1), prompt], [prompt]])
        self.run_until_interrupt(self.args(confirm_pairing=False), answers=())
        self.agent.assert_called_once()
        self.assertEqual(self.agent.call_args.args[2], "first prompt")
        polls = [payload for method, payload in self.calls if method == "getUpdates"]
        self.assertEqual(polls[-1]["offset"], 3)

    def test_bridge_announces_background_snapshot_completion_once(self):
        with patch.object(app, "background_upload_status", return_value={
                "phase": "ready", "message": "Workspace ready and snapshot saved."}):
            self.run_until_interrupt()
        messages = [payload.get("text") for method, payload in self.calls if method == "sendMessage"]
        self.assertEqual(messages.count("Workspace ready and snapshot saved."), 1)

    def test_hidden_token_prompt_and_no_echo_fallback(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}), \
                patch("getpass.getpass", return_value=TOKEN) as prompt:
            self.run_until_interrupt()
            prompt.assert_called_once()
        self.assertNotIn(TOKEN, self.output.getvalue())

    def test_explicit_setup_prompts_again_instead_of_reusing_a_saved_token(self):
        self.run_until_interrupt()
        self.batches = iter([[], [update(3)], []])
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}), \
                patch("getpass.getpass", return_value=TOKEN) as prompt:
            self.run_until_interrupt(self.args(setup=True))
        prompt.assert_called_once()

    def test_getpass_warning_fails_closed(self):
        import getpass
        import warnings

        def unsafe_prompt(*args):
            warnings.warn("cannot hide input", getpass.GetPassWarning)

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}), \
                patch("getpass.getpass", side_effect=unsafe_prompt), \
                self.assertRaisesRegex(SystemExit, "privately"):
            app.cmd_bridge_telegram(self.args())
        self.api.assert_not_called()

    def test_unmatched_group_bot_and_edited_messages_cannot_pair(self):
        self.batches = iter([[], [
            update(1, "/start wrong"), update(2, "hello"),
            update(3, chat={"id": 11, "type": "group"}),
            update(4, **{"from": {"id": 22, "is_bot": True}}),
            {"update_id": 5, "edited_message": update(5)["message"]},
            update(6), update(7, "do not execute this queued prompt"),
        ], [update(8, "also queued during confirmation")], [update(9, "fresh prompt")]])
        self.run_until_interrupt()
        self.agent.assert_called_once()
        self.assertEqual(self.agent.call_args.args[2], "fresh prompt")

    def test_rejected_account_never_saved_or_sent_to_agent(self):
        with patch("builtins.input", return_value="n"), self.assertRaisesRegex(SystemExit, "canceled"):
            app.cmd_bridge_telegram(self.args())
        self.assertFalse(self.profile_path().exists())
        self.agent.assert_not_called()
        self.assertFalse(any(method == "sendMessage" for method, _ in self.calls))

    def test_remote_account_name_cannot_inject_terminal_controls(self):
        self.batches = iter([[], [update(1, **{"from": {
            "id": 22, "is_bot": False, "first_name": "\x1b]52;steal\x07\nALLOW"}})], []])
        self.run_until_interrupt()
        self.assertNotIn("\x1b", self.output.getvalue())
        self.assertIn("\\u001b", self.output.getvalue())

    def test_expired_challenge_never_pairs(self):
        # deadline=300, first poll begins at 1, response arrives after expiry.
        with patch.object(app.time, "monotonic", side_effect=[0, 1, 1, 301]), \
                self.assertRaisesRegex(SystemExit, "expired"), patch("builtins.input") as ask:
            app.cmd_bridge_telegram(self.args())
        ask.assert_not_called()
        self.assertFalse(self.profile_path().exists())
        self.agent.assert_not_called()

    def test_expiry_during_local_confirmation_never_pairs(self):
        with patch.object(app.time, "monotonic", side_effect=[0, 1, 1, 2, 301, 302]), \
                patch("builtins.input", return_value="y"), self.assertRaisesRegex(SystemExit, "expired"):
            app.cmd_bridge_telegram(self.args())
        self.assertFalse(self.profile_path().exists())

    def test_webhook_is_not_deleted(self):
        def api(token, method, payload, **kwargs):
            if method == "getWebhookInfo":
                return {"url": "https://private.example/secret"}
            return self.api_call(token, method, payload)

        self.api.side_effect = api
        with self.assertRaisesRegex(SystemExit, "webhook") as error:
            app.cmd_bridge_telegram(self.args())
        self.assertNotIn("private.example", str(error.exception))
        self.assertFalse(any(call.args[1] == "deleteWebhook" for call in self.api.call_args_list))
        self.qr.assert_not_called()

    def test_noninteractive_setup_and_partial_allowlists_fail_before_network(self):
        with patch.object(app.sys.stdin, "isatty", return_value=False):
            with self.assertRaisesRegex(SystemExit, "interactive terminal"):
                app.cmd_bridge_telegram(self.args(setup=True))
            with self.assertRaisesRegex(SystemExit, "no matching saved"):
                app.cmd_bridge_telegram(self.args())
        for args in [self.args(allow_chat=[11]), self.args(allow_user=[22]),
                     self.args(allow_chat=[11], allow_user=[22], setup=True),
                     self.args(allow_chat=[-11], allow_user=[22])]:
            with self.assertRaises(SystemExit):
                app.cmd_bridge_telegram(args)
        self.api.assert_not_called()

    def test_setup_can_replace_pairing_but_cancellation_retains_old_profile(self):
        self.run_until_interrupt()
        old = self.profile_path().read_bytes()
        self.batches = iter([[], [update(3)]])
        with patch("builtins.input", return_value="n"), self.assertRaises(SystemExit):
            app.cmd_bridge_telegram(self.args(setup=True))
        self.assertEqual(self.profile_path().read_bytes(), old)
        self.batches = iter([[], [update(4, **{"from": {"id": 33, "is_bot": False}})], []])
        self.run_until_interrupt(self.args(setup=True), answers=("y", "n"))
        profile = json.loads(self.profile_path().read_text())
        self.assertEqual(profile["allow_user"], [33])
        self.assertNotIn("token", profile)

    def test_rotated_token_does_not_silently_reuse_old_allowlists(self):
        self.run_until_interrupt()
        old = self.profile_path().read_bytes()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "456:new-token"}), \
                patch.object(app.sys.stdin, "isatty", return_value=False), \
                self.assertRaisesRegex(SystemExit, "no matching saved"):
            app.cmd_bridge_telegram(self.args())
        self.assertEqual(self.profile_path().read_bytes(), old)

    def test_profile_rejects_symlinks_public_files_and_malformed_data(self):
        self.run_until_interrupt()
        path = self.profile_path()
        path.chmod(0o644)
        with self.assertRaisesRegex(SystemExit, "0600"):
            app.telegram_load_profile(path)
        path.chmod(0o600)
        path.write_text('{"token":"DO_NOT_PRINT"}')
        with self.assertRaises(SystemExit) as error:
            app.telegram_load_profile(path)
        self.assertNotIn("DO_NOT_PRINT", str(error.exception))
        target = self.root / "target"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(SystemExit):
            app.telegram_load_profile(path)

    def test_manual_flags_remain_usable_without_pairing(self):
        self.batches = iter([[], [update(1, "hello")]])
        self.run_until_interrupt(self.args(allow_chat=[11], allow_user=[22]), answers=())
        self.qr.assert_not_called()
        self.agent.assert_called_once()
        self.assertFalse(self.profile_path().exists())

    def test_same_bot_cannot_pair_a_different_sandbox(self):
        self.run_until_interrupt()
        self.qr.reset_mock()
        with self.assertRaisesRegex(SystemExit, "another sandbox"):
            app.cmd_bridge_telegram(self.args(name="other"))
        self.qr.assert_not_called()

    def test_pairing_transport_failure_is_redacted_and_does_not_save_profile(self):
        self.api.side_effect = app.TelegramError("Telegram request failed; check network and bot setup")
        with self.assertRaisesRegex(SystemExit, "Telegram request failed"):
            app.cmd_bridge_telegram(self.args())
        self.assertFalse(self.profile_path().exists())

    def test_ctrl_c_and_eof_leave_no_new_pairing(self):
        for failure in (KeyboardInterrupt, EOFError):
            self.batches = iter([[], [update(1)]])
            with patch("builtins.input", side_effect=failure):
                with self.assertRaises((KeyboardInterrupt, SystemExit)):
                    app.cmd_bridge_telegram(self.args())
            self.assertFalse(self.profile_path().exists())
        self.agent.assert_not_called()

    def test_bot_lock_prevents_two_pairing_consumers(self):
        import fcntl

        directory = self.root / ".local/state/cws-agent/telegram" / hashlib.sha256(TOKEN.encode()).hexdigest()[:24]
        directory.mkdir(parents=True)
        with (directory / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(SystemExit, "already running"):
                app.cmd_bridge_telegram(self.args())
        self.api.assert_not_called()
        self.qr.assert_not_called()

    def test_worker_backend_is_rejected_before_token_prompt(self):
        with patch.object(app, "active_harness", return_value=app.HARNESSES["ant"]), \
                patch("getpass.getpass") as prompt, \
                self.assertRaisesRegex(SystemExit, "not Managed Agents"):
            app.cmd_bridge_telegram(self.args())
        self.api.assert_not_called()
        prompt.assert_not_called()

    def run_managed_creation(self, *, owner=22, child_error=None, readiness_error=False):
        manager_token = "999:MANAGER_TEST_SECRET"
        manager_batches = iter([[], [{"update_id": 1, "managed_bot": {
            "user": {"id": owner, "is_bot": False},
            "bot": {"id": 123, "is_bot": True, "username": "cws_dev1_abcd1234abcd_bot"},
        }}]])
        child_api = self.api_call

        def api(token, method, payload, **kwargs):
            if token == manager_token:
                if method == "getMe":
                    return {"username": "manager_bot", "can_manage_bots": True}
                if method == "getWebhookInfo":
                    return {"url": ""}
                if method == "getManagedBotToken":
                    return TOKEN
                return next(manager_batches)
            if child_error == "identity" and method == "getMe":
                return {"id": 456, "is_bot": True, "username": "wrong_bot"}
            if child_error == "webhook" and method == "getWebhookInfo":
                return {"url": "https://example.com/webhook"}
            if readiness_error and method == "sendMessage" and "listening now" in payload.get("text", ""):
                raise app.TelegramError("chat not started")
            return child_api(token, method, payload, **kwargs)

        self.api.side_effect = api
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_MANAGER_BOT_TOKEN": manager_token}), \
                patch("secrets.token_hex", return_value="abcd1234abcd"), \
                patch.object(app, "telegram_confirm", return_value=True) as confirm, \
                patch.object(app, "telegram_pair") as pair:
            self.confirm, self.pair = confirm, pair
            app.cmd_bridge_telegram(self.args(confirm_pairing=False))

    def test_managed_creation_one_qr_no_confirmation_and_keeps_early_owner_prompt(self):
        early = update(2, "early prompt", chat={"id": 22, "type": "private"})
        stranger = update(3, "untrusted", chat={"id": 33, "type": "private"},
                          **{"from": {"id": 33, "is_bot": False}})
        self.batches = iter([[early, stranger]])
        with self.assertRaises(KeyboardInterrupt):
            self.run_managed_creation()
        self.qr.assert_called_once()
        self.assertIn("https://t.me/newbot/", self.qr.call_args.args[0])
        self.confirm.assert_not_called()
        self.pair.assert_not_called()
        self.agent.assert_called_once()
        self.assertEqual(self.agent.call_args.args[2], "early prompt")
        profile = json.loads(self.profile_path().read_text())
        self.assertEqual(profile["allow_chat"], [22])
        self.assertEqual(profile["allow_user"], [22])
        self.assertNotIn("token", profile)
        self.assertNotIn("_approved_owner", profile)
        self.assertNotIn("SECRET", self.profile_path().read_text())
        messages = [payload["text"] for method, payload in self.calls if method == "sendMessage"]
        self.assertIn("listening now", messages[0])
        polls = [payload for method, payload in self.calls if method == "getUpdates"]
        self.assertEqual(polls[0]["offset"], 0)
        self.assertFalse(any(payload["offset"] == -1 for payload in polls))

    def test_managed_creation_readiness_notification_failure_does_not_block_prompts(self):
        self.batches = iter([[update(1, "hello", chat={"id": 22, "type": "private"})]])
        with self.assertRaises(KeyboardInterrupt):
            self.run_managed_creation(readiness_error=True)
        self.agent.assert_called_once()
        self.assertIn("tap Start", self.output.getvalue())

    def test_managed_creation_checks_child_identity_and_webhook_before_saving(self):
        for failure in ("identity", "webhook"):
            with self.subTest(failure=failure), self.assertRaises(SystemExit):
                self.run_managed_creation(child_error=failure)
            self.assertFalse(self.profile_path().exists())
        self.agent.assert_not_called()

    def test_managed_saved_pairing_recovers_token_without_qr_or_owner_bypass(self):
        self.batches = iter([])
        with self.assertRaises(KeyboardInterrupt):
            self.run_managed_creation()
        self.qr.reset_mock()
        self.batches = iter([[update(3, "resumed", chat={"id": 22, "type": "private"})]])
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_MANAGER_BOT_TOKEN": "999:MANAGER_TEST_SECRET"}), \
                patch.object(app, "telegram_create_managed_bot") as create, \
                patch.object(app, "telegram_pair") as pair, self.assertRaises(KeyboardInterrupt):
            app.cmd_bridge_telegram(self.args())
        create.assert_not_called()
        pair.assert_not_called()
        self.qr.assert_not_called()
        self.agent.assert_called_once()

    def test_parser_accepts_guided_and_manual_forms(self):
        with patch.object(app, "cmd_bridge_telegram", side_effect=lambda args: args):
            for options in ([], ["--setup"], ["--allow-chat", "11", "--allow-user", "22"]):
                args = app.main(["bridge", "telegram", "dev1", *options])
                self.assertEqual(args.name, "dev1")
                self.assertEqual(args.setup, "--setup" in options)


class TelegramQRTests(unittest.TestCase):
    def test_real_qr_renderer_and_narrow_terminal_fallback(self):
        # Segno is supplied by the script's inline dependencies; test it when installed.
        try:
            import segno
        except ImportError:
            self.skipTest("run with --with 'segno>=1.6,<2' to exercise QR rendering")
        url = "https://t.me/cws_test_bot?start=one-time-code"
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
                patch.object(app.shutil, "get_terminal_size", return_value=os.terminal_size((120, 40))), \
                patch.object(segno, "make", wraps=segno.make) as make:
            app.telegram_pairing_code(url)
        make.assert_called_once_with(url, micro=False)
        self.assertIn(url, output.getvalue())
        self.assertTrue(any(character in output.getvalue() for character in "▀▄█"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
                patch.object(app.shutil, "get_terminal_size", return_value=os.terminal_size((20, 20))):
            app.telegram_pairing_code(url)
        self.assertIn("too narrow", output.getvalue())
        self.assertIn(url, output.getvalue())

    def test_missing_qr_dependency_still_prints_link(self):
        output = io.StringIO()
        with patch.dict("sys.modules", {"segno": None}), contextlib.redirect_stdout(output):
            app.telegram_pairing_code("https://t.me/cws_test_bot?start=one-time-code")
        self.assertIn("QR display unavailable", output.getvalue())
        self.assertIn("https://t.me/", output.getvalue())


if __name__ == "__main__":
    unittest.main()
