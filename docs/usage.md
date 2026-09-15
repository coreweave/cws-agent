# Everyday usage

[Back to README](../README.md). Run `cws-agent` from your local project directory.

## Authentication

This CLI uses `CWSANDBOX_API_KEY` for sandbox access. OpenCode's `--wandb`
preset additionally uses `WANDB_API_KEY` for inference; it does not switch the
sandbox SDK's authentication strategy. [W&B setup](opencode.md#wb-serverless-inference).
Set `CWSANDBOX_BASE_URL` only for a non-default endpoint.

| Agent | Sign in after launch | Optional credential before launch |
| --- | --- | --- |
| Claude Code | `cws-agent login dev1`, then `/login` | `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` |
| Codex | `cws-agent login cdx1` | `OPENAI_API_KEY` |
| Devin CLI | `cws-agent login dvn1`, then paste its login token | Use the login flow; forwarded keys may not be accepted by your account |
| OpenCode | `cws-agent login open1`, then choose a provider | `OPENCODE_API_KEY` or your supported provider key; [details](opencode.md) |
| Cursor CLI | `cws-agent login cursor1`, then open its browser link | `CURSOR_API_KEY`; [details](cursor.md) |

For a Claude token, run `claude setup-token` locally and export only the printed
token as `CLAUDE_CODE_OAUTH_TOKEN`. Do not capture the interactive command with
shell substitution. It requires a local Claude installation.
API-key usage is billed through the API; it is not subscription authentication.

Saved login files survive snapshots. Environment-only credentials do not:
re-export them before restoring a sandbox. Forward additional variables by name:

```bash
cws-agent launch --name dev1 --local-dir . --env-passthrough MY_SERVICE_TOKEN
```

Never commit credentials or paste real keys into issues or chat.

## Upload your project

```bash
cws-agent launch --name dev1 --local-dir . --exclude data --exclude .cache
cws-agent sync dev1 .
```

Files go to `/workspace/project`, including `.git` and project agent settings.
Common dependency/build directories are excluded. Use `--no-git` to omit Git history.
Successful project uploads automatically save a workspace snapshot, including
already-imported skills/MCP configuration and stored agent state. `--no-snapshot`
on `launch` or `sync` opts out. Snapshot failure leaves uploaded data intact and
prints a snapshot retry command; only a reported READY snapshot is restorable.
Launch sizes the disk automatically from the filtered, uncompressed local files:
allow 4 KiB per entry for filesystem overhead, budget another copy for the staged
archive, add 50% headroom plus 5 GiB, and
round up to a 5 GiB increment (minimum 10 GiB). The chosen size is printed before
provisioning. For example, 20.4 GiB across roughly 546,000 entries selects 65 GiB.
An explicit `--disk` overrides this choice. Without `--local-dir`, the default
remains 10 GiB. This sizes new sandboxes; `sync` does not resize an existing disk.
The estimate cannot anticipate unlimited file growth or future dependency installs.
Review what you upload: project sync is a file copy, not the filtered
[skills/MCP import](config-import.md).

`--local-dir .` selects your **whole current directory**, including nested projects.
`.gitignore` is not applied; exclude secrets such as `.env` explicitly.
Scanning reports discovered bytes, packaging shows the selected source-byte total,
and upload shows the exact compressed total and remotely verified bytes.
Terminals show a progress bar when wide enough; redirected logs get periodic plain
status lines on stderr. Remote extraction is reported separately before success.

Upload/extraction gets a size-based deadline instead of the SDK's five-minute
default: allow one second per compressed MiB and per 100 files, plus ten minutes
(minimum fifteen minutes). Override with `--transfer-timeout 4h` on `launch` or
`sync` if needed. This does not extend the sandbox's `--lifetime`.
Each 32 MiB chunk is checksum-verified and committed before progress advances.
Chunks have a five-minute deadline and one-minute input-stall limit; failed chunks
get up to two retries, checking for a saved receipt first. `Upload verified` means
all compressed bytes are saved; extraction is a separate step and can be retried
without uploading again.

### Resume an upload

```bash
cws-agent uploads                         # find retained upload IDs
cws-agent sync telegram2 --resume-upload ID
cws-agent uploads --discard ID            # delete only this local cache
```

The error prints the exact recovery command. Resume uses the **original cached
archive**, not your current local files; no scanning or packaging is repeated.
Missing or damaged remote chunks are resent. A failed extraction keeps all chunks;
a saved completion marker prevents reapplying an already successful extraction.
To send newer edits afterward, run a normal `sync NAME .`.

Failed uploads keep the sandbox running and billable until its **original lifetime
expires** or you stop it. Resume before expiration. If it has expired, explicitly
target a replacement sandbox with the same cached ID; chunks not available there
must be uploaded again. This cannot recover uploads made by older CLI versions.
For Telegram launches, imports and sign-in happen before the background upload;
the agent remains usable if that upload pauses. Follow its private log or the
bot's status message for the resume ID. Non-Telegram failed launches print the
remaining config/login/connect commands.

Cached archives live in `~/.local/state/cws-agent/uploads` (owner-only permissions,
not encrypted) and need compressed-archive-sized local disk space. Remote staging
uses `/workspace/.cws-uploads`. Success deletes the local archive and remote chunks,
keeping a small remote completion receipt. Discard removes only the local cache;
remote staging remains until successful extraction or sandbox deletion. Cache
directories are excluded from project scans.

Extraction merges into the project and is **not atomic**: a failure can leave
partial files. Telegram background uploads preserve existing remote files rather
than overwrite agent edits (`sync --preserve-existing` selects the same behavior).
That choice is retained in the resumable archive. Other syncs overwrite as before.
Avoid external writers during extraction/recovery.
`sync --clean` deletes project contents only after every chunk is verified;
resuming such an upload requires `--clean` again. Extraction is never automatically
replayed after a failure.

To check the upload fix without a large project, run these from the repository:

```bash
# Offline interruption/resume tests using real local tar and checksum operations.
uv run --no-project --python 3.12 python -m unittest discover -s tests -p 'test_resumable_upload.py'
# Live interruption/resume check in isolated temporary directories.
uv run smoke_upload.py telegram2
```

The live check uses an **existing** sandbox, 8 MiB of random test data, and a
temporary directory that is removed after verification. It does not touch your
project or start/stop agents. Expect tens of seconds on a healthy connection;
individual transfers have short deadlines. It prints the SDK version being
tested. This checks the failure handling, not sustained multi-gigabyte throughput.

Project packaging tolerates a working directory that changes: new files after
the scan wait for the next sync; removed paths are skipped. Edited files are
captured individually, retried once if they change while being read, then skipped
with a warning if still unstable. This is a best-effort copy, not an atomic
snapshot. Packaging totals adjust for changed sizes and retries; the compressed
upload total is exact. One file at a time is buffered (large files use temporary
disk space). Once packaging finishes, later edits cannot affect that upload.
History transfers retain their stricter consistency checks.

`sync --clean` **deletes the remote project before extraction**. Use it only when
you intend to replace remote changes. Private repositories need remote Git authentication.

## Get changes back

There is no `pull` command. Export a patch, or push a branch after configuring
Git credentials inside the sandbox:

```bash
cws-agent exec dev1 "git diff" > dev1.patch
cws-agent session diff work fix-auth > fix-auth.patch
```

Review a patch before applying it with `git apply`. Diffs omit untracked files;
use `git add -N` for new files before exporting, or commit them before pushing.

```bash
cws-agent exec dev1 "git push origin HEAD:agent/fix-auth"
```

Git authentication is not copied from your laptop. `GH_TOKEN` alone does not
authenticate plain Git. For HTTPS GitHub remotes, install `gh`, supply its
credential, and configure [gh auth setup-git](https://cli.github.com/manual/gh_auth_setup-git).
SSH remotes need their own SSH setup.

## Snapshots

```bash
cws-agent snapshot dev1         # snapshot without stopping
cws-agent down dev1             # snapshot, then stop compute
cws-agent restore dev1 --connect  # restore into a new sandbox
cws-agent snapshots dev1
cws-agent prune dev1 --keep 3   # delete older READY snapshots
```

`snapshot` creates a backup; `snapshots` lists backups. `checkpoint` remains a
compatibility alias for `snapshot`.

Only `/workspace` persists: project files, worktrees, and saved agent state
under `/workspace/home`. Processes do not persist; agent binaries outside the
volume are reinstalled. Use [session restart](sessions.md) for retained worktrees.

**Snapshot caveat:** the snapshot service needs readable files and cannot archive
symlinks directly. Preparation temporarily broadens read permissions (including
stored credentials) and substitutes link placeholders. A restore manifest preserves
the original targets and modes; the CLI restores live attributes afterward, even
on snapshot failure, and rehydrates them when resuming a snapshot. Use only trusted
processes/users in the sandbox. This host's Telegram requests coordinate with
capture; unrelated processes or clients on other hosts are not paused.

New snapshots record the configured disk size, reused by `restore` unless `--disk`
overrides it. Legacy snapshots without disk metadata still default to 10 GiB;
specify a sufficient disk explicitly for those. Snapshots are not continuous
backups: later edits require another snapshot or `down` without `--no-snapshot`.

`down --no-snapshot` stops without saving current changes. Snapshots accumulate
until pruned; quotas and maximum sandbox lifetime depend on your account.

## Reconnect from another machine

Install the CLI there and use a CoreWeave credential with access to the sandbox:

```bash
cws-agent list
cws-agent status dev1
cws-agent connect dev1
```

`list` shows start dates and times in your local timezone, with a timezone label.

Use `restore dev1 --connect` instead if it was stopped and has a snapshot.
`connect` opens the CLI; [session resume](sessions.md) selects a saved conversation.
Dropped connections are not automatically reattached.

Compatibility: `attach` aliases `connect`; top-level `resume` aliases `restore`;
`restore --attach` aliases `restore --connect`. `session resume` still continues
an existing conversation, and `session attach` still joins a running tmux process.

## Claude Remote Control

For an eligible Claude subscription login:

```bash
cws-agent login dev1
cws-agent rc dev1
```

Complete `/login`, exit to your local shell, then run `rc` and open the URL in
its logs. API keys and setup-token-only authentication are not sufficient for
this workflow. This is Claude Code Remote Control, not Managed Agents.
See [provider requirements](https://code.claude.com/docs/en/remote-control).

## Options

Run `cws-agent --help` for commands or `cws-agent launch --help` for all launch flags.

| Scope | Options |
| --- | --- |
| Launch and restore sizing | `--cpu 4 --memory 8Gi --disk 20Gi --lifetime 7d` (defaults: 2, 4Gi, auto for local-directory launch or 10Gi otherwise, 8h) |
| Launch and restore environment | `--image IMAGE`, `--mode serverless\|cks`, `--env KEY=VALUE`, `--env-passthrough KEY` |
| Launch only | `--local-dir PATH`, `--repo-url URL`, `--exclude NAME`, `--no-git`, `--detach` |
| Restore and connect | `restore NAME --connect` |
| Worker backends | [Environment/outpost target and worker count](self-hosted.md) |
| Agent policy | [Accept edits, bypass, or native](permissions.md) |

`launch --detach` prepares a CLI sandbox without starting its interactive agent.
Use `login`, `connect`, or `session start` afterward. `exec NAME "command"` runs a
shell command without invoking an agent; `run NAME "prompt"` invokes the agent
headlessly. Headless approval prompts cannot be answered.
