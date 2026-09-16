# Chat with your agent in Discord

[Back to README](../README.md).

Connect a Discord bot to a running Claude Code, OpenCode, or Cursor CLI sandbox.
Chat from your phone or desktop; each thread or DM keeps its agent conversation.

## 1. Prepare your sandbox

Complete [installation](install.md) and [agent sign-in](../README.md#get-started)
first. For a new Claude sandbox:

```sh
cws-agent launch my-claude
```

Type `/login` in Claude, finish signing in, then exit to your local shell.
For another agent, follow the [OpenCode](opencode.md) or [Cursor](cursor.md) guide.
If you already have an authenticated sandbox, choose it from:

```sh
cws-agent list
```

Keep your [sandbox credentials](usage.md#authentication) available in the terminal
that will run the bot: `WANDB_API_KEY` or a saved W&B login, or `CWSANDBOX_API_KEY`
for CoreWeave accounts. The Discord command connects to this sandbox; it does not
create one when someone sends a message.

## 2. Create and install your bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications),
   select **New Application**, give it a name, and select **Create**.
2. Open **Bot**. Leave **Requires OAuth2 Code Grant** off. Use **Reset Token**
   to get the bot token; keep it private for the next step.
3. Under **Privileged Gateway Intents**, leave **Presence Intent** and **Server
   Members Intent** off. Basic mentions and DMs need no privileged intents.
   Enable **Message Content Intent** if you want [shared thread history](#share-thread-history).
4. Open **Installation** and enable **Guild Install**. Under **Install Link**,
   select **Discord Provided Link**. In **Default Install Settings → Guild Install**,
   select the `bot` and `applications.commands` scopes.
5. In that Guild Install **Permissions** menu, select **View Channels**, **Send
   Messages**, **Read Message History**, **Create Public Threads**, **Send Messages
   in Threads**, **Add Reactions**, and **Attach Files**. Administrator is not needed.
   Save your changes.
6. Open the install link, choose **Add to server**, select your server, and authorize.
   You need permission to manage that server. Confirm the bot appears in its member
   list; installing it only to your account is insufficient.
7. Copy your server ID. In a browser, it is the first number after `/channels/`:
   `https://discord.com/channels/SERVER_ID/CHANNEL_ID`. On desktop, you can also
   enable **User Settings → Advanced → Developer Mode**, then right-click the
   server icon and select **Copy Server ID**.

On mobile, tap your profile avatar → settings gear → **Advanced** and enable
**Developer Mode**. Open the server, tap its name, scroll down, and tap **Copy Server ID**.
See [Discord's ID instructions](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID).

These steps use Discord's [server installation flow](https://docs.discord.com/developers/quick-start/getting-started#installing-your-app).

## 3. Connect Discord to your sandbox

In your local terminal, enter the bot token without echoing it or saving it in
shell history:

```sh
export DISCORD_BOT_TOKEN="$(uv run --no-project python -c 'import getpass; print(getpass.getpass("Discord bot token: "))')"
cws-agent discord --server 234567890123456789 --sandbox my-claude
```

Replace the example server ID with yours and `my-claude` with your sandbox name.
Wait for `Discord ready`. Anyone in that server can invoke the agent by mentioning
it in a public text channel visible to `@everyone`, or a public thread there.
Participants share access to the sandbox's files, tools, and configured credentials.
Agents use [YOLO mode](permissions.md) by default. Use `--permission-mode native`
to keep the agent's own policy; Discord cannot answer tool approval prompts.

Keep the command running and its host awake and online. Ctrl-C stops the bot
connection after the active request finishes; the sandbox stays running.

## 4. Try a conversation

In a public channel such as **#general**, type `@`, select your bot from the mention
picker, and send **Remember the word violet**. Expect a 👀 reaction, a new thread,
a receipt, and an agent reply.

In that thread, mention the same bot and ask **What word did I ask you to remember?**
It should answer **violet**. Select the mention each time; typing the bot's name
as ordinary text does not invoke it.

You can also mention the bot in an existing public thread. Everyone invoking that
bot in the thread shares its agent context. Separate threads have separate
conversations; each bot keeps its own session, even when several share a thread.

## Share thread history

By default, the agent sees only messages that invoke it. To include the surrounding
conversation, enable **Message Content Intent** on the bot's Developer Portal page,
save, then restart with:

```sh
cws-agent discord --server 234567890123456789 --sandbox my-claude --thread-history
```

Mention the bot in a thread and ask about an earlier message, including a reply
from another bot. Each request includes up to 30 preceding messages since this
bot's last request, plus the thread's starter on first use. History is capped at
12,000 characters, with at most 4,000 per message; older text may be omitted.
Attachments are not read. Other bots' messages provide context but never trigger
requests. Bots share visible text, not each other's private agent state.

## Use private messages

With Developer Mode enabled, right-click your profile and select **Copy User ID**.
Supply that numeric ID to allow DMs; usernames do not work:

```sh
cws-agent discord 123456789012345678 --sandbox my-claude
```

To enable DMs and server mentions together:

```sh
cws-agent discord --user 123456789012345678 --server 234567890123456789 --sandbox my-claude
```

Repeat `--user` for additional DM users (`--allow-user` is an alias). Each DM has
its own context. The DM allowlist does not restrict server mentions.

## Commands and replies

Send these as ordinary text messages, with a bot mention first in server threads.
They are not registered Discord slash commands.

| Command | Action |
| --- | --- |
| `/help` | Show available commands |
| `/new` | Start fresh context; exclude earlier thread history |
| `/session` | Show the command to continue this conversation in your terminal |

Only the person who first invoked that bot in the thread can use `/new` there.
For `/session`, stop the bot connection before running the printed resume command.

Requests run sequentially. Receipts show queued or working status, with typing and
elapsed-time updates. Replies use Markdown; long replies arrive as `response.md`.
Use `--timeout SECONDS` to change the default 300-second execution timeout.

## Reconnect and defaults

Run the same command with the same token environment variable to reconnect.
Conversation mappings survive restarts in owner-only files under
`~/.local/state/cws-agent/discord/`; the bot token stays in the environment.
Use one host per bot. A bot's saved binding cannot silently switch to another sandbox.

| Setting | Argument | Environment variable |
| --- | --- | --- |
| Bot token | Environment only | `DISCORD_BOT_TOKEN` |
| Allowed DM user | `USER_ID` or `--user ID` | `DISCORD_USER_ID` |
| Server | `--server ID` | `DISCORD_SERVER_ID` |
| Sandbox | `--sandbox NAME` | `DISCORD_SANDBOX` |

Explicit arguments override environment defaults. With these set, run
`cws-agent discord`. If the sandbox is omitted, the only running sandbox is
selected; if there are none or several, use `--sandbox`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `discord` is an unknown command | Check `command -v cws-agent` and [update the installation](install.md). |
| Bot cannot access the server | Use Guild Install, confirm server membership, and copy the server ID rather than the channel ID. |
| No receipt | Select the correct bot from the mention picker; use a public text channel/thread, or allow your numeric ID for DMs. |
| Cannot create a thread or send a reply | Check the bot's permissions in that channel, including thread permissions. |
| `--thread-history` fails at startup | Enable Message Content Intent for that bot and save. |
| Thread history cannot be read | Grant View Channels and Read Message History, then send a new mention. |
| Agent request fails or times out | Inspect the sandbox and agent sign-in before retrying; use `/new` after resolving an interrupted request. |

Offline messages, queued requests lost at shutdown, and failed replies are not
replayed. After restoring an older snapshot, use `/new` if its agent conversation
is missing. Private server channels, forum posts, attachments as input, and native
slash commands are not supported.
