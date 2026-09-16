# OpenCode

```sh
cws-agent launch open1 --agent opencode
```

Use `cws-agent login open1` to choose a provider, or export its API key before
launch. Common provider keys are forwarded automatically; use
`--env-passthrough NAME` for others. Provider access and billing are separate
from sandbox access. [CLI reference](https://opencode.ai/docs/cli/).

## W&B Serverless Inference

Export your full [W&B API key](https://wandb.ai/settings). Your account needs
[Inference access and credits](https://docs.wandb.ai/inference/prerequisites).

```sh
export WANDB_API_KEY='YOUR_FULL_WANDB_KEY'
cws-agent launch open-wandb --agent opencode --wandb
```

`WANDB_API_KEY` is forwarded only with `--wandb` or explicit passthrough.
The agent can use it for both inference and sandbox access.
`CWSANDBOX_API_KEY` takes precedence for sandbox access. The preset disables
other model providers; code execution stays in your sandbox.

The default is [DeepSeek V4 Pro 0813](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813).
Select another [W&B catalog model](https://docs.wandb.ai/inference/models) with
`--wandb-model MODEL_ID`. Image support depends on the model.

### Resume and restore

```sh
cws-agent session history open-wandb --agent opencode
cws-agent session resume open-wandb ses_EXAMPLE --agent opencode
cws-agent down open-wandb
cws-agent restore open-wandb --wandb --connect
```

Re-export `WANDB_API_KEY` before restoring. Snapshots preserve the preset,
saved logins, and conversation data, but not environment-only credentials.
To stop using the preset, remove
`/workspace/home/.config/opencode/cws-wandb.json` in the sandbox.

## Permissions

YOLO is the default: OpenCode's `--auto` accepts approval requests while keeping
explicit deny rules. `accept-edits` supplies defaults only when no policy is
configured: file edits are allowed, other tools ask. Unanswered approvals fail
in headless runs. `native` keeps OpenCode's own policy.

```sh
cws-agent connect open1 --permission-mode accept-edits
cws-agent connect open1 --permission-mode native
```

See [OpenCode permissions](https://opencode.ai/docs/permissions/).

## Skills, tools, and images

Use [config sync](config-import.md) for skills and MCP definitions, and workspace
sync for project settings. Install missing MCP executables and authenticate
servers in the sandbox. See [configuration](https://opencode.ai/docs/config/),
[MCP](https://opencode.ai/docs/mcp-servers/), and
[skills](https://opencode.ai/docs/skills/).

Native `opencode run --file PATH` accepts attachments when the model supports
them; remote terminal clipboard behavior differs from your local desktop.

## Continue a conversation elsewhere

Install OpenCode locally and stop both agents before transferring history:

```sh
cws-agent session transfer open1 --agent opencode --upload ses_EXAMPLE
cws-agent session transfer open1 --agent opencode --download ses_EXAMPLE --cwd /absolute/local/project
```

Transfers use [native export/import](https://github.com/anomalyco/opencode/blob/v1.18.29/packages/opencode/src/cli/cmd/import.ts).
Destination IDs must be new; `--replace` cannot overwrite OpenCode history.
The encoded limit is 46 MiB. Project files, credentials, child sessions, and
external attachments need separate transfer. A failed import may be partial;
wait for success before resuming.
