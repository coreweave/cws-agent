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
cws-agent claude
```

The command creates a sandbox, prints its generated name (for example,
`claude-fa97da5d`), and opens Claude Code. Type `/login` to sign in.
In the commands below, replace `SANDBOX` with that full name.
Use `cws-agent AGENT [NAME]` to choose `claude`, `codex`, `devin`, `opencode`, or
`cursor`. These are shortcuts for `cws-agent launch [NAME] --agent AGENT`;
existing `launch` commands remain supported, with Claude Code as the default.
See [all agents and worker backends](docs/usage.md#launch-an-agent).
To use an open-source model hosted by W&B instead of the default proprietary models,
choose OpenCode with `cws-agent opencode --wandb` ([setup](docs/opencode.md)).
Agents use [YOLO mode](docs/permissions.md) by default; `--permission-mode native`
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

Exiting leaves the sandbox running. Stop it with `cws-agent down dev1 --no-snapshot`.
See the [shell guide](docs/shell.md) for images, files, secrets, snapshots, and CKS volumes.

## Bring your skills and tools

Launch offers to import local skills and MCP tools. Review or update them later:

```bash
cws-agent config preview SANDBOX
cws-agent config sync SANDBOX
```

You choose what gets copied. [Supported configuration](docs/config-import.md).

## Move your workspace to the cloud

```bash
cws-agent claude project1 --local-dir .
```

Uploads the current directory to `/workspace/project` and saves a snapshot when
complete. Keep this terminal open until the upload finishes; use `cws-agent sync
project1 .` for later changes. [Upload options and recovery](docs/usage.md#upload-your-project).

## Continue a session

A sandbox holds your workspace and agent files. An agent session is a saved chat
inside that sandbox. Its ID comes from the harness (for example, Claude Code or
Codex). Find a running sandbox by its full `NAME`, then list its agent sessions:

```bash
cws-agent list
cws-agent session history SANDBOX
cws-agent claude --resume SESSION_ID
```

`--resume` takes the `SESSION_ID` shown by `session history`, finds that session
in running agent sandboxes, and continues it.
Use the matching agent command (`claude`, `codex`, or `opencode`); add `SANDBOX`
before `--resume` if a copied session exists in multiple sandboxes.
`connect SANDBOX` opens a fresh agent terminal. If the sandbox was stopped, run
`cws-agent restore SANDBOX` first to recreate it from its snapshot.
Top-level `cws-agent resume SANDBOX` is an alias for `restore`; it does not
select an agent session. [Sessions guide](docs/sessions.md).

## Save your work and stop compute

```bash
cws-agent down SANDBOX
cws-agent restore SANDBOX --connect
```

`down` snapshots your workspace and stops the sandbox; `restore` brings the files
back. Exiting the agent alone leaves compute running. [Snapshot details](docs/usage.md#snapshots).
For [Claude Code cloud runners](docs/claude-cloud.md#monitor-and-stop), stop with
`down --no-snapshot` and create a fresh runner when needed.

## Self-hosted sandboxes

Keep the agent and conversation in Devin Cloud, Claude Managed Agents, or the
OpenAI Agents API while tools execute in your sandbox. After setting up the
[provider credentials](docs/self-hosted.md), choose one:

```bash
cws-agent devin devinbox --outpost my-outpost
cws-agent anthropic claudebox --claude-env env_REPLACE_ME
cws-agent openai api1
```

Send work through Devin Cloud or the Claude Managed Agents API; for OpenAI, use
`cws-agent run api1 "your task"` ([API setup](docs/openai-agents.md)).

To run Claude Code cloud sessions on sandbox compute with your own image and
resources, see [Run Claude Code cloud sessions in a sandbox](docs/claude-cloud.md).

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
cws-agent claude telegram1 --local-dir . --telegram
```

The upload survives closing its terminal.
Keep your laptop awake and online; the Telegram bridge needs a running terminal.

## Chat with your agent in Discord

Chat with Claude Code, OpenCode, or Cursor CLI from Discord. Start with a running,
signed-in sandbox, such as `SANDBOX` above, and exit the agent to your local shell.

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
