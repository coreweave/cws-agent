# OpenAI Agents API on CoreWeave

[Back to README](../README.md).

`--agent openai` creates an OpenAI Agents API session and runs its
`codex exec-server` executor in CWSandbox. OpenAI manages the conversation and
agent loop. Commands and file operations run in `/workspace/project` on
CoreWeave. `cws-agent run` submits a prompt from your local machine and streams
the response; subsequent prompts use the same API session.

This uses the [Agents API self-hosted environment protocol](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted).
The [Agents SDK sandbox provider interface](https://developers.openai.com/api/docs/guides/agents/sandboxes)
is a separate integration for applications that run their own agent loop.
`--agent codex` continues to launch the interactive Codex CLI.

## Credentials

You need three credentials:

| Variable on your machine | Purpose | Sent to the sandbox |
| --- | --- | --- |
| `WANDB_API_KEY` (or `CWSANDBOX_API_KEY` for CoreWeave accounts) | Sandbox access | No |
| `OPENAI_API_KEY` | Create API sessions, submit prompts, inspect results | No |
| `OPENAI_EXECUTOR_API_KEY` | Register the sandbox executor with OpenAI | Yes, as `CODEX_API_KEY` |

Create the restricted executor key in [Agents → Environments → Keys](https://platform.openai.com/agents?tab=environments&environment_view=keys).
It must share the session's organization, project, and user or service account.
Set unrelated permissions to **None**. The application key needs
`api.agents.read`, `api.agents.write`, and `api.responses.write`.

Export these variables in your local shell. Keep secret files outside any
directory you upload. To load an existing dotenv file for one invocation:

```bash
uv run --env-file /absolute/path/to/credentials.env /path/to/cws-agent/cws-agent --help
```

The executor key lives in the sandbox environment and must be supplied again
on restore. The launcher rejects forwarding `OPENAI_API_KEY` through `--env`
or `--env-passthrough` for this backend.

## Launch and send work

```bash
cws-agent launch --name api1 --agent openai --local-dir .
cws-agent run api1 "Inspect the project and summarize its test setup"
cws-agent run api1 "Add a test for the issue you identified, then run it"
cws-agent status api1
```

Launch returns after the API reports the executor connected. It prints the API
session ID and saves that ID in `/workspace/.cws-agent-backend.json`.
The default model is `gpt-6-astra`; select another available model with
`launch ... --openai-model MODEL`.

You can supply an existing session with `--openai-session SESSION_ID`.
It must use a `self_hosted` environment with
`workspace_directory: "/workspace/project"`. Only one executor may connect
to a session. Configure instructions, tools, and capabilities through the
Agents API when creating an existing session.

The executor uses the pinned Codex package `0.155.0-alpha.6`, installed under
`/opt/agent` in `node:22-bookworm`. Launch and restore check that
`codex exec-server` is available. It requires outbound access to OpenAI's
registration and executor services; no inbound sandbox endpoint is exposed.

## Inspect, stop, and restore

```bash
cws-agent connect api1                       # shell in the project
cws-agent connect api1 --cmd 'tail -100 /workspace/project/worker.log'
cws-agent down api1                          # snapshot files and stop compute
cws-agent restore api1                      # reconnect the same API session
cws-agent run api1 "Continue the previous task"
```

Snapshots contain the workspace and API session ID. OpenAI retains the
conversation. Restore reinstalls the executor and retrieves current environment
connection details from that same API session; it does not create a new one.
Keep both OpenAI credentials configured on the client for restore.

Stopping CWSandbox does not delete the API session. When permanently finished,
delete it separately through the OpenAI API. Deleting an API session does not
stop CoreWeave compute. Wait for work to finish before snapshotting or stopping:
an executor can receive work from other API clients, outside local CLI locks.

For a paused upload, finish the printed `sync --resume-upload` command before
submitting work. The API session mapping is retained with the sandbox.

`run --timeout SECONDS` bounds waiting for output; it does not cancel cloud work.
If a request or stream fails, inspect the printed API session ID and its saved
items in OpenAI before resubmitting. Requests are not automatically retried.
See [recovering a stream](https://developers.openai.com/api/docs/guides/agents-api/sessions/events#how-to-recover-a-disconnected-stream).

This backend supports `launch`, `run`, `connect` (shell), `sync`, `status`,
`snapshot`, `down`, and `restore`. Configure agent tools and policies through
OpenAI. Local CLI login, skills/MCP import, Telegram, CLI history transfer,
worktree agent sessions, and `--yolo` do not drive API sessions.

## Verification

The offline suite checks credential isolation, API-session reuse, cleanup,
executor connection checks, and streamed root-turn completion.

For a live test, configure all three credentials and run:

```bash
uv run smoke_openai_agents.py
```

This creates temporary billable compute and runs model turns. It independently
reads a model-written file through CWSandbox, checks a follow-up, snapshots and
restores the workspace, and verifies a further turn uses the same API session.
It cleans up its sandbox, snapshots, and API session, including on failure.
An installed executor or a connected status alone does not prove tool execution.

### Verified on 2026-09-15

- Offline suite: 431 tests passed, with two existing skips. Git signing was
  disabled for temporary test-repository commits.
- Live: `smoke_openai_agents.py` passed with `gpt-6-astra`, OpenAI Python SDK
  3.14.0, CWSandbox SDK 1.14.2, and Codex executor 0.155.0-alpha.6.
- CWSandbox independently read the model-created file and its follow-up edit.
  A fresh sandbox restored the snapshot, reconnected the same API session,
  and executed a third turn using the original conversation's value.
- The smoke test stopped its compute and deleted its snapshots and API session;
  its report contained no cleanup errors.
