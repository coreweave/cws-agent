import asyncio
import contextlib
import copy
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import discord

sdk = types.ModuleType("cwsandbox")
sdk.AuthStrategy = types.SimpleNamespace(WANDB="wandb", COREWEAVE_API_KEY="coreweave_api_key")
sdk.CWSandboxAuthenticationError = type("CWSandboxAuthenticationError", (Exception,), {})
sdk.FileSystemSnapshotOptions = sdk.ResourceOptions = sdk.Sandbox = object
sys.modules.setdefault("cwsandbox", sdk)
loader = importlib.machinery.SourceFileLoader("cws_discord", str(Path(__file__).parents[1] / "cws-agent.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
loader.exec_module(app)


class DiscordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.args = types.SimpleNamespace(name="dev1", timeout=10, allow_user=[2], user=None, server=None)
        self.state = {"sessions": {}}
        self.saved = []
        self.client = app.discord_client(object(), app.HARNESSES["claude"], self.args,
                                         self.state, lambda: self.saved.append(copy.deepcopy(self.state)))
        await self.client.setup_hook()
        self.receipt = types.SimpleNamespace(edit=AsyncMock())
        self.channel = Mock(spec_set=discord.DMChannel)
        self.channel.id = 1
        self.channel.send = AsyncMock(return_value=self.receipt)
        self.channel.typing = AsyncMock()

    async def asyncTearDown(self):
        await self.client.close()

    def message(self, **changes):
        values = dict(channel=self.channel, author=types.SimpleNamespace(id=2, bot=False),
                      webhook_id=None, id=10, content="hello", attachments=[], guild=None, mentions=[])
        values.update(changes)
        return types.SimpleNamespace(**values)

    async def test_only_allowlisted_humans_in_dms(self):
        cases = [dict(author=types.SimpleNamespace(id=3, bot=False)),
                 dict(author=types.SimpleNamespace(id=2, bot=True)),
                 dict(channel=Mock(spec=discord.TextChannel)),
                 dict(channel=Mock(spec=discord.GroupChannel)),
                 dict(webhook_id=5), dict(attachments=[object()]), dict(content=" ")]
        with patch.object(app, "telegram_agent_reply") as run:
            for case in cases:
                await self.client.on_message(self.message(**case))
        run.assert_not_called()
        self.channel.send.assert_not_called()
        self.assertFalse(self.saved)
        self.assertEqual(self.client.intents.value, 1 << 12)
        self.assertEqual(self.client.allowed_mentions.to_dict()["parse"], [])

    async def test_session_continues_receipt_precedes_run_and_duplicates_are_ignored(self):
        def execute(*args, **kwargs):
            self.assertTrue(self.channel.send.await_count)
            self.assertIn("last_message", self.saved[-1]["sessions"]["1:2:claude"])
            return types.SimpleNamespace(returncode=0, stdout='{"result":"**done**"}')

        with patch.object(app, "workspace_access", return_value=contextlib.nullcontext()), \
                patch.object(app, "exec_retry", side_effect=execute) as run:
            await self.client.on_message(self.message())
            sid = self.state["sessions"]["1:2:claude"]["agent"]["id"]
            await self.client.on_message(self.message())
            self.assertEqual(run.call_count, 1)
            await self.client.on_message(self.message(id=11))
            self.assertIn("--resume " + sid, run.call_args.args[1][2])
            self.assertEqual(run.call_count, 2)
        self.assertTrue(any(s["sessions"]["1:2:claude"].get("agent", {}).get("uncertain") for s in self.saved))
        self.assertEqual(self.channel.send.await_args.args, ("**done**",))
        self.receipt.edit.assert_awaited_with(content="Finished.")

    async def test_timeout_blocks_next_prompt_without_replay_or_error_leak(self):
        with patch.object(app, "workspace_access", return_value=contextlib.nullcontext()), \
                patch.object(app, "exec_retry", side_effect=TimeoutError("SECRET")) as run:
            await self.client.on_message(self.message())
            await self.client.on_message(self.message(id=11))
            run.assert_called_once()
        self.assertTrue(self.state["sessions"]["1:2:claude"]["agent"]["uncertain"])
        self.assertIn("/new", self.channel.send.await_args.args[0])
        self.assertNotIn("SECRET", str(self.channel.send.await_args_list))

    async def test_restart_retains_session_and_deduplicates_received_message(self):
        with patch.object(app, "workspace_access", return_value=contextlib.nullcontext()), \
                patch.object(app, "exec_retry", return_value=types.SimpleNamespace(returncode=0, stdout='{"result":"ok"}')) as run:
            await self.client.on_message(self.message())
            restored = copy.deepcopy(self.saved[-1])
            sid = restored["sessions"]["1:2:claude"]["agent"]["id"]
            await self.client.close()
            self.client = app.discord_client(object(), app.HARNESSES["claude"], self.args, restored, lambda: None)
            await self.client.setup_hook()
            await self.client.on_message(self.message())
            run.assert_called_once()
            await self.client.on_message(self.message(id=11))
            self.assertIn("--resume " + sid, run.call_args.args[1][2])

    async def test_new_and_session_commands_do_not_prompt_agent(self):
        self.state["sessions"]["1:2:claude"] = {"agent": {"id": "test-session"}}
        with patch.object(app, "telegram_agent_reply") as run:
            await self.client.on_message(self.message(content="/session"))
            self.assertIn("claude --resume test-session", self.channel.send.await_args.args[0])
            await self.client.on_message(self.message(id=11, content="/new"))
            self.assertEqual(self.state["sessions"]["1:2:claude"]["agent"], {})
            await self.client.on_message(self.message(id=12, content="/model"))
            run.assert_not_called()

    async def test_progress_and_delivery_failures_do_not_repeat_execution(self):
        self.channel.send.side_effect = RuntimeError("SECRET")
        with patch.object(app, "workspace_access", return_value=contextlib.nullcontext()), \
                patch.object(app, "exec_retry", return_value=types.SimpleNamespace(returncode=0, stdout='{"result":"ok"}')) as run:
            await self.client.on_message(self.message())
            await self.client.on_message(self.message())
        run.assert_called_once()
        self.assertNotIn("uncertain", self.state["sessions"]["1:2:claude"]["agent"])

    async def test_busy_queues_prompt_without_mutating_active_state(self):
        with patch.object(app, "workspace_access", return_value=contextlib.nullcontext()), \
                patch.object(app, "exec_retry", return_value=types.SimpleNamespace(returncode=0, stdout='{"result":"ok"}')) as run:
            async with self.client.busy:
                waiting = asyncio.create_task(self.client.on_message(self.message()))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                run.assert_not_called()
                self.assertFalse(self.saved)
                self.assertIn("Queued", self.channel.send.await_args.args[0])
            await waiting
            run.assert_called_once()

    async def test_long_unicode_reply_is_intact_markdown_attachment(self):
        reply = "```python\n" + "😀" * 1100 + "\n```"
        await self.client.send_reply(self.channel, reply)
        attachment = self.channel.send.await_args.kwargs["file"]
        self.assertEqual(attachment.filename, "response.md")
        self.assertEqual(attachment.fp.read().decode(), reply)

    async def test_typing_and_elapsed_status(self):
        with patch.object(app.time, "monotonic", side_effect=[0, 31]), \
                patch("asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.client.progress(self.channel, self.receipt)
        self.channel.typing.assert_awaited_once()
        self.receipt.edit.assert_awaited_once_with(content="Working… (31s elapsed)")

    async def test_progress_uses_real_discord_channel_typing_api(self):
        state = self.client._connection
        state.loop = asyncio.get_running_loop()
        channel = discord.DMChannel._from_message(state, 1)
        with patch.object(state.http, "send_typing", new_callable=AsyncMock) as typing, \
                patch("asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.client.progress(channel, None)
        typing.assert_awaited_once_with(1)

    async def test_crashed_progress_task_cannot_discard_completed_agent_reply(self):
        with patch.object(self.client, "progress", side_effect=AttributeError("progress failure")), \
                patch.object(app, "workspace_access", return_value=contextlib.nullcontext()), \
                patch.object(app, "exec_retry", return_value=types.SimpleNamespace(returncode=0, stdout='{"result":"saved answer"}')) as run:
            await self.client.on_message(self.message())
        run.assert_called_once()
        self.channel.send.assert_awaited_with("saved answer")
        self.receipt.edit.assert_awaited_with(content="Finished.")

    def server_message(self, *, user=3, message_id=100, channel=None, content="<@99> hello", mentions=True):
        state = self.client._connection
        state.loop = asyncio.get_running_loop()
        bot = {"id": "99", "username": "agent", "discriminator": "0", "avatar": None, "bot": True}
        state.user = discord.ClientUser(state=state, data=bot)
        if not hasattr(self, "guild"):
            self.guild = discord.Guild(state=state, data={"id": "5", "owner_id": "2",
                "roles": [{"id": "5", "name": "@everyone", "permissions": "1024"}],
                "channels": [{"id": "10", "type": 0, "name": "general", "position": 0}]})
        return discord.Message(state=state, channel=channel or self.guild.get_channel(10), data={
            "id": str(message_id), "type": 0, "content": content,
            "author": {"id": str(user), "username": f"user{user}", "discriminator": "0", "avatar": None},
            "mentions": [bot] if mentions else []})

    def thread_data(self, thread_id=100, private=False):
        return {"id": str(thread_id), "parent_id": "10", "owner_id": "99", "name": "conversation",
                "type": 12 if private else 11, "message_count": 0, "member_count": 1,
                "thread_metadata": {"archived": False, "auto_archive_duration": 1440,
                                    "archive_timestamp": "2026-01-01T00:00:00+00:00", "locked": False}}

    async def test_server_requires_explicit_mention_and_public_channel_in_selected_server(self):
        self.args.server = 5
        message = self.server_message()  # User 3 is intentionally not on the DM allowlist.
        self.assertEqual(self.client.route(message), ("guild:5:100:claude", "hello", True))
        self.assertIsNone(self.client.route(self.server_message(mentions=False)))
        self.assertIsNone(self.client.route(self.server_message(content="reply without a typed mention")))
        self.args.server = 6
        self.assertIsNone(self.client.route(message))
        self.args.server = 5
        private = discord.TextChannel(state=self.client._connection, guild=self.guild, data={
            "id": "11", "type": 0, "name": "private", "position": 1,
            "permission_overwrites": [{"id": "5", "type": 0, "allow": "0", "deny": "1024"}]})
        self.assertIsNone(self.client.route(self.server_message(channel=private)))
        thread = discord.Thread(state=self.client._connection, guild=self.guild, data=self.thread_data(private=True))
        self.state["sessions"]["guild:5:100:claude"] = {}
        self.assertIsNone(self.client.route(self.server_message(channel=thread)))

    async def test_thread_shares_session_between_people_and_new_mentions_are_isolated(self):
        self.args.server = 5
        first = self.server_message()
        http = self.client._connection.http
        with patch.object(http, "start_thread_with_message", new_callable=AsyncMock,
                          side_effect=[self.thread_data(), self.thread_data(200)]) as create, \
                patch.object(http, "add_reaction", new_callable=AsyncMock), \
                patch.object(http, "send_typing", new_callable=AsyncMock), \
                patch.object(discord.Thread, "send", new_callable=AsyncMock, return_value=self.receipt) as send, \
                patch.object(app, "workspace_access", return_value=contextlib.nullcontext()), \
                patch.object(app, "exec_retry", return_value=types.SimpleNamespace(returncode=0, stdout='{"result":"done"}')) as run:
            await self.client.on_message(first)
            create.assert_awaited_once()
            sid = self.state["sessions"]["guild:5:100:claude"]["agent"]["id"]
            self.assertIn("user ID 3", run.call_args.args[1][2])
            thread = discord.Thread(state=self.client._connection, guild=self.guild, data=self.thread_data())
            await self.client.on_message(self.server_message(user=4, message_id=101, channel=thread))
            self.assertIn("--resume " + sid, run.call_args.args[1][2])
            self.assertIn("user ID 4", run.call_args.args[1][2])
            await self.client.on_message(self.server_message(user=4, message_id=102, channel=thread, content="<@99> /new"))
            self.assertIn("Only the person", send.await_args.args[0])
            self.assertEqual(run.call_count, 2)
            await self.client.on_message(self.server_message(message_id=200))
            self.assertEqual(create.await_count, 2)
            self.assertNotEqual(self.state["sessions"]["guild:5:200:claude"]["agent"]["id"], sid)
            await self.client.on_message(first)
            self.assertEqual(run.call_count, 3)

    async def test_thread_creation_failure_never_runs_agent(self):
        self.args.server = 5
        message = self.server_message()
        with patch.object(self.client._connection.http, "add_reaction", new_callable=AsyncMock), \
                patch.object(message._state.http, "start_thread_with_message", side_effect=RuntimeError("SECRET")), \
                patch.object(discord.Message, "reply", new_callable=AsyncMock) as reply, \
                patch.object(app, "telegram_agent_reply") as run:
            await self.client.on_message(message)
        run.assert_not_called()
        self.assertIn("Create Public Threads", reply.await_args.args[0])
        self.assertNotIn("SECRET", reply.await_args.args[0])

    def existing_thread(self, thread_id=100):
        self.args.server = 5
        self.server_message()
        data = self.thread_data(thread_id)
        data["owner_id"] = "88"  # A different bot created the thread.
        return discord.Thread(state=self.client._connection, guild=self.guild, data=data)

    async def test_join_other_bots_thread_keeps_own_session_and_does_not_trigger_on_bots(self):
        thread = self.existing_thread()
        with patch.object(discord.Thread, "send", new_callable=AsyncMock, return_value=self.receipt), \
                patch.object(self.client, "progress", new_callable=AsyncMock), \
                patch.object(self.client, "thread_context", new_callable=AsyncMock) as history, \
                patch.object(app, "workspace_access", return_value=contextlib.nullcontext()), \
                patch.object(app, "exec_retry", return_value=types.SimpleNamespace(returncode=0, stdout='{"result":"done"}')) as run:
            await self.client.on_message(self.server_message(message_id=110, channel=thread))
            sid = self.state["sessions"]["guild:5:100:claude"]["agent"]["id"]
            await self.client.on_message(self.server_message(user=4, message_id=111, channel=thread))
            self.assertIn("--resume " + sid, run.call_args.args[1][2])
            bot_reply = self.server_message(message_id=112, channel=thread)
            bot_reply.author.bot = True
            await self.client.on_message(bot_reply)
            await self.client.on_message(self.server_message(message_id=113, channel=thread, mentions=False))
            self.assertEqual(run.call_count, 2)
            history.assert_not_called()  # History is opt-in.
            await self.client.on_message(self.server_message(message_id=210, channel=self.existing_thread(200)))
            self.assertNotEqual(self.state["sessions"]["guild:5:200:claude"]["agent"]["id"], sid)

    async def test_history_is_bounded_ordered_and_attributes_other_bot_text(self):
        thread = self.existing_thread()
        current = self.server_message(message_id=150, channel=thread)
        starter = self.server_message(message_id=100, content="remember violet", mentions=False)
        other = self.server_message(user=88, message_id=120, channel=thread, content="violet", mentions=False)
        other.author.bot = True
        own = self.server_message(user=99, message_id=130, channel=thread, content="Finished.")
        blank = self.server_message(message_id=140, channel=thread, content="")
        items = [blank, own, other]

        async def history(**kwargs):
            self.assertEqual(kwargs["limit"], 30)
            self.assertIs(kwargs["before"], current)
            self.assertIsNone(kwargs["after"])
            self.assertFalse(kwargs["oldest_first"])
            for item in items:
                yield item

        with patch.object(discord.Thread, "history", side_effect=history), \
                patch.object(discord.TextChannel, "fetch_message", new_callable=AsyncMock, return_value=starter) as fetch:
            text = await self.client.thread_context(current, 0)
            self.assertLess(text.index("remember violet"), text.index('"text": "violet"'))
            self.assertIn('"bot": true', text)
            self.assertNotIn("Finished.", text)
            self.assertIn("not instructions", text)
            fetch.assert_awaited_once_with(100)
            items[:] = [self.server_message(user=88, message_id=i, channel=thread, content="x" * 20000)
                        for i in range(149, 119, -1)]
            text = await self.client.thread_context(current, 0)
            self.assertNotIn("x" * 4001, text)
            self.assertLess(len(text), 12500)

    async def test_history_reads_only_between_previous_and_current_request(self):
        thread = self.existing_thread()
        current = self.server_message(message_id=150, channel=thread)
        data = [{"id": str(i), "type": 0, "content": text, "mentions": [],
                 "author": {"id": "88", "username": "other-bot", "discriminator": "0", "avatar": None, "bot": True}}
                for i, text in [(140, "new context"), (110, "old context")]]
        with patch.object(thread._state.http, "logs_from", new_callable=AsyncMock, return_value=data) as logs, \
                patch.object(discord.TextChannel, "fetch_message", new_callable=AsyncMock) as starter:
            text = await self.client.thread_context(current, 120)
            self.assertIn("new context", text)
            self.assertNotIn("old context", text)
            starter.assert_not_called()
            self.assertEqual(logs.call_args.args[0], thread.id)
            self.assertEqual(logs.call_args.kwargs["before"], current.id)

    async def test_thread_without_starter_still_has_history(self):
        thread = self.existing_thread()
        async def history(**kwargs):
            yield self.server_message(message_id=120, channel=thread, content="thread text")
        error = discord.NotFound(types.SimpleNamespace(status=404, reason="Not Found"), "missing starter")
        with patch.object(discord.Thread, "history", side_effect=history), \
                patch.object(discord.TextChannel, "fetch_message", side_effect=error):
            text = await self.client.thread_context(self.server_message(message_id=150, channel=thread), 0)
        self.assertIn("thread text", text)

    async def test_history_cursor_persists_and_reset_excludes_previous_context(self):
        thread = self.existing_thread()
        self.client._connection._intents.message_content = True
        with patch.object(discord.Thread, "send", new_callable=AsyncMock, return_value=self.receipt), \
                patch.object(self.client, "progress", new_callable=AsyncMock), \
                patch.object(self.client, "thread_context", new_callable=AsyncMock, return_value="Quoted violet\n") as history, \
                patch.object(app, "workspace_access", return_value=contextlib.nullcontext()), \
                patch.object(app, "telegram_agent_reply", return_value="done") as run:
            first = self.server_message(message_id=110, channel=thread)
            await self.client.on_message(first)
            history.assert_awaited_with(first, 0)
            self.assertIn("Quoted violet\nDiscord participant", run.call_args.args[2])
            self.assertEqual(self.saved[-1]["sessions"]["guild:5:100:claude"]["history_after"], 110)
            second = self.server_message(message_id=120, channel=thread)
            await self.client.on_message(second)
            history.assert_awaited_with(second, 110)
            await self.client.on_message(self.server_message(message_id=130, channel=thread, content="<@99> /new"))
            third = self.server_message(message_id=140, channel=thread)
            await self.client.on_message(third)
            history.assert_awaited_with(third, 130)
            self.assertEqual(run.call_count, 3)

    async def test_history_permission_failure_does_not_execute_or_advance_cursor(self):
        thread = self.existing_thread()
        self.client._connection._intents.message_content = True
        error = discord.Forbidden(types.SimpleNamespace(status=403, reason="Forbidden"), "SECRET")
        with patch.object(discord.Thread, "send", new_callable=AsyncMock, return_value=self.receipt) as send, \
                patch.object(self.client, "progress", new_callable=AsyncMock), \
                patch.object(self.client, "thread_context", side_effect=error), \
                patch.object(app, "telegram_agent_reply") as run:
            await self.client.on_message(self.server_message(message_id=110, channel=thread))
            run.assert_not_called()
            self.assertIn("Read Message History", send.await_args.args[0])
            self.assertNotIn("SECRET", str(send.await_args_list))
            self.assertNotIn("history_after", self.state["sessions"]["guild:5:100:claude"])


class DiscordStartupTests(unittest.TestCase):
    def test_environment_defaults_and_explicit_overrides(self):
        env = {"DISCORD_USER_ID": "2", "DISCORD_SERVER_ID": "5", "DISCORD_SANDBOX": "dev1"}
        with patch.dict("os.environ", env):
            args = types.SimpleNamespace(user=None, allow_user=None, server=None, name=None, timeout=10)
            app.discord_options(args)
            self.assertEqual((args.allow_user, args.server, args.name), ([2], 5, "dev1"))
            args = types.SimpleNamespace(user=3, allow_user=[4], server=6, name="dev2", timeout=10)
            app.discord_options(args)
            self.assertEqual((args.allow_user, args.server, args.name), ([3, 4], 6, "dev2"))
        for value in ("not-an-id", "-1"):
            with patch.dict("os.environ", {"DISCORD_USER_ID": value}), self.assertRaises(SystemExit):
                app.discord_options(types.SimpleNamespace(user=None, allow_user=None, server=None, name=None, timeout=10))

    def test_automatic_sandbox_selection_requires_one_running_sandbox(self):
        running = types.SimpleNamespace(status=types.SimpleNamespace(value="running"))
        stopped = types.SimpleNamespace(status=types.SimpleNamespace(value="terminated"))
        sdk = Mock()
        with patch.object(app, "Sandbox", sdk), patch.object(app, "probe_session_meta", return_value=("dev1", "claude")):
            sdk.list.return_value.result.return_value = [running, stopped]
            args = types.SimpleNamespace(name=None)
            self.assertIs(app.discord_sandbox(args), running)
            sdk.list.assert_called_once_with(tags=[app.SESSION_TAG], auth=app.sandbox_auth())
            self.assertEqual(args.name, "dev1")
            for boxes in ([], [running, running]):
                sdk.list.return_value.result.return_value = boxes
                with self.assertRaisesRegex(SystemExit, "--sandbox"):
                    app.discord_sandbox(types.SimpleNamespace(name=None))

    def test_state_survives_token_rotation_and_bot_lock_prevents_duplicate_bridge(self):
        import fcntl

        client = Mock(user=types.SimpleNamespace(id=99), worker=None)
        client.login = AsyncMock()
        client.connect = AsyncMock()
        client.fetch_guild = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        args = types.SimpleNamespace(name="dev1", timeout=10, allow_user=[2], user=None, server=None)
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(Path, "home", return_value=Path(temporary)), \
                patch.object(app, "require_active", return_value=object()), \
                patch.object(app, "active_harness", return_value=app.HARNESSES["claude"]), \
                patch.object(app, "discord_client", return_value=client), \
                patch.object(app, "telegram_ssl_context"), patch("aiohttp.TCPConnector"), \
                patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "test-token"}):
            self.assertEqual(app.cmd_discord(args), 0)
            path = Path(temporary) / ".local/state/cws-agent/discord/99/state.json"
            state = json.loads(path.read_text())
            state["sessions"]["1:2:claude"] = {"agent": {"id": "saved-session"}}
            app.telegram_save_json(path, state)
            with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "rotated-token"}):
                self.assertEqual(app.cmd_discord(args), 0)
            self.assertEqual(json.loads(path.read_text()), state)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("token", path.read_text())
            with (path.parent / "lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(SystemExit, "already running"):
                    app.cmd_discord(args)
            self.assertEqual(client.connect.await_count, 2)
            args.server = 5
            for error_type, status in ((discord.NotFound, 404), (discord.Forbidden, 403)):
                client.fetch_guild.side_effect = error_type(types.SimpleNamespace(status=status, reason="unavailable"), "SECRET")
                with self.assertRaisesRegex(SystemExit, "Guild Install") as caught:
                    app.cmd_discord(args)
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertEqual(json.loads(path.read_text()), state)
            self.assertEqual(client.connect.await_count, 2)
            args.server = None
            args.name = "another-sandbox"
            with self.assertRaisesRegex(SystemExit, "another sandbox"):
                app.cmd_discord(args)


if __name__ == "__main__":
    unittest.main()
