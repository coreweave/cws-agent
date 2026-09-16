# OpenCode

Run the OpenCode CLI in a sandbox:

```sh
cws-agent launch --name open1 --agent opencode --local-dir .
cws-agent login open1
cws-agent connect open1
cws-agent run open1 "Explain this project"
```

Login opens OpenCode's provider chooser. Existing `OPENCODE_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`,
`OPENROUTER_API_KEY`, `GROQ_API_KEY`, and `WANDB_API_KEY` are passed at sandbox creation.
Other provider variables can be supplied explicitly with `--env NAME=value`.
Provider access and billing are separate from sandbox access.

The integration installs OpenCode **1.18.29**; override with
`--env OPENCODE_VERSION=VERSION`. The executable and caches live outside the
snapshot. Credentials, configuration, and conversation data live under
`/workspace/home`, so `snapshot` and `restore` preserve them. Treat snapshots as
sensitive backups. [OpenCode CLI reference](https://opencode.ai/docs/cli/)

## W&B Serverless Inference

With `--wandb`, `WANDB_API_KEY` is forwarded for inference and gives the agent
its sandbox-access permissions too. Without `--wandb`, it stays local unless
you explicitly pass it through. In
[W&B User Settings](https://wandb.ai/settings), select **Create new API key**,
give it a name, then copy the **full key** immediately (it is shown only once).
Your W&B account needs Serverless Inference access and available credits.
[Prerequisites](https://docs.wandb.ai/inference/prerequisites)

```sh
export WANDB_API_KEY='YOUR_FULL_WANDB_KEY'
cws-agent launch --name open-wandb --agent opencode --wandb --local-dir .
```

That's it: no provider chooser or hand-written JSON. The preset uses
`deepseek-ai/DeepSeek-V4-Pro-0813` at `https://api.inference.wandb.ai/v1`,
including background/title requests. Other providers are disabled for this preset.
Code and prompts sent to the model use W&B Inference; execution stays in the sandbox.

An explicit `CWSANDBOX_API_KEY` takes precedence for sandbox access.
`WANDB_API_KEY` still supplies inference credentials; `--wandb` selects the preset.

### Why this model?

Default selected September 8, 2026: **DeepSeek V4 Pro 0813** is in the
[W&B catalog](https://docs.wandb.ai/inference/models). Its publisher reports
87.9 on Terminal-Bench 2.1 versus GLM 5.2's 81.0, and 61.5 on NL2Repo versus
48.9. These are publisher-reported results with the publisher's harness and
reasoning settings, not an independent OpenCode comparison or a promise of
identical results here. They support our coding-default recommendation.
[Benchmark comparison](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813)

Prefer GLM? Override at launch or restore:

```sh
cws-agent launch --name open-glm --agent opencode --wandb --wandb-model zai-org/GLM-5.2
```

`--wandb-model` accepts a W&B catalog ID. The default is text-only; do not
expect image attachments to work with DeepSeek V4 Pro or GLM 5.2.

### Resume and restore

```sh
cws-agent connect open-wandb
cws-agent session history open-wandb --agent opencode
cws-agent session resume open-wandb ses_EXAMPLE --agent opencode
cws-agent snapshot open-wandb
cws-agent down open-wandb --no-snapshot
cws-agent restore open-wandb --wandb --connect
```

Re-export `WANDB_API_KEY` before restoring. The preset and conversation data
survive snapshots; the environment-only key does not. The preset lives in
`/workspace/home/.config/opencode/cws-wandb.json` and is applied by the launcher
without rewriting project or imported MCP configuration. It stores an environment
reference, not the key. To stop using it, remove that preset file in the sandbox.

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

OpenCode's persistent config is `/workspace/home/.config/opencode/opencode.json`;
skills go in its `skills/` directory. Project `opencode.json` and `.opencode/`
arrive with the workspace. MCP executables and provider/MCP authorization must
be available remotely. [Config](https://opencode.ai/docs/config/),
[MCP](https://opencode.ai/docs/mcp-servers/),
[skills](https://opencode.ai/docs/skills/)

Image handling depends on the selected model. Native `opencode run --file PATH`
accepts file attachments; terminal clipboard integration is not equivalent to
a local desktop clipboard. Do not assume arbitrary models accept images.

## Continue a conversation elsewhere

```sh
cws-agent session history open1 --agent opencode
cws-agent session resume open1 ses_EXAMPLE --agent opencode
cws-agent session transfer open1 --agent opencode --upload ses_EXAMPLE
cws-agent session transfer open1 --agent opencode --download ses_EXAMPLE --cwd /absolute/local/project
```

Transfers use OpenCode's native JSON export/import, not its live database. Install
OpenCode locally too. Stop both agents before transferring. Destination session
IDs must be new; `--replace` cannot overwrite OpenCode history. The encoded limit
is 46 MiB. Workspace files, credentials, child sessions, and externally referenced
attachments require separate transfer. The native importer relocates session
metadata; message text is preserved, and the imported transcript is verified
before success is reported. A failed import may be partial and is never retried
automatically. [Native import implementation](https://github.com/anomalyco/opencode/blob/v1.18.29/packages/opencode/src/cli/cmd/import.ts)
