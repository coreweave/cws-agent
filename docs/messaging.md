# Messaging an agent

[Back to README](../README.md).

For Discord DMs and server threads, see [Discord setup](discord.md).

Chat with Claude Code, Codex, Devin CLI, OpenCode, or Cursor CLI through Telegram.
The bridge runs on your laptop or an always-on host; no inbound port is needed.
Managed Agents and Outposts are not supported.

## Telegram setup

```sh
cws-agent launch telegram2 --local-dir . --telegram
```

If prompted, sign into Claude with `/login`, then press Ctrl-D. The command
continues into Telegram setup:

1. Create a bot with [BotFather](https://t.me/BotFather) using `/newbot`.
2. Enter its token privately in the terminal.
3. Scan the pairing QR and tap **Start** to authorize your account.

With a [management bot](#create-bots-without-copying-their-tokens), scan the
creation QR and confirm in Telegram instead; no token copying or second scan.

Pairing links grant access and expire after five minutes; keep them private.
On `bridge telegram`, use `--confirm-pairing` for terminal approval or
`--no-save-token` to avoid saving a manually entered token.

Skills/MCP review and sign-in happen before the workspace upload. You can chat
while it uploads in a separate process; files become available after extraction,
and agent-created files win on collisions. The bot reports progress and a snapshot
is saved on completion. The CLI prints the upload log path.

The upload survives closing its terminal. **Keep the host awake and online, and
keep the bridge running to receive messages.** Requests may briefly wait during
extraction or snapshot capture. After the sandbox stops or expires:

```sh
cws-agent restore telegram2 --telegram
```

This restores the latest ready snapshot without another upload. Re-export
credentials previously supplied only through the environment; renew expired
agent logins with `cws-agent login telegram2`.

For an existing authenticated sandbox:

```sh
cws-agent bridge telegram dev1
```

### Create bots without copying their tokens

1. Create a dedicated bot in BotFather's Mini App and enable **Bot Management Mode**.
2. Export its token as `TELEGRAM_MANAGER_BOT_TOKEN` on the bridge host.
3. Launch with `--telegram`; scan the creation QR and confirm in Telegram.

No manager is bundled. Keep its credential private and available for reconnects;
child tokens are not saved locally. Use one host and a dedicated manager without
a webhook: setup consumes its updates. Existing saved tokens or
`TELEGRAM_BOT_TOKEN` take precedence over new creation.
[Telegram managed bots](https://core.telegram.org/bots/features#managed-bots).

## Reconnect or pair again

Run the bridge command again to reuse a saved account. Unsaved tokens must be
re-entered or supplied through `TELEGRAM_BOT_TOKEN`; managed bots use the same
`TELEGRAM_MANAGER_BOT_TOKEN`.

```sh
cws-agent bridge telegram dev1 --setup
```

`--setup` replaces the pairing; canceling preserves the old one. Interactive setup
is required unless a pairing is saved or a token and both allowlists are supplied:

```sh
cws-agent bridge telegram dev1 --allow-chat 123456789 --allow-user 123456789
```

Repeat both flags for trusted users. Manual flags override the saved account for
that run. Use one bot per sandbox and one polling host per bot. Ctrl-C stops the
bridge, not the sandbox.

## Behavior and limits

- Only allowlisted users' private text messages are accepted. Everyone shares
  the sandbox and its credentials; groups and attachments are ignored.
- Claude, OpenCode, and Cursor keep a conversation per chat/user pair. `/new`
  starts another; `/help` shows help. Codex and Devin use one-shot prompts.
- Requests run sequentially with a 300-second default timeout (`--timeout`).
  Status messages show progress; tool output is not streamed. There is no cancellation.
- Replies are capped at 12,000 source characters. Delivery is not durable:
  interrupted requests and failed replies are not replayed.
- After an interrupted run, inspect the sandbox before using `/new`.
  Also use `/new` if an older restored snapshot lacks the saved conversation.

## Run without permission prompts

[YOLO mode](permissions.md) is the default. To use the agent's own policy:

```sh
cws-agent bridge telegram dev1 --permission-mode native
```

`--permission-mode accept-edits` is also available. Headless runs cannot answer
approval prompts; pass overrides each time you start the bridge.

## Privacy and operation

Prompts and replies pass through Telegram and the model provider. Manually entered
tokens are saved by default, unencrypted but owner-only, under
`~/.local/state/cws-agent/telegram/connections/`. Use `--no-save-token` to opt out.
The management token is never saved or sent into the sandbox.

Setup prints the pairing file; delete it to forget the account and saved token.
Revoke compromised tokens through BotFather. Conversation history stays in the
sandbox; the host stores pairing and polling state.

## Slack and WhatsApp

Devin Cloud offers a [Slack integration](https://docs.devin.ai/integrations/slack)
for your outpost. There is no general Slack or WhatsApp adapter here.

## Connection errors

| Error | Action |
| --- | --- |
| HTTP 401 / 404 | Check or regenerate the BotFather token. |
| HTTP 409 | Stop other polling clients or remove the bot's webhook. |
| TLS certificate | Configure your proxy's approved CA in OS trust or `SSL_CERT_FILE`; keep verification enabled. |
| DNS / timeout | Check the connection, VPN, and proxy. |

After token rotation, run `unset TELEGRAM_BOT_TOKEN`, then
`cws-agent bridge telegram dev1 --setup` to enter the replacement privately.
