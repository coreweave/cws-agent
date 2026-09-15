# Self-hosted workers

[Back to README](../README.md).

| You want | Use |
| --- | --- |
| Claude Code in your terminal | `cws-agent launch --name claude1 --agent claude --local-dir .` |
| Managed Agents tools on CoreWeave | `--claude-env ENV_ID`, plus a separate conversation client |
| Devin Cloud tools on CoreWeave | `--outpost NAME`, then use Devin Cloud |
| OpenAI Agents API tools on CoreWeave | `--agent openai`, then `cws-agent run NAME "prompt"`; see [setup](openai-agents.md) |

## Claude Managed Agents

Managed Agents can support interactive chat: your client sends messages,
streams responses, and handles approvals/interruptions. Anthropic runs the model
and agent loop; the worker runs sandbox tool calls on CoreWeave.
[Architecture](https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes)
and [interaction API](https://platform.claude.com/docs/en/managed-agents/events-and-streaming).

**This branch starts workers, but has no Managed Agents chat frontend.**
`login`, `run`, Telegram, and CLI history transfer do not drive these sessions.
Attaching to the worker only shows its logs.

### Start the worker

Create a self-hosted environment in the [Claude Console](https://platform.claude.com/environments),
then generate its environment key there. Your account needs Managed Agents access.

```bash
export ANTHROPIC_ENVIRONMENT_KEY='YOUR_ENVIRONMENT_KEY'
cws-agent launch --name claudebox --claude-env env_REPLACE_ME
```

The command installs `ant`, starts polling, and returns to your shell.
Add `--workers 2` for two workers. Their directories are `/workspace/claude/0`,
`/workspace/claude/1`, etc.; uploaded projects live separately at `/workspace/project`.

### Connect a conversation

You also need a Managed Agent with tools and a session assigned to this environment.
An environment ID alone is not a conversation.

1. Configure an agent with the standard `agent_toolset_20260401` toolset.
2. [Create a session](https://platform.claude.com/docs/en/managed-agents/sessions) with that agent ID and your environment ID.
3. Send a `user.message` and read the session event stream. Reuse the session ID for follow-ups; handle tool approvals when requested.

Use a **Console API key** in `ANTHROPIC_API_KEY` in your client, separately from
the worker. The environment key only authenticates workers; it is not an API key
or a Claude Code login token, even when token prefixes look alike.

To check connectivity, install `ant` on the client machine and set its Console API key:

```bash
ant beta:environments:work stats --environment-id env_REPLACE_ME
```

Check `workers_polling >= 1`, then submit a tool-using task through your client.
A startup message or polling count alone does not prove successful tool execution.

### Logs and restore

```bash
cws-agent connect claudebox --cmd 'tmux attach -t claude-0'
```

Detach with Ctrl-b, then d. Stop and restore from your local shell:

```bash
cws-agent down claudebox
# Re-export ANTHROPIC_ENVIRONMENT_KEY if needed.
cws-agent restore claudebox
```

The environment and worker count are saved. To override them on restore, use
`restore claudebox --claude-env env_REPLACE_ME --workers 2` instead.

The branch pins `ant` to 1.31.0 (`--env ANT_VERSION=x.y.z` overrides it).
It uses the CLI worker, not the SDK memory-store worker; memory-store mounting
is not implemented here. [Provider worker reference](https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes).

## Devin Outposts

Create a Linux outpost in Devin Cloud and copy its token. Outpost availability
depends on your organization.

```bash
export DEVIN_OUTPOSTS_TOKEN='YOUR_OUTPOST_TOKEN'
cws-agent launch --name devinbox --outpost my-outpost --workers 2
```

Start a Devin Cloud session with that outpost selected, or use Devin's existing
[Slack integration](https://docs.devin.ai/integrations/slack). This is separate
from the interactive `--agent devin` CLI mode.

```bash
cws-agent connect devinbox --cmd 'tmux attach -t outpost-0'
```

Detach with Ctrl-b, then d. Use `down devinbox` to snapshot/stop and
`restore devinbox` to restore; re-export `DEVIN_OUTPOSTS_TOKEN` first.
Restore keeps the outpost/count unless overridden with `--outpost` / `--workers`.
Snapshots preserve local files, not vendor-managed conversation state.
