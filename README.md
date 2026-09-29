# cws-agent

Run coding agents in persistent cloud sandboxes. Bring your code, skills, and tools.
Run agents in parallel, save your workspace, and resume your work.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/coreweave/cws-agent/main/install.sh | sh
```

The installer works on macOS, Linux, and WSL. It installs `uv` and configures `zsh` and `bash`
for future sessions. Open a new terminal afterward. [Install options](docs/install.md).

## Get started

For sandbox access and setup, see [Get started with Serverless Sandboxes](https://docs.wandb.ai/sandboxes#basic-usage).

Set your [W&B API key](https://wandb.ai/authorize):

```bash
export WANDB_API_KEY='YOUR_WANDB_KEY'
cws-agent claude
```

The command creates a sandbox, prints its generated name (for example,
`claude-fa97da5d`), and opens Claude Code. Type `/login` to sign in.
In the following commands, replace `SANDBOX` with that full name.

Use `cws-agent AGENT [NAME]` to choose `claude`, `codex`, `devin`, `opencode`, or
`cursor`. These are shortcuts for `cws-agent launch [NAME] --agent AGENT`.
Existing `launch` commands remain supported, with Claude Code as the default.
See [all agents and worker backends](docs/usage.md#launch-an-agent).

To use an open source model hosted by W&B instead of the default proprietary models,
choose OpenCode with `cws-agent opencode --wandb` ([OpenCode setup](docs/opencode.md)).

Agents use [YOLO mode](docs/permissions.md) by default. `--permission-mode native`
uses the agent's own approval settings. [Other credentials](docs/usage.md#authentication).

To start Codex using your existing local ChatGPT login, without remote device codes:

```bash
cws-agent codex --import-codex-auth
```

This copies your local login into the sandbox before Codex opens.
See [Codex login import](docs/usage.md#reuse-a-local-codex-login) for existing sessions
and credential storage details.

## Open a shell

Create or reconnect to a sandbox terminal without starting a coding agent:

```bash
cws-agent shell dev1
cws-agent shell gpu1 --gpu any:1
```

Exiting leaves the sandbox running. Stop it with `cws-agent stop dev1 --no-snapshot`.
See the [shell guide](docs/shell.md) for images, files, secrets, snapshots, and CoreWeave Kubernetes Service (CKS) volumes.

## Bring your skills and tools

Launch offers to import local skills and MCP tools. Review or update them later:

```bash
cws-agent config preview SANDBOX
cws-agent config sync SANDBOX
```

You choose what gets copied. [Supported configuration](docs/config-import.md).

## Move your workspace to the cloud

Upload your local project when creating a sandbox:

```bash
cws-agent claude project1 --add-dir .
```

The command uploads the current directory to `/workspace/project` and saves a snapshot when
the upload completes. Keep this terminal open until the upload finishes. Use `cws-agent sync
project1 .` for later changes. [Upload options and recovery](docs/usage.md#upload-your-project).

## Continue a session

Choose a conversation across running and saved workspaces:

```bash
cws-agent resume
cws-agent resume project1
cws-agent claude --resume SESSION_ID
```

Use arrows and **Enter** to choose. Resume uses live compute when available, or
restores the latest saved workspace before continuing the conversation.
`SESSION_ID` is the agent's own chat ID. Workspace names and sandbox IDs also
work with top-level `resume`. [Workspaces and conversations](docs/sessions.md).

## Save your work and stop compute

Save the workspace so you can restore its files later:

```bash
cws-agent stop SANDBOX
cws-agent restore SANDBOX --connect
```

`stop` snapshots your workspace and stops the sandbox. `restore` brings the files
back. Exiting the agent alone leaves compute running. [Snapshot details](docs/usage.md#snapshots).

## Self-hosted sandboxes

Keep the agent and conversation in Devin Cloud, Claude Managed Agents, or the
OpenAI Agents API while tools execute in your sandbox. After setting up the
[provider credentials](docs/self-hosted.md), choose one:

```bash
cws-agent devin devinbox --outpost my-outpost
cws-agent anthropic claudebox --claude-env env_REPLACE_ME
cws-agent openai api1
```

Send work through Devin Cloud or the Claude Managed Agents API. For OpenAI, use
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

Branches are named `agent/fix-auth` and `agent/add-tests`. Detach with **Ctrl-b, d**.
The agent keeps working.

## Chat with your agent on Telegram

Use [Telegram](docs/messaging.md) to chat with your agent while your project uploads
in the background:

```bash
cws-agent claude telegram1 --add-dir . --telegram
```

The upload survives closing its terminal.
Keep your laptop awake and online. The Telegram bridge requires a running terminal.

## Chat with your agent in Discord

Chat with Claude Code, OpenCode, or Cursor CLI from Discord. Start with a running,
signed-in sandbox, such as `SANDBOX` from [Get started](#get-started), and exit the agent to your local shell.

[Create a Discord bot and install it in your server](docs/discord.md#2-create-and-install-your-bot).
The guide walks through the Developer Portal, bot permissions, token, and server ID.
Then enter the bot token privately and start listening:

```bash
export DISCORD_BOT_TOKEN="$(uv run --no-project python -c 'import getpass; print(getpass.getpass("Discord bot token: "))')"
cws-agent discord --server 234567890123456789 --sandbox SANDBOX
```

Replace the example server ID with the first number after `/channels/` in a Discord
channel URL: `https://discord.com/channels/SERVER_ID/CHANNEL_ID`.
On mobile, tap your profile avatar → settings gear → **Advanced** and enable
**Developer Mode**. Open the server, tap its name, scroll down, and tap **Copy Server ID**.

In a public channel, select your bot
from the `@` mention picker and send a message. It replies in a thread. Mention it
there for follow-ups. You get a receipt, typing indicator, and formatted replies.
The first message starts an agent conversation in the selected sandbox.

Keep this terminal running and its host awake and online.
See [Discord setup](docs/discord.md) for DMs, shared thread history, and continuing
the conversation in your terminal.

## More

[CLI guide](docs/usage.md) · [Terminal and clipboard](docs/terminal.md) ·
[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Copyright 2026 CoreWeave, Inc. [Apache 2.0](LICENSE) · [Third-party notices](docs/third-party.md).
