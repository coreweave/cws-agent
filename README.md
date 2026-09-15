# cws-agent

Run Claude Code, Codex, Devin CLI, OpenCode, or Cursor CLI in a CoreWeave sandbox.
Also run [OpenAI Agents API tools on CoreWeave](docs/openai-agents.md), with
OpenAI managing the agent and conversation.

## Install once

Already have `cws-agent --help` working? Skip to [Launch](#launch).

Requires Git, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and macOS, Linux, or WSL.

```bash
git clone https://github.com/coreweave/cws-agent.git
cd cws-agent
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/cws-agent" "$HOME/.local/bin/cws-agent"
export PATH="$HOME/.local/bin:$PATH"
cws-agent --help
```

Add the `export PATH=...` line above to `~/.zshrc` (or `~/.bashrc`) for future
terminals. Keep the checkout: the installed command links to it.
If the link already exists, inspect `command -v cws-agent` before changing it;
these instructions do not overwrite an existing installation.

All examples use **`cws-agent`**, which works from any project directory.
A `./` prefix looks for a file in your current directory instead.

## Launch

Set your CoreWeave credential, then change to the project you want to upload:

```bash
export CWSANDBOX_API_KEY='YOUR_COREWEAVE_KEY'
```

Choose one agent:

```bash
cws-agent launch --name claude1 --local-dir .              # Claude Code (default)
cws-agent launch --name cdx1 --agent codex --local-dir .    # Codex
cws-agent launch --name dvn1 --agent devin --local-dir .    # Devin CLI
cws-agent launch --name open1 --agent opencode --local-dir . # OpenCode
cws-agent launch --name cursor1 --agent cursor --local-dir . # Cursor CLI
```

Launch opens the remote agent. Follow its sign-in flow; if needed, exit the
agent to return to your local shell and run:

```bash
cws-agent login claude1
```

For Claude, type `/login` inside the agent. For the others, use their sandbox name.
Already have an agent token or API key? See [authentication](docs/usage.md#authentication).
Setup and differences: [OpenCode](docs/opencode.md) · [Cursor CLI](docs/cursor.md).

OpenCode with an open-weight coding model on **W&B Serverless Inference**:

```bash
export WANDB_API_KEY='YOUR_WANDB_KEY'
cws-agent launch --name open-wandb --agent opencode --wandb --local-dir .
```

Keep your sandbox credential set. Get the W&B key from
[User Settings](https://wandb.ai/settings) → Create new API key; inference access
and credits are required. The preset selects DeepSeek V4 Pro 0813, with no fallback
to GPT or another provider. [Key setup, model choice, and alternatives](docs/opencode.md#wb-serverless-inference).

Your project lands at `/workspace/project`. Local sync shows packaging/upload
sizes and progress (`.` means the entire current directory).
Launch automatically sizes the disk to fit local files with headroom; `--disk` overrides it.
Project uploads are resumable: on failure, run the printed `cws-agent sync NAME
--resume-upload ID` command. Completed chunks are reused without repackaging.
The sandbox stays billable until stopped or expired. See [upload recovery](docs/usage.md#resume-an-upload).
With `--telegram`, skills/MCP setup and sign-in come first; workspace upload runs
in the background while you chat. Completion automatically saves a snapshot.
Later: `cws-agent restore telegram2 --telegram --dangerously-skip-permissions` restores
the saved workspace and reconnects the bot without another upload.
Edits are accepted by default where supported; Cursor retains its native permissions.
Other actions can still need approval. Review the skills/MCP import prompt,
or press Enter to skip. Exiting the agent **does not stop the sandbox**.

## Daily commands

```bash
cws-agent list
cws-agent connect claude1                   # open the agent in a running sandbox
cws-agent sync claude1 .                   # upload local changes
cws-agent run claude1 "summarize this repo" # one-shot prompt
cws-agent snapshot claude1                 # save workspace without stopping
cws-agent down claude1                     # snapshot and stop compute
cws-agent restore claude1 --connect          # restore and open the agent
```

Re-export environment-only credentials before `restore`. Snapshots retain
files and saved logins, not running processes. To continue a specific
conversation, use `session resume` below.

Snapshot preparation temporarily changes live permissions and link representations;
read the [snapshot caveat](docs/usage.md#snapshots) before storing sensitive data.

## Continue a conversation

```bash
cws-agent session history claude1
cws-agent session resume claude1 SESSION_ID
```

History listing supports Claude/Codex and OpenCode workspace projects; Cursor
uses its native picker (`session history NAME --agent cursor`). For upload/download and multiple
agents in one sandbox, see [sessions](docs/sessions.md).

## Claude Managed Agents: a separate mode

Use `--agent claude` for the Claude Code terminal.
Use `--claude-env` to run **Managed Agents tool workers**:

```bash
export ANTHROPIC_ENVIRONMENT_KEY='YOUR_ENVIRONMENT_KEY'
cws-agent launch --name claudebox --claude-env env_REPLACE_ME
```

This starts workers and returns to your shell. It does not create a conversation.
Managed Agents supports interactive conversations through its API, but
**cws-agent has no Managed Agents chat command**. Neither `login` nor
attaching to worker logs opens a chat.

See [self-hosted setup](docs/self-hosted.md) for connecting an agent/session,
checking worker connectivity, and Devin Outposts. Environment keys are not
Claude Code login tokens.

## More features

Create a Claude sandbox and connect Telegram in one command:

```bash
cws-agent launch --name telegram2 --local-dir . --telegram --dangerously-skip-permissions
```

Follow sign-in and QR pairing; leave the command running. The permission flag
bypasses tool approvals. [Configure a management bot once](docs/messaging.md#create-bots-without-copying-their-tokens)
to create bots without copying each token; otherwise setup asks for a BotFather token.

| Task | How |
| --- | --- |
| Parallel agents, restart, history transfer | [Sessions guide](docs/sessions.md) |
| Bypass permissions explicitly | `cws-agent connect claude1 --yolo` — [permissions](docs/permissions.md) |
| Review skills and MCP imports | `cws-agent config sync claude1` — [configuration](docs/config-import.md) |
| Paste a local image into remote Claude | **Ctrl+V** — [terminal guide](docs/terminal.md) |
| Copy remote text to your clipboard | Ask the agent to run `cws-copy` — [terminal guide](docs/terminal.md) |
| Telegram chat with QR pairing and progress updates | `cws-agent bridge telegram claude1` — [messaging guide](docs/messaging.md) |
| Get changes back, snapshots, remote control | [Usage guide](docs/usage.md) |

## Troubleshooting

- **Command not found:** complete [Install once](#install-once), then check `command -v cws-agent`.
- **Unknown option:** check which checkout `command -v cws-agent` links to, then `git pull` in it. This guide targets `main`.
- **Managed Agents starts but shows no prompt:** follow [self-hosted setup](docs/self-hosted.md); worker logs are not chat.
- **Ghostty redraw or clipboard issues:** see [terminal troubleshooting](docs/terminal.md#troubleshooting).
- **Need an option?** Run `cws-agent --help` or `cws-agent launch --help`.

## Development

From the repository checkout, run offline checks:

```bash
uv run --no-project --with 'cwsandbox>=1.1' --with 'segno>=1.6,<2' --with 'truststore>=0.10,<1' --with 'markdown-it-py>=3,<5' --with 'python-dotenv>=1,<2' --with 'openai>=3.14,<4' python -m unittest discover -s tests
```

These do not prove live provider authentication or terminal rendering.
The optional `./smoke.sh` creates billable sandboxes and deletes its test snapshots.

Probe an existing OpenCode/Cursor sandbox: `uv run smoke_harnesses.py NAME`.
Add `--model-turns` for two potentially billable prompts that verify conversation
resume. Authentication is required; the sandbox is not stopped.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Commits need a `Signed-off-by` trailer
to accept the [CoreWeave CLA](CLA.md). Report vulnerabilities per [SECURITY.md](SECURITY.md).

## License

Copyright 2026 CoreWeave, Inc. Licensed under Apache 2.0. See [LICENSE](LICENSE).

## Third-party software, services, and accounts

cws-agent is an open-source CoreWeave tool that runs compatible agent software
and services in CoreWeave Sandboxes. Third-party offerings are governed by their
providers' licenses, terms, privacy notices, pricing, usage limits, and support
policies. You are responsible for any required accounts, credentials,
permissions, and compliance with the terms applicable to the third-party
offerings you use. The Apache License 2.0 governs cws-agent; applicable
CoreWeave terms govern your use of CoreWeave Sandboxes and related CoreWeave
services.

Except for offerings from CoreWeave or its affiliates, third-party offerings are
independently provided and are not controlled by CoreWeave. CoreWeave makes no
warranties regarding third-party offerings or their availability, security,
accuracy, outputs, or conduct. Listing or supporting a compatible product does
not, by itself, constitute sponsorship or endorsement of that product by
CoreWeave. Product names and trademarks belong to their respective owners.

Agents can generate inaccurate, insecure, harmful, or potentially infringing
material and may execute commands, modify or delete files, or invoke external
services. Review agent actions and outputs, maintain appropriate backups and
access controls, and test thoroughly before production or other consequential
use.

This notice supplements and does not modify the Apache License 2.0 or any
applicable agreement with CoreWeave.
