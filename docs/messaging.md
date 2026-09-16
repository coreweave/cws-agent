# Messaging an agent

[Back to README](../README.md).

The Telegram bridge forwards approved users' private text messages to a running
Claude Code, Codex, Devin CLI, OpenCode, or Cursor CLI sandbox. It runs on your laptop or an always-on
host; no inbound port is needed.

**Managed Agents and Devin Outpost conversations are not supported by this bridge.**

## Telegram setup

From your project directory, create the sandbox and connect Telegram in one command:

```sh
cws-agent launch --name telegram2 --agent claude --local-dir . --telegram --dangerously-skip-permissions
```

This opens sign-in when needed: type `/login` in Claude, complete it, then
press Ctrl-D. The same command continues into Telegram setup and stays running.
YOLO is the default. Use `--permission-mode accept-edits` or `--permission-mode native` to override it.

Skills/MCP review and agent sign-in happen first. A separate process packages and
uploads the workspace while Telegram setup proceeds; you do not wait for the files
to chat. The CLI prints a private upload-log path, and the bot reports upload and
snapshot milestones. Closing the bridge does not stop that worker; the host must
remain awake and connected. `--local-dir .` still selects the whole current directory.

Local files become available after extraction. Files created by the agent win on
collisions. Requests through this host's bridge may briefly wait during final
extraction or snapshot capture. After completion, restore without uploading again:

```sh
cws-agent restore telegram2 --telegram --dangerously-skip-permissions
```

This restores the latest READY snapshot after the original sandbox has stopped or
expired. Stored credentials are included if present; environment-only tokens must
be re-exported, and expired agent logins need `cws-agent login telegram2`.

Already have an authenticated sandbox? Start just the bridge:

```sh
cws-agent bridge telegram dev1
```

With a configured management bot: **one QR, no extra terminal approvals**. Scan the
creation QR, keep the suggested username, and confirm creation in Telegram.
The creator's account is connected automatically. The bridge starts
listening without a second scan or token-storage prompt. It sends a readiness
message if the chat is open; otherwise open the bot and tap **Start**.

Without a manager: create a bot with [BotFather](https://t.me/BotFather), using
`/newbot`, and enter its token privately. Scan the pairing QR and tap **Start**.
That account is authorized automatically. Manual tokens are saved locally in an
owner-only (0600), unencrypted file; use `bridge telegram --no-save-token` to opt out.
Use `--confirm-pairing` to bring back explicit local approval questions.

No manual ID lookup is needed. QR codes are generated locally and never contain
bot tokens. **Keep links private:** completing creation or tapping Start with the
pairing challenge grants access to that account. Links expire after five minutes.
A narrow terminal shows the link instead.

### Create bots without copying their tokens

An operator must configure a **dedicated management bot once**:

1. Create/select a bot in BotFather's Mini App and enable **Bot Management Mode**.
2. Set `TELEGRAM_MANAGER_BOT_TOKEN` privately on the bridge host to that manager's token.
3. Run the single launch command above. It shows Telegram's create-bot link/QR,
   retrieves the new bot's token automatically, and uses Telegram's creator ID
   to authorize the owner automatically.

New managed bots do not store child tokens locally. Keep
`TELEGRAM_MANAGER_BOT_TOKEN` available on the bridge host for reconnects.

There is no bundled or hosted manager. The operator still supplies one management
credential; individual bots no longer require copying BotFather tokens.
Use a dedicated manager on one host, not a shared production polling bot:
setup consumes its updates and refuses existing webhooks. The manager can access
the bots' tokens; never distribute its credential to untrusted users.
Existing saved tokens or `TELEGRAM_BOT_TOKEN` take precedence over new creation.
See [Telegram managed bots](https://core.telegram.org/bots/features#managed-bots).

## Reconnect or pair again

Run the same command to reuse the saved account. If you chose not to save the
token, enter it again or supply `TELEGRAM_BOT_TOKEN`. Managed bots can retrieve
it again with the same `TELEGRAM_MANAGER_BOT_TOKEN`. Run setup again to replace
the saved account (the old pairing remains if you cancel):

```sh
cws-agent bridge telegram dev1 --setup
```

Pairing needs an interactive terminal. Unattended starts need a saved pairing
and token, or a token in the environment plus both manual allowlists:

```sh
cws-agent bridge telegram dev1 --allow-chat 123456789 --allow-user 123456789
```

Manual flags override the saved account for that run without changing it.
Use one bot per sandbox; `--setup` does not move a bot already bound to another sandbox.
Keep the bridge running and its host awake.
Ctrl-C stops the bridge, not the sandbox.

## Behavior and limits

- Each accepted prompt gets a receipt message and a typing indicator. While
  waiting, the receipt updates about every 30 seconds with elapsed time; the
  final answer is sent separately. These are bridge status updates, not streamed
  tool output. Status-delivery failures do not replay or cancel agent work.
- Both chat and user must be allowlisted. Repeat flags for more trusted users.
  Everyone shares the sandbox/project.
- Claude, OpenCode, and Cursor keep a conversation per chat/user pair; `/new` starts another and
  `/help` shows help. Codex and Devin use independent one-shot prompts.
- [YOLO mode](permissions.md) bypasses approval prompts by default. Headless runs
  cannot answer prompts when an explicit policy requires approval.
- Groups, bots, edits, and attachments are ignored. Existing-bot pairing discards
  messages from before the pairing challenge; automatic pairing keeps prompts
  sent after the successful Start event. Fresh managed creation preserves the owner's
  early private prompts and processes them after setup; other users remain blocked.
  There is no streaming or cancellation.
- Requests run sequentially, with a 300-second default timeout. Replies render
  Markdown headings, emphasis, lists, links, and code as Telegram-native formatting.
  Tables become readable monospace rows; raw HTML stays literal. Replies are
  limited to 12,000 source characters and split into Unicode-safe messages.
- Interrupted requests and failed replies are not replayed. A crash can lose a
  message or response; delivery is not durable.

## Run without permission prompts

For an existing authenticated CLI sandbox:

```sh
cws-agent bridge telegram dev1 --dangerously-skip-permissions
```

This is equivalent to `--yolo`. It allows agent tools to run without approval;
only allowlist people you trust with the sandbox and its credentials.
YOLO is the default. Explicit permission overrides apply only to the current
invocation; saved pairing settings do not persist them.

The one-command launch above passes the permission choice to its bridge too.
For a fresh sandbox, use a separate bot; unset `TELEGRAM_BOT_TOKEN` if it points
to a bot already bound to another sandbox.

## Privacy and operation

Prompts and replies pass through Telegram and the model provider.
The bot token stays on the bridge host. Pairing settings are stored under
`~/.local/state/cws-agent/telegram/connections/`; setup prints the exact file.
If you explicitly choose to save the token, it is **unencrypted** in that file,
readable only by your OS user (0600, directory 0700). Otherwise only the pairing
and token hash are saved (plus bot ID/manager hash for managed creation).
The management token is not saved or sent into the sandbox.
Remove the printed pairing file to forget the saved
account/token; revoke a compromised token through BotFather.

Separate polling state under `~/.local/state/cws-agent/telegram/` stores update
offsets and Claude IDs, not message text or tokens. Claude history stays in the sandbox.

Use one bot per sandbox and one polling host per bot.
A configured webhook must be removed through bot administration before pairing;
the wizard reports it without deleting it. Existing-bot pairing discards setup
messages. New managed-bot authorization explicitly includes the owner's early
prompts, so those are handled after the bridge starts listening.
After restoring an older sandbox snapshot, use `/new` if the saved conversation is missing.

## Slack and WhatsApp

For Devin Cloud, use its existing [Slack integration](https://docs.devin.ai/integrations/slack)
with your outpost selected. There is no general Slack adapter or WhatsApp
connector in this repository.

## Connection errors

- **HTTP 401 / 404:** check or regenerate the BotFather token. Claude login does not authenticate Telegram.
- **TLS certificate error:** the bridge uses OS certificate trust, including macOS Keychain.
  For a company proxy/VPN, use an IT-approved CA in the OS trust store or
  `SSL_CERT_FILE`. Do not disable certificate verification.
- **HTTP 409:** another polling client or a webhook is using this bot.
- **DNS / timeout:** check your connection, VPN, and proxy settings.

After rotating a token, run `unset TELEGRAM_BOT_TOKEN`, then
`cws-agent bridge telegram dev1 --setup` to enter its replacement privately.
Do not paste bot tokens into chat or issues.

Tests cover automatic and confirmation-based pairing, expiry, token privacy, QR rendering,
and saved reconnects. They mock remote execution and Telegram HTTP; real phone
scanning and managed bot creation still require live verification with a configured bot.
