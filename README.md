# cws-agent

Run coding agents in persistent cloud sandboxes. Bring your code, skills, and tools;
run agents in parallel, save your workspace, and pick up where you left off.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/coreweave/cws-agent/main/install.sh | sh
```

Works on macOS, Linux, and WSL. Installs `uv` and configures zsh and bash
for future sessions. Open a new terminal afterward. [Install options](docs/install.md).

## Get started

For sandbox access and setup, see [Get started with Serverless Sandboxes](https://docs.wandb.ai/sandboxes#basic-usage).

Set your [W&B API key](https://wandb.ai/authorize):

```bash
export WANDB_API_KEY='YOUR_WANDB_KEY'
cws-agent launch my-claude
```

`my-claude` names your sandbox. Claude Code opens; type `/login` to sign in.
CLI choices for `--agent`: `claude` (default), `codex`, `devin`, `opencode`, `cursor`.
To use an open-source model hosted by W&B instead of the default proprietary models,
choose OpenCode with `--agent opencode --wandb` ([setup](docs/opencode.md)).
Agents use [YOLO mode](docs/permissions.md) by default; `--permission-mode native`
uses the agent's own approval settings. [Other credentials](docs/usage.md#authentication).

## Bring your skills and tools

Launch offers to import local skills and MCP tools. Review or update them later:

```bash
cws-agent config preview my-claude
cws-agent config sync my-claude
```

You choose what gets copied. [Supported configuration](docs/config-import.md).

## Move your workspace to the cloud

```bash
cws-agent launch project1 --local-dir .
```

Uploads the current directory to `/workspace/project` and saves a snapshot when
complete. Keep this terminal open until the upload finishes; use `cws-agent sync
project1 .` for later changes. [Upload options and recovery](docs/usage.md#upload-your-project).

## Continue a session

Find a saved conversation inside the `my-claude` sandbox, then resume it by ID:

```bash
cws-agent session history my-claude
cws-agent session resume my-claude SESSION_ID
```

To open a fresh agent terminal in that sandbox, use `cws-agent connect my-claude`.

## Save your work and stop compute

```bash
cws-agent down my-claude
cws-agent restore my-claude --connect
```

`down` snapshots your workspace and stops the sandbox; `restore` brings the files
back. Exiting the agent alone leaves compute running. [Snapshot details](docs/usage.md#snapshots).

## Self-hosted sandboxes

Keep the agent and conversation in Devin Cloud, Claude Managed Agents, or the
OpenAI Agents API while tools execute in your sandbox. After setting up the
[provider credentials](docs/self-hosted.md), choose one:

```bash
cws-agent launch devinbox --outpost my-outpost
cws-agent launch claudebox --claude-env env_REPLACE_ME
cws-agent launch api1 --agent openai
```

Send work through Devin Cloud or the Claude Managed Agents API; for OpenAI, use
`cws-agent run api1 "your task"` ([API setup](docs/openai-agents.md)).

## Run agents in parallel

Sign in to `project1` with `/login`, then exit Claude. Give each task its own
Git worktree and branch inside that sandbox:

```bash
cws-agent session start project1 fix-auth --prompt "Fix the login bug"
cws-agent session start project1 add-tests --prompt "Add parser tests"
cws-agent session attach project1 fix-auth
cws-agent session diff project1 add-tests
```

Branches are named `agent/fix-auth` and `agent/add-tests`. Detach with **Ctrl-b, d**;
the agent keeps working. [Sessions guide](docs/sessions.md).

## Chat with your agent on Telegram

Use [Telegram](docs/messaging.md) to chat with your agent while your project uploads
in the background:

```bash
cws-agent launch telegram1 --local-dir . --telegram
```

The upload survives closing its terminal.
Keep your laptop awake and online; the Telegram bridge needs a running terminal.

## Chat with your agent in Discord

Chat with Claude Code, OpenCode, or Cursor CLI from Discord. Start with a running,
signed-in sandbox, such as `my-claude` above, and exit the agent to your local shell.

[Create a Discord bot and install it in your server](docs/discord.md#2-create-and-install-your-bot).
The guide walks through the Developer Portal, bot permissions, token, and server ID.
Then enter the bot token privately and start listening:

```bash
export DISCORD_BOT_TOKEN="$(uv run --no-project python -c 'import getpass; print(getpass.getpass("Discord bot token: "))')"
cws-agent discord --server 234567890123456789 --sandbox my-claude
```

Replace the example server ID with the first number after `/channels/` in a Discord
channel URL: `https://discord.com/channels/SERVER_ID/CHANNEL_ID`.
On mobile, tap your profile avatar → settings gear → **Advanced** and enable
**Developer Mode**. Open the server, tap its name, scroll down, and tap **Copy Server ID**.

In a public channel, select your bot
from the `@` mention picker and send a message. It replies in a thread; mention it
there for follow-ups. You get a receipt, typing indicator, and formatted replies.
The first message starts an agent conversation in the selected sandbox.

Keep this terminal running and its host awake and online.
See [Discord setup](docs/discord.md) for DMs, shared thread history, and continuing
the conversation in your terminal.

## More

[CLI guide](docs/usage.md) · [Terminal and clipboard](docs/terminal.md) ·
[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Copyright 2026 CoreWeave, Inc. [Apache 2.0](LICENSE) · [Third-party notices](docs/third-party.md).
