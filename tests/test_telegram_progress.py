"""Offline progress delivery/lifecycle tests; never send Telegram messages."""
import contextlib
import io
import threading
import unittest
from unittest.mock import patch

from test_messaging import app


class TelegramProgressTests(unittest.TestCase):
    def test_ack_precedes_work_typing_stops_and_final_status_is_last(self):
        calls = []
        typing = threading.Event()

        def api(token, method, payload, **kwargs):
            calls.append((method, payload))
            self.assertEqual(kwargs["timeout"], 5)
            if method == "sendChatAction":
                typing.set()
            return {"message_id": 71}

        with patch.object(app, "telegram_api", side_effect=api):
            with app.TelegramProgress("SECRET", 11, "dev1") as progress:
                self.assertEqual(calls[0][0], "sendMessage")
                self.assertIn("Received", calls[0][1]["text"])
                self.assertTrue(typing.wait(2), "typing indication did not start")
                self.assertTrue(progress.thread.is_alive())
            self.assertFalse(progress.thread.is_alive())
        self.assertEqual(calls[-1][0], "editMessageText")
        self.assertEqual(calls[-1][1]["message_id"], 71)
        self.assertIn("finished", calls[-1][1]["text"])
        self.assertTrue(all(payload["chat_id"] == 11 for _, payload in calls))

    def test_periodic_status_edits_one_message_and_reports_only_waiting(self):
        now = [0]

        class Ticks:
            def is_set(self):
                return now[0] >= 70

            def wait(self, seconds):
                self_test.assertEqual(seconds, 4)
                now[0] += seconds
                return self.is_set()

        self_test = self
        with patch.object(app.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(app, "telegram_api", return_value={}) as api:
            progress = app.TelegramProgress("SECRET", 11, "dev1")
            progress.stop = Ticks()
            progress.message_id = 71
            progress._run()
        edits = [call.args[2] for call in api.call_args_list if call.args[1] == "editMessageText"]
        self.assertEqual(len(edits), 2)
        self.assertTrue(all(item["message_id"] == 71 for item in edits))
        self.assertIn("32s elapsed", edits[0]["text"])
        self.assertIn("64s elapsed", edits[1]["text"])
        self.assertTrue(all("No final response yet" in item["text"] for item in edits))

    def test_status_delivery_failure_never_prevents_or_replays_work(self):
        error_output = io.StringIO()
        executions = []
        with patch.object(app, "telegram_api", side_effect=app.TelegramError("SECRET")), \
                contextlib.redirect_stderr(error_output):
            with app.TelegramProgress("SECRET", 11, "dev1") as progress:
                executions.append("run")
        self.assertEqual(executions, ["run"])
        self.assertFalse(progress.thread.is_alive())
        self.assertNotIn("SECRET", error_output.getvalue())
        self.assertEqual(error_output.getvalue().count("unavailable"), 1)

    def test_exception_and_ctrl_c_stop_feedback_without_claiming_remote_cancellation(self):
        for failure in (RuntimeError, KeyboardInterrupt):
            with self.subTest(failure=failure), \
                    patch.object(app, "telegram_api", return_value={"message_id": 71}) as api:
                with self.assertRaises(failure):
                    with app.TelegramProgress("SECRET", 11, "dev1") as progress:
                        raise failure("private failure detail")
                self.assertFalse(progress.thread.is_alive())
                text = api.call_args.args[2]["text"]
                self.assertIn("may still be running", text)
                self.assertNotIn("private failure", text)


if __name__ == "__main__":
    unittest.main()
