# Agent sessions and history

## Sandbox or agent session?

A sandbox is the running compute and workspace; an agent session is a saved chat
inside it. Use the full sandbox `NAME` printed by `cws-agent list`, such as
`claude-fa97da5d`, for sandbox commands. `SESSION_ID` is the ID issued by the
harness, such as Claude Code or Codex, and shown by `cws-agent session history
SANDBOX`. It is not a separate ID created by `cws-agent`.

| Task | Command |
| --- | --- |
| Open a fresh agent terminal in a running sandbox | `cws-agent connect SANDBOX` |
| Continue a saved Claude session | `cws-agent claude --resume SESSION_ID` |
| Recreate a stopped sandbox from a snapshot | `cws-agent restore SANDBOX` |

`claude`, `codex`, and `opencode` can find an agent session across running agent
sandboxes. If the same session was copied into multiple sandboxes, choose
one explicitly:

```bash
cws-agent claude SANDBOX --resume SESSION_ID
cws-agent codex SANDBOX --resume SESSION_ID
```

Claude, Codex, and OpenCode use the saved project directory;
`--cwd /remote/path` overrides it. Devin and Cursor default to `/workspace/project`.
`--resume` does not create a sandbox or restore a snapshot. If stopped, run `restore`
first, then resume the agent session. Creation options such as `--local-dir` and
`--detach` do not apply to resuming an agent session. Permission flags still apply.
Devin and Cursor require a sandbox name with `--resume` because their histories
cannot be searched automatically. The `anthropic` and `openai` worker shortcuts
do not support this flag.

Top-level `cws-agent resume SANDBOX` remains an alias for `restore`.
`cws-agent session resume SANDBOX SESSION_ID` remains supported for agent sessions.

## Several agents in one sandbox

Start an agent for each task in its own Git worktree and branch:

```bash
cws-agent launch work --local-dir . --detach
cws-agent login work
# Sign in, then exit the agent.
cws-agent session start work fix-auth --prompt "Fix the login bug"
cws-agent session start work add-tests --prompt "Add parser tests"
cws-agent session ls work
cws-agent session attach work fix-auth
cws-agent session diff work fix-auth
```

Detach with **Ctrl-b, d**; the agent keeps working. Worktrees live under
`/workspace/sessions/NAME` on `agent/NAME` branches, sharing the repository's Git
objects. Agents share the sandbox and credentials; worktrees separate files,
not access permissions.

New worktrees start from project `HEAD`; commit local changes before uploading
if you want them included. Override with `--base` or `--branch`. Agents default
to [YOLO](permissions.md). `session start` requires a new name; `attach` only
joins an existing session. Install and authenticate another CLI before selecting
it with `--agent`.

```bash
cws-agent session stop work add-tests
```

Stop removes the worktree and keeps its branch. It refuses uncommitted changes;
`--force` discards them. `--delete-branch` also deletes the retained branch.

## Resume an agent session or restart a worktree agent

`work` below is a sandbox, `fix-auth` is a worktree session, and
`SESSION_ID` identifies an agent's saved chat.

```bash
cws-agent session history work
cws-agent session resume work SESSION_ID
cws-agent session restart work fix-auth --attach
```

`resume` continues a saved agent session. `restart` uses an existing worktree's
files and branch, continuing its latest agent session where supported; use
`--session-id ID` to choose one. Stop the original agent session before resuming
it elsewhere.

`restore` recreates the sandbox from a snapshot. It restores files and branches,
not running processes; use `session restart` afterward.

History supports [Claude](https://code.claude.com/docs/en/sessions),
[Codex](https://developers.openai.com/codex/cli/reference/#codex-resume), and
OpenCode workspace projects. For another OpenCode directory, add
`--agent opencode --cwd /remote/path`. Cursor uses its native picker:

```bash
cws-agent session history cursor1 --agent cursor
cws-agent session resume cursor1 CHAT_ID --agent cursor
cws-agent session resume open1 ses_SESSION_ID --agent opencode
cws-agent session resume devin1 brisk-otter --agent devin
```

For [Devin history](https://docs.devin.ai/cli/essential-commands#session-history),
connect to its CLI and run `/ls --all`. Devin and Cursor `session resume` require
`--agent`; Cursor defaults to `/workspace/project` unless you supply `--cwd`.
These commands do not list cloud-managed conversations.

## Move CLI session history between machines

Stop source and destination agents first. For Claude and Codex:

```bash
cws-agent sync work ./my-repo
cws-agent session transfer work --upload LOCAL_SESSION_ID
cws-agent session resume work LOCAL_SESSION_ID
# Later, from your local project directory:
cws-agent session transfer work --download REMOTE_SESSION_ID
```

Transfer copies history, including supported inline images and Claude subagent
assets. Sync project files separately. Credentials, tool configuration, project
memory, and external attachments are not copied. Wait for the printed resume
instructions before continuing.

Use `--agent claude` or `--agent codex` to disambiguate an ID. `--cwd` selects an
absolute destination project path; defaults are `/workspace/project` for uploads
and your current directory for downloads. Existing history requires `--replace`,
which saves backups beside replaced files.

Transfers support recognized native JSONL histories, up to 32 MiB and 256 files.
Use compatible agent versions; desktop/cloud-only and Codex sidecar histories
are unsupported. Old paths in message text and file-rewind data may still refer
to the source machine. History can contain sensitive prompts, tool output, and
images.

[OpenCode](opencode.md#continue-a-conversation-elsewhere) has native export/import
with different limits. Cursor transfer is unsupported. Devin offers
[ATIF export](https://docs.devin.ai/cli/reference/commands), but no supported native
import; its history and [Outpost sessions](https://docs.devin.ai/cloud/outposts/overview)
cannot be transferred here.
