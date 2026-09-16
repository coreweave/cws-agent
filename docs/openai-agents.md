# OpenAI Agents API on CoreWeave

[Back to README](../README.md).

OpenAI manages the conversation and agent loop; its executor runs tools in your
sandbox. `cws-agent run` sends prompts to the same API session and streams replies.
This uses the [Agents API self-hosted protocol](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted).
Use `--agent codex` for the interactive Codex CLI.

## Credentials

Export three credentials locally:

| Variable | Purpose | Sent to sandbox |
| --- | --- | --- |
| `WANDB_API_KEY` | Sandbox access; `CWSANDBOX_API_KEY` is the CoreWeave alternative | No |
| `OPENAI_API_KEY` | Create sessions and send prompts | No |
| `OPENAI_EXECUTOR_API_KEY` | Connect the executor | Yes, as `CODEX_API_KEY` |

Create the executor key in [Agents → Environments → Keys](https://platform.openai.com/agents?tab=environments&environment_view=keys).
It must share the session's organization, project, and user or service account.
Set unrelated permissions to **None**. The application key needs
`api.agents.read`, `api.agents.write`, and `api.responses.write`.

The application key stays on your machine; the CLI rejects forwarding it with
`--env` or `--env-passthrough`. Keep credential files outside uploaded directories.

## Launch and send work

```bash
cws-agent launch api1 --agent openai --local-dir .
cws-agent run api1 "Inspect the project and summarize its test setup"
cws-agent run api1 "Add a test for the issue you identified"
```

Launch waits for the executor to connect and prints the API session ID.
The default model is `gpt-6-astra`; use `--openai-model MODEL` to choose another.

To connect an existing API session, add `--openai-session SESSION_ID`. It must use
a `self_hosted` environment with `workspace_directory: "/workspace/project"`.
Only one executor may connect. Configure instructions, tools, and capabilities
through the Agents API.

## Inspect, stop, and restore

```bash
cws-agent status api1
cws-agent connect api1                  # open a project shell
cws-agent down api1                     # snapshot files and stop compute
cws-agent restore api1                  # reconnect the same API session
cws-agent run api1 "Continue the previous task"
```

Snapshots preserve files and the API session ID; OpenAI retains the conversation.
Restore starts a new executor, so keep both OpenAI credentials exported locally.
Wait for work to finish before snapshotting or stopping.

Stopping the sandbox does not delete the API session, and deleting the API
session does not stop compute. When finished permanently, clean up both.

`run --timeout SECONDS` limits waiting, not cloud execution. After a failed request
or disconnected stream, inspect the API session before resubmitting;
[stream recovery](https://developers.openai.com/api/docs/guides/agents-api/sessions/events#how-to-recover-a-disconnected-stream)
explains how. Finish any paused workspace upload before submitting work.

Configure agent tools and policies through OpenAI. CLI login, local skills/MCP
import, Telegram, CLI history transfer, worktree sessions, and `--yolo` do not
apply to API sessions.
