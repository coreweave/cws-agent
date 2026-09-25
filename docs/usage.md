# Everyday usage

[Back to README](../README.md). Run these commands from your local shell.

## Launch an agent

Choose an agent directly:

```bash
cws-agent claude
cws-agent codex --import-codex-auth
cws-agent claude --local-dir . --detach
cws-agent devin my-devin
cws-agent opencode my-opencode --wandb
cws-agent cursor my-cursor
```

Names are optional for shortcuts and `launch`. When omitted, the CLI generates
`HARNESS-` followed by 8 random lowercase hexadecimal characters, such as
`claude-fa97da5d`. Claude Managed Agents uses the `anthropic-` prefix;
`--claude-env` and `--outpost` select the worker's prefix. The generated name is
printed during launch; use it with `connect`, `status`, or `down` afterward.
Pass `NAME` or `--name NAME` to choose your own name.

Each `cws-agent AGENT [NAME]` shortcut is equivalent to
`cws-agent launch [NAME] --agent AGENT`, except `anthropic` selects the existing
`ant` backend (`launch NAME --agent ant`). Supported agents and backends:

| Command | Agent or backend |
| --- | --- |
| `claude` | Claude Code |
| `codex` | Codex CLI |
| `devin` | Devin CLI; add `--outpost NAME` for a Devin outpost worker |
| `opencode` | OpenCode; add `--wandb` for W&B inference |
| `cursor` | Cursor CLI |
| `anthropic` | Claude Managed Agents worker; requires `--claude-env ENV_ID` |
| `openai` | OpenAI Agents API executor |

Worker backends keep the agent in the provider's service while tools execute in
your sandbox. Set up the [provider credentials](self-hosted.md), then launch:

```bash
cws-agent anthropic claude-worker --claude-env env_REPLACE_ME
cws-agent openai api1
cws-agent devin devin-worker --outpost my-outpost
```

For creation, shortcuts accept the same options as `launch` except `--agent`, including the
`--name NAME` compatibility form. Existing backend requirements still apply.
Use `cws-agent AGENT --help` to see the options for an agent.
To continue a saved agent session, use `cws-agent claude --resume SESSION_ID` (or
`codex` / `opencode`). This finds its running sandbox; it does not create one.
See [sandbox restoration and agent sessions](sessions.md#sandbox-or-agent-session) for
explicit sandbox selection, other agents, and stopped sandboxes.

Existing `launch` commands remain supported, including the default Claude Code
agent and other `--agent` choices. To reconnect to a running sandbox, use
`cws-agent connect NAME`.

## Authentication

### W&B accounts

Use your [W&B API key](https://wandb.ai/authorize):

```bash
export WANDB_API_KEY='YOUR_WANDB_KEY'
cws-agent launch my-claude
```

A saved `wandb login` also works. `--agent opencode --wandb` forwards the key
for inference, giving the agent its sandbox-access permissions too. For access and setup, see
[Serverless Sandboxes](https://docs.wandb.ai/sandboxes).

### CoreWeave accounts

Use a **CoreWeave API access token**. In [Cloud Console → Tokens](https://console.coreweave.com/tokens),
choose **Create Token** and copy the **Token Secret**:

```bash
export CWSANDBOX_API_KEY='YOUR_COREWEAVE_TOKEN_SECRET'
cws-agent launch my-claude
```

This token takes precedence over W&B credentials for sandbox access.
See [token setup](https://docs.coreweave.com/security/authn-authz/manage-api-access-tokens)
and [CoreWeave Sandbox setup](https://docs.coreweave.com/products/sandboxes/get-started).
Your organization must have sandbox access enabled.

### Agent sign-in

Sandbox credentials create compute; sign in to your agent separately.

| Agent | Sign in | Optional environment variable |
| --- | --- | --- |
| Claude Code | `/login` inside Claude | `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` |
| Codex | `cws-agent login my-codex` | `OPENAI_API_KEY` |
| Devin CLI | `cws-agent login my-devin` | Use its login flow |
| OpenCode | `cws-agent login my-opencode` | [Provider key or W&B inference](opencode.md) |
| Cursor CLI | `cws-agent login my-cursor` | `CURSOR_API_KEY` |

For a Claude subscription token, run `claude setup-token` locally and copy only
the printed token into `CLAUDE_CODE_OAUTH_TOKEN`. API keys use API billing.
Saved logins survive snapshots; re-export environment-only credentials before restore.
To pass another variable, add `--env-passthrough VARIABLE_NAME` to launch or restore.

### Reuse a local Codex login

If device-code sign-in is unavailable, sign in to Codex on your local machine
using its browser flow, then opt in to importing that ChatGPT login at startup:

```bash
codex login
cws-agent launch my-codex --agent codex --import-codex-auth
```

The CLI reads `auth.json` from your local `CODEX_HOME` (default `~/.codex`), sends
it over the sandbox connection, and stores it at `/workspace/home/.codex/auth.json`
with mode `0600` in a `0700` directory. It checks `codex login status` before opening
Codex. This checks that Codex recognizes the cache; it does not make a model request
or prove that a saved token can still access the service.

The import is explicit. Normal launch/connect never reads your local login.
With the option, launch/restore omit `OPENAI_API_KEY` from the sandbox environment
so the imported ChatGPT login is selected. API-key launch still works without it.
Local OS-keyring credentials are not exported. If your login is stored in a keyring,
create a file-based cache with `codex -c cli_auth_credentials_store='"file"' login`,
if your workspace policy allows file storage.

For an existing Codex sandbox, import and open its terminal in one command, or
import without opening a terminal:

```bash
cws-agent connect my-codex --import-codex-auth
cws-agent login my-codex --import-codex-auth
```

Stop already-open Codex processes before importing, then reconnect. Existing
sandboxes with an `OPENAI_API_KEY` environment variable or a custom `CODEX_HOME`
cannot use this import; launch a fresh sandbox with `--import-codex-auth` instead.
The flag is only available for the Codex CLI harness, not the `openai` executor backend.

Snapshots retain the imported login. To replace a restored cache with your current
local login before reconnecting:

```bash
cws-agent restore my-codex --import-codex-auth --connect
```

Treat the cache and snapshots containing it as credentials: the sandbox can read
the tokens. Import replaces an existing remote cache; a failed login-status check
restores the previous file. Concurrent imports are rejected while one is in progress.
The local cache is never modified or copied back from the sandbox.
If remote authentication later expires or is revoked, sign in
locally again and explicitly re-import it. See the
[official headless authentication guidance](https://learn.chatgpt.com/docs/auth#fallback-authenticate-locally-and-copy-your-auth-cache).

## Upload your project

```bash
cws-agent launch project1 --local-dir . --exclude .env --exclude data
cws-agent sync project1 . --exclude .env --exclude data
```

Files go to `/workspace/project`. `.git` is included; common dependency and build
directories are excluded. **`.gitignore` is not applied.** Exclude secrets explicitly;
`--no-git` skips Git history. Private Git clones need credentials inside the sandbox.

CLI launches and `sync` save a snapshot after upload unless you pass `--no-snapshot`.
If snapshot creation fails, the files remain uploaded; retry with `cws-agent snapshot project1`.
Worker launch modes do not take this automatic snapshot.

Launch sizes the disk to fit your files with headroom; `--disk` overrides it.
Without local files, the default is 10 GiB. `sync` cannot resize an existing disk.
Large transfers get a size-based timeout; use `--transfer-timeout 4h` to override it.
This does not extend the sandbox's lifetime.

Uploads normally block. With `launch --telegram`, upload runs in a separate local
process after sign-in; keep the laptop awake and online. Background uploads preserve
existing remote files so they do not overwrite the agent's edits.

Sync merges files and overwrites matching paths. `--clean` deletes the remote
project before extraction. Avoid concurrent edits: packaging and extraction are
not atomic, and files changed during packaging may be skipped with a warning.

### Resume an upload

Run the recovery command printed after a failure:

```bash
cws-agent uploads
cws-agent sync project1 --resume-upload ID
cws-agent uploads --discard ID
```

Resume reuses the original cached archive and verified chunks. Send newer changes
with a separate `sync`. An extraction failure may leave partial files; resume it
before using the project. A clean upload requires `--clean` again on resume.

A paused upload leaves compute running until you stop it or its lifetime expires.
Resume before expiration, or target a replacement sandbox and resend missing chunks.

Archives live in `~/.local/state/cws-agent/uploads`; they need local disk space and
are not encrypted. Successful upload removes the archive and remote chunks.
`--discard` removes only the local archive.

## Get changes back

Export a patch, or push a branch using Git credentials configured in the sandbox:

```bash
cws-agent exec project1 "git diff" > project.patch
cws-agent session diff project1 fix-auth > fix-auth.patch
cws-agent exec project1 "git push origin HEAD:agent/fix-auth"
```

Review patches before `git apply`. Diffs omit untracked files; use `git add -N`
before exporting new files, or commit before pushing. There is no `pull` command.

Git credentials are not copied from your laptop. For GitHub HTTPS remotes,
[configure `gh auth setup-git`](https://cli.github.com/manual/gh_auth_setup-git)
inside the sandbox; `GH_TOKEN` alone does not authenticate plain Git.
SSH remotes need SSH credentials there.

## Snapshots

For applications that control all workspace writers, see
[recoverable checkpoint and stop](checkpoints.md) for an opt-in durable journal,
retry recovery, and restore from an exact committed snapshot.

```bash
cws-agent snapshot my-claude          # save without stopping
cws-agent down my-claude              # save and stop compute
cws-agent restore my-claude --connect  # restore and open the agent
cws-agent snapshots my-claude          # list saved snapshots
cws-agent prune my-claude --keep 3     # delete older READY snapshots
```

Snapshots preserve `/workspace`: project files, worktrees, and saved logins.
They do not preserve running processes. Use [session restart](sessions.md) for
saved worktrees, and take another snapshot after later edits.
Only READY snapshots can be restored.

**During agent workspace capture, file read permissions are temporarily broadened, including on
saved credentials, and symlinks become placeholders.** The CLI restores their
original permissions and targets afterward. Use trusted processes in the sandbox
and pause unrelated writers during capture; only this host's Telegram requests
coordinate automatically.

[Shell sandboxes](shell.md#save-work-and-stop-compute) use native filesystem
snapshots without this metadata helper. Restore them with `shell --snapshot`.

`cws-agent restore` reuses the saved disk size; override it with `--disk`. For this
command, snapshots without disk metadata default to 10 GiB. `down --no-snapshot`
stops compute without saving current changes; snapshots remain until pruned.

## Reconnect from another machine

Install `cws-agent` and use credentials with access to the same sandbox:

```bash
cws-agent list
cws-agent status my-claude
cws-agent connect my-claude
```

If stopped, use `restore my-claude --connect`. To continue a saved agent session,
use [session resume](sessions.md). Connections do not reconnect automatically.

## Claude Remote Control

Use a Claude subscription login, then open the URL printed by `rc`:

```bash
cws-agent login my-claude
cws-agent rc my-claude
```

Complete `/login` and exit Claude before running `rc`. API keys and setup tokens
alone do not support this workflow. [Provider requirements](https://code.claude.com/docs/en/remote-control).

## Options

Run `cws-agent --help` or `cws-agent launch --help` for all commands and flags.

| Task | Options |
| --- | --- |
| Set resources | `--cpu 4 --memory 8Gi --disk 20Gi --lifetime 7d` |
| Choose an image or placement | `--image IMAGE`, `--mode serverless\|cks` |
| Pass environment variables | `--env KEY=VALUE`, `--env-passthrough KEY` |
| Clone or upload a project | `--repo-url URL`, `--local-dir PATH` |
| Create without opening an agent | `launch NAME --detach` |
| Run a shell command | `exec NAME "command"` |
| Send a one-shot agent task | `run NAME "prompt"` |

Defaults: 2 CPUs, 4 GiB memory, 8-hour lifetime. Disk is automatic for local uploads,
otherwise 10 GiB. `--detach` prepares the sandbox; use `login`, `connect`, or
`session start` afterward.

Compatibility aliases: `attach` → `connect`, `resume` → `restore`,
`checkpoint` → `snapshot`, and `restore --attach` → `restore --connect`.
