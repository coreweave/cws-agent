import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import shlex
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
loader = importlib.machinery.SourceFileLoader("cws_messaging", str(Path(__file__).parents[1] / "cws-agent"))
spec = importlib.util.spec_from_loader(loader.name, loader)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
loader.exec_module(app)


class MessagingTests(unittest.TestCase):
    def update(self, **changes):
        message = {"chat": {"id": 1, "type": "private"},
                   "from": {"id": 2, "is_bot": False}, "text": "hello"}
        message.update(changes)
        return {"message": message}

    def test_allowlists_require_both_ids_and_private_human(self):
        self.assertEqual(app.telegram_message(self.update(), {1}, {2}), (1, 2, "hello"))
        self.assertIsNone(app.telegram_message(self.update(), {1}, {3}))
        self.assertIsNone(app.telegram_message(self.update(), {3}, {2}))
        self.assertIsNone(app.telegram_message(self.update(chat={"id": 1, "type": "group"}), {1}, {2}))
        self.assertIsNone(app.telegram_message(self.update(**{"from": {"id": 2, "is_bot": True}}), {1}, {2}))
        self.assertIsNone(app.telegram_message({"edited_message": self.update()["message"]}, {1}, {2}))

    def test_claude_continues_same_id_and_quotes_prompt(self):
        result = types.SimpleNamespace(returncode=0, stdout='{"result":"done"}')
        session = {}
        prompt = "$(touch /tmp/bad); 'quoted'"
        with patch.object(app, "exec_retry", return_value=result) as execute:
            self.assertEqual(app.telegram_agent_reply(object(), app.HARNESSES["claude"], prompt, session, 10), "done")
            first = execute.call_args.args[1][2]
            self.assertIn(shlex.quote(prompt), first)
            self.assertIn("--session-id " + session["id"], first)
            self.assertTrue(first.endswith(" </dev/null"), first)
            self.assertEqual(execute.call_args.kwargs["attempts"], 1)
            app.telegram_agent_reply(object(), app.HARNESSES["claude"], "next", session, 10)
            self.assertIn("--resume " + session["id"], execute.call_args.args[1][2])

    def test_failure_does_not_mark_session_started_or_leak_stderr(self):
        result = types.SimpleNamespace(returncode=1, stdout="", stderr="secret")
        session = {}
        with patch.object(app, "exec_retry", return_value=result):
            answer = app.telegram_agent_reply(object(), app.HARNESSES["claude"], "hello", session, 10)
        self.assertNotIn("secret", answer)
        self.assertNotIn("started", session)

    def test_transport_does_not_expose_token(self):
        with patch("urllib.request.urlopen", side_effect=OSError("https://botSECRET")):
            with self.assertRaises(app.TelegramError) as caught:
                app.telegram_api("SECRET", "getUpdates", {})
        self.assertNotIn("SECRET", str(caught.exception))

    def test_transport_errors_are_actionable_without_exposing_urls_or_bodies(self):
        import socket
        import ssl
        import urllib.error

        secret_url = "https://api.telegram.org/botSECRET/getMe"
        cases = [
            (urllib.error.HTTPError(secret_url, 401, "SECRET", {}, io.BytesIO(b"SECRET")), "HTTP 401"),
            (urllib.error.HTTPError(secret_url, 409, "SECRET", {}, io.BytesIO(b"SECRET")), "polling conflict"),
            (urllib.error.HTTPError(secret_url, 429, "SECRET", {}, None), "rate limit"),
            (urllib.error.URLError(ssl.SSLCertVerificationError(1, "SECRET")), "certificate verification"),
            (ssl.SSLCertVerificationError(1, "SECRET"), "certificate verification"),
            (urllib.error.URLError(socket.gaierror(-2, "SECRET")), "DNS"),
            (urllib.error.URLError(TimeoutError("SECRET")), "timed out"),
            (TimeoutError("SECRET"), "timed out"),
            (urllib.error.URLError(OSError("SECRET")), "Cannot connect"),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__, expected=expected), \
                    patch.object(app, "telegram_ssl_context", return_value=object()), \
                    patch("urllib.request.urlopen", side_effect=error), \
                    self.assertRaises(app.TelegramError) as caught:
                app.telegram_api("SECRET", "getMe", {})
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn("SECRET", str(caught.exception))
            self.assertNotIn("https://", str(caught.exception))

    def test_telegram_success_passes_verifying_context_and_redacts_api_errors(self):
        context = object()
        with patch.object(app, "telegram_ssl_context", return_value=context), \
                patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"ok":true,"result":{"id":1}}')) as request:
            self.assertEqual(app.telegram_api("SECRET", "getMe", {}), {"id": 1})
            self.assertIs(request.call_args.kwargs["context"], context)
        with patch.object(app, "telegram_ssl_context", return_value=context), \
                patch("urllib.request.urlopen", return_value=io.BytesIO(
                    b'{"ok":false,"error_code":401,"description":"SECRET"}')), \
                self.assertRaises(app.TelegramError) as caught:
            app.telegram_api("SECRET", "getMe", {})
        self.assertIn("HTTP 401", str(caught.exception))
        self.assertNotIn("SECRET", str(caught.exception))

    def test_native_tls_context_requires_verification_and_honors_explicit_ca(self):
        import ssl
        from unittest.mock import Mock

        context = Mock()
        truststore = types.SimpleNamespace(SSLContext=Mock(return_value=context))
        with patch.dict(sys.modules, {"truststore": truststore}), \
                patch.dict("os.environ", {"SSL_CERT_FILE": "/approved/company-ca.pem", "SSL_CERT_DIR": ""}):
            self.assertIs(app.telegram_ssl_context(), context)
        truststore.SSLContext.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations.assert_called_once_with(cafile="/approved/company-ca.pem", capath=None)
        with patch.dict(sys.modules, {"truststore": None}):
            fallback = app.telegram_ssl_context()
        self.assertEqual(fallback.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(fallback.check_hostname)

    def test_tls_setup_failure_is_redacted(self):
        with patch.object(app, "telegram_ssl_context", side_effect=OSError("SECRET")), \
                patch("urllib.request.urlopen") as request, self.assertRaises(app.TelegramError) as caught:
            app.telegram_api("SECRET", "getMe", {})
        request.assert_not_called()
        self.assertIn("TLS trust setup", str(caught.exception))
        self.assertNotIn("SECRET", str(caught.exception))

    def test_invalid_persisted_session_is_rejected(self):
        with self.assertRaises(ValueError), patch.object(app, "exec_retry") as execute:
            app.telegram_agent_reply(object(), app.HARNESSES["claude"], "hi", {"id": "; rm -rf"}, 10)
        execute.assert_not_called()

    def test_polling_discards_history_filters_senders_and_deduplicates(self):
        args = types.SimpleNamespace(name="dev1", timeout=10, allow_chat=[1], allow_user=[2])
        unauthorized = self.update(**{"from": {"id": 9, "is_bot": False}})
        unauthorized["update_id"] = 11
        allowed = self.update()
        allowed["update_id"] = 12
        calls = []
        batches = iter([[{"update_id": 10}], [unauthorized, allowed, allowed]])

        def api(token, method, payload, **kwargs):
            calls.append((method, payload))
            if method in {"sendMessage", "sendChatAction", "editMessageText"}:
                return {}
            try:
                return next(batches)
            except StopIteration:
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as temporary, \
                patch("pathlib.Path.home", return_value=Path(temporary)), \
                patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123:abc"}), \
                patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "active_harness", return_value=app.HARNESSES["claude"]), \
                patch.object(app, "telegram_api", side_effect=api), \
                patch.object(app, "telegram_agent_reply", return_value="**done**") as agent:
            with self.assertRaises(KeyboardInterrupt):
                app.cmd_bridge_telegram(args)
            agent.assert_called_once()
            self.assertEqual(calls[0][1]["offset"], -1)
            self.assertEqual(calls[-1][1]["offset"], 13)
            self.assertEqual(sum(method == "sendMessage" for method, _ in calls), 2)
            final_reply = [payload for method, payload in calls if method == "sendMessage"][-1]
            self.assertEqual(final_reply["text"], "done")
            self.assertEqual(final_reply["entities"], [{"type": "bold", "offset": 0, "length": 4}])
            self.assertNotIn("parse_mode", final_reply)

    def test_startup_transport_error_exits_cleanly_without_traceback(self):
        args = types.SimpleNamespace(name="dev1", timeout=10, allow_chat=[1], allow_user=[2])
        failure = app.TelegramError("Telegram request failed; check network and bot setup")
        with tempfile.TemporaryDirectory() as temporary, \
                patch("pathlib.Path.home", return_value=Path(temporary)), \
                patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123:abc"}), \
                patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "active_harness", return_value=app.HARNESSES["claude"]), \
                patch.object(app, "telegram_api", side_effect=failure):
            with self.assertRaises(SystemExit) as caught:
                app.cmd_bridge_telegram(args)
            self.assertFalse(list(Path(temporary).rglob("state.json")))
        self.assertEqual(str(caught.exception), "error: " + str(failure))

    def test_session_id_and_pending_state_persist_before_execution(self):
        session, saved = {}, []

        def execute(*args, **kwargs):
            self.assertEqual(saved[-1]["id"], session["id"])
            self.assertTrue(saved[-1]["uncertain"])
            return types.SimpleNamespace(returncode=0, stdout='{"result":"done"}')

        with patch.object(app, "exec_retry", side_effect=execute):
            app.telegram_agent_reply(object(), app.HARNESSES["claude"], "hello", session, 10,
                                     persist=lambda: saved.append(dict(session)))
        self.assertEqual(len(saved), 2)
        self.assertTrue(saved[-1]["started"])
        self.assertNotIn("uncertain", saved[-1])

    def test_crash_during_remote_run_blocks_same_conversation_after_restart(self):
        args = types.SimpleNamespace(name="dev1", timeout=10, allow_chat=[1], allow_user=[2])
        first = dict(self.update(), update_id=1)
        next_prompt = dict(self.update(), update_id=2)
        replies = []
        batches = iter([[], [first]])

        def api(token, method, payload, **kwargs):
            if method == "sendMessage":
                replies.append(payload["text"])
                return {}
            if method in {"sendChatAction", "editMessageText"}:
                return {}
            try:
                return next(batches)
            except StopIteration:
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as temporary, \
                patch("pathlib.Path.home", return_value=Path(temporary)), \
                patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123:abc"}), \
                patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "active_harness", return_value=app.HARNESSES["claude"]), \
                patch.object(app, "telegram_api", side_effect=api):
            with patch.object(app, "exec_retry", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                app.cmd_bridge_telegram(args)
            state = json.loads(next(Path(temporary).rglob("state.json")).read_text())
            saved_session = state["sessions"]["1:2:claude"]
            self.assertTrue(saved_session["uncertain"])
            self.assertIn("id", saved_session)
            self.assertEqual(state["offset"], 2)
            batches = iter([[next_prompt]])
            with patch.object(app, "exec_retry") as execute, self.assertRaises(KeyboardInterrupt):
                app.cmd_bridge_telegram(args)
            execute.assert_not_called()
            self.assertEqual(len(replies), 3)  # two acknowledgements and the recovery reply
            self.assertIn("/new", replies[-1])


if __name__ == "__main__":
    unittest.main()
