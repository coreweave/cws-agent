# Self-hosted workers

[Back to README](../README.md).

The provider manages the conversation and agent loop; tools run in your sandbox.
Set up [sandbox authentication](usage.md#authentication), then choose a provider.
For the OpenAI Agents API, see [its setup guide](openai-agents.md).

## Claude Managed Agents

Create a self-hosted environment and environment key in the
[Claude Console](https://platform.claude.com/environments). Your account needs
Managed Agents access.

```bash
export ANTHROPIC_ENVIRONMENT_KEY='YOUR_ENVIRONMENT_KEY'
cws-agent launch claudebox --claude-env env_REPLACE_ME
```

Launch starts a worker and returns to your shell. Add `--workers 2` for more
capacity. Workers use `/workspace/claude/0`, `/workspace/claude/1`, etc.; uploads
go to `/workspace/project`. This is separate from the interactive Claude Code CLI.

### Connect a conversation

Use a separate Managed Agents client:

1. Configure an agent with the `agent_toolset_20260401` toolset.
2. [Create a session](https://platform.claude.com/docs/en/managed-agents/sessions) using that agent and environment.
3. Send a `user.message`, [stream events](https://platform.claude.com/docs/en/managed-agents/events-and-streaming), and handle approvals. Reuse the session ID for follow-ups.

Your client uses a Console API key in `ANTHROPIC_API_KEY`; the environment key
only authenticates workers. `cws-agent run`, Telegram, and CLI history commands
do not drive Managed Agents conversations.
[Provider setup](https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes).

### Logs and restore

```bash
cws-agent connect claudebox --cmd 'tmux attach -t claude-0'
cws-agent down claudebox
cws-agent restore claudebox
```

Detach from logs with **Ctrl-b, d** before running `down`. Restore starts workers
with the saved environment and count; re-export `ANTHROPIC_ENVIRONMENT_KEY` first.
Override them with `--claude-env` and `--workers`.

## Devin Outposts

Create a Linux outpost in Devin Cloud and copy its token. Availability depends
on your organization.

```bash
export DEVIN_OUTPOSTS_TOKEN='YOUR_OUTPOST_TOKEN'
cws-agent launch devinbox --outpost my-outpost --workers 2
cws-agent connect devinbox --cmd 'tmux attach -t outpost-0'
```

Select the outpost in Devin Cloud when starting a session, or use its
[Slack integration](https://docs.devin.ai/integrations/slack).
This is separate from the interactive `--agent devin` CLI.

Detach from logs with **Ctrl-b, d**. Use `down devinbox` to snapshot and stop;
`restore devinbox` starts workers with the saved outpost and count. Re-export
`DEVIN_OUTPOSTS_TOKEN` first; use `--outpost` or `--workers` to override.
Snapshots preserve files, not running processes or provider conversations.
