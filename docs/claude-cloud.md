# Run Claude Code cloud sessions in a sandbox

[Back to README](../README.md).

Use `cws-agent cloud` to run a Claude Code self-hosted runner in a sandbox,
give it a repository task, and follow the conversation in Claude. Choose your
sandbox image and resources while Claude manages the cloud session.

## Choose a Claude workflow

| Workflow | Start with | Conversation and execution |
| --- | --- | --- |
| Interactive Claude Code | `cws-agent claude` | Claude Code runs in the sandbox. Work from its terminal or use [Remote Control](usage.md#claude-remote-control). |
| Claude Managed Agents | `cws-agent anthropic NAME --claude-env ENV_ID` | Your API client manages a Managed Agents conversation. Sandbox workers execute tools. See [Managed Agents setup](self-hosted.md#claude-managed-agents). |
| Claude Code cloud sessions | `cws-agent cloud start NAME --environment ENVIRONMENT_ID` | Claude manages the session. A self-hosted runner executes Claude Code in your sandbox. |

Anthropic-hosted [cloud environments](https://code.claude.com/docs/en/cloud-environments)
provide setup scripts, network settings, and cached dependencies. Their base
image can't be replaced. A
[self-hosted environment](https://code.claude.com/docs/en/self-hosted-environments)
lets you supply the compute and image. Anthropic still hosts the control plane,
conversation transcript, and model inference. Prompts and tool results go to
Anthropic.

## Prerequisites

Before starting a runner, complete these prerequisites:

- [Install cws-agent](install.md), which also installs `uv`, and configure
  [sandbox authentication](usage.md#authentication).
- Use a Claude Team or Enterprise organization with cloud sessions and
  self-hosted environments enabled. Self-hosted environments are in public beta.
  A personal Pro or Max subscription doesn't provide this feature.
- Have an organization Owner create a self-hosted environment on the
  [Cloud environments admin page](https://claude.ai/admin-settings/cloud-environments),
  following [Anthropic's quickstart](https://code.claude.com/docs/en/self-hosted-environments-quickstart).
  Obtain its `ccpool_...` ID and environment secret.
- Install Claude Code 2.1.224 or later locally and sign in with
  `claude auth login` to the organization that owns the environment.
- Connect GitHub to Claude and use a local checkout with an `origin` remote
  accessible through that connection.

The runner secret belongs to Claude Code cloud environments. The
`ANTHROPIC_ENVIRONMENT_KEY` and `env_...` ID used by Managed Agents can't
substitute for it.

## Start a runner and send a goal

In your local repository checkout, start a runner and send a goal. Replace
`[ENVIRONMENT-ID]` with the complete `ccpool_...` ID.

1. Read the environment secret without adding it to shell history:

   ```bash
   export SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET="$(uv run --no-project python -c 'import getpass; print(getpass.getpass("Claude environment secret: "))')"
   ```

2. Start the runner:

   ```bash
   cws-agent cloud start cloud1 --environment '[ENVIRONMENT-ID]'
   cws-agent cloud status cloud1
   ```

   The runner registers with Claude and polls for work. The environment's
   status in Claude changes to **Healthy** when a runner is available.

3. Send a goal:

   ```bash
   cws-agent cloud run cloud1 'Inspect the test suite and propose a focused improvement. Run the relevant tests.'
   ```

   The command dispatches through your local Claude login and returns the
   cloud session ID and URL. Open the URL to follow the work. Dispatch returns
   before the task finishes.

Claude checks out the repository from its remote. Local uncommitted files aren't
uploaded. Use `--repo /path/to/checkout` to select another local repository
or `--ref BRANCH` to select a remote branch. You can combine provisioning and
dispatch with `cloud start ... --goal 'YOUR GOAL'`.

Send a follow-up, replacing `[SESSION-ID]` with the returned `session_...` ID:

```bash
cws-agent cloud run cloud1 'Explain the test results.' --session '[SESSION-ID]'
```

Sessions are routed to the environment, not a particular sandbox. Other runners
in that environment can claim a task. This runner uses Anthropic's git proxy
and the session's GitHub connection for checkout. It doesn't require your local
GitHub token. See [git proxy requirements](https://code.claude.com/docs/en/self-hosted-environments-deploy#use-the-anthropic-git-proxy).

## Optional: Customize the sandbox

Choose resources when creating a runner:

```bash
cws-agent cloud start build1 --environment '[ENVIRONMENT-ID]' \
  --cpu 4 --memory 8Gi --disk 20Gi --lifetime 8h
```

Use `--image IMAGE` for a custom Debian-compatible sandbox image with Python,
Bash, and `apt-get`. The bootstrap installs current Claude Code when absent;
preinstalled versions must be 2.1.267 or later for git proxy registration.
Use `--setup setup.sh` to run a local Bash script in the
sandbox before the runner starts. For example, save this script as a `setup.sh` file:

```bash
#!/usr/bin/env bash
apt-get update
apt-get install -y shellcheck
```

Then pass `--setup setup.sh` to `cloud start`. Setup runs once per sandbox,
before any session checks out its repository. Use it for build tools and
dependencies, and use absolute paths in the script. The runner reads its
settings, hooks, and skills from `/workspace/home/.claude/`. Your local Claude
configuration isn't imported. For per-session configuration, including Model
Context Protocol (MCP) servers and lifecycle hooks, see
[Customize sessions](https://code.claude.com/docs/en/self-hosted-environments-configuration).

The sandbox's network controls apply to self-hosted execution. Anthropic-hosted
environment network settings don't configure the sandbox's network. Review
[Anthropic's deployment guidance](https://code.claude.com/docs/en/self-hosted-environments-deploy)
before serving private repositories or internal services.

## Monitor and stop

Check the runner's status and logs, then stop the sandbox when you finish:

```bash
cws-agent cloud status cloud1
cws-agent cloud logs cloud1
cws-agent down cloud1 --no-snapshot
```

`cloud logs` shows runner diagnostics. Read Claude's replies and review changes
in the cloud session. Before stopping the sandbox, save the work you need to
keep: `down --no-snapshot` stops compute without saving its files.

Each runner accepts one session at a time and locks to the first session's
owner. After its sessions end, it polls for that owner for up to an hour before
exiting. The runner begins retiring 5 minutes before the sandbox's lifetime
expires. A task that exceeds that lifetime can still be interrupted.

To provide fresh capacity after the runner exits, stop the sandbox and use
`cloud start` again. Cloud runners can't be restored from snapshots. A fresh
sandbox prevents a new runner owner from inheriting the previous owner's
checkout. This command manages individual runners. Fleet autoscaling requires
a separate orchestrator.

To test a dedicated environment from a `cws-agent` repository checkout, run
`uv run smoke_claude_cloud.py --environment '[ENVIRONMENT-ID]' --ref main`.
The smoke test creates billable compute, verifies that Claude runs a custom
command inside the sandbox, and stops its sandbox on success or failure.
