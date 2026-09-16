# Sessions and conversation history

[Back to README](../README.md). Examples assume an installed `cws-agent` and an authenticated CLI sandbox.

## Several agents in one sandbox

A launched sandbox is a devspace. Run several agents in it at once, each in its
own git worktree on its own branch, each a persistent `tmux` session you can
attach to and detach from while the agent keeps working. Worktrees live under
`/workspace/sessions`, so they survive `snapshot` and `restore`.

```bash
cws-agent launch --name work --local-dir . --detach

cws-agent session start work fix-auth  --prompt "fix the login bug"
cws-agent session start work add-tests --prompt "add tests for the parser"

cws-agent session ls work                  # SESSION  BRANCH  AGENT(running)  CHANGES
cws-agent session attach work fix-auth     # steer it. Ctrl-b then d to detach.
cws-agent session diff work fix-auth       # review its changes against the base
cws-agent session stop work add-tests      # kill agent and worktree, keep the branch
```

Each session forks from the project's `HEAD` onto `agent/<session>`. Override
with `--base` and `--branch`. Agents bypass permission prompts by default (YOLO).
Use `--permission-mode accept-edits` or `--permission-mode native` to override it.
See [permission modes](permissions.md) for mappings and aliases. On
`restore`, worktrees and branches come back, but their tmux processes do not.
Use `cws-agent session restart work fix-auth` to restart the agent in its
existing worktree, then attach. `session start` creates a new worktree and
rejects an existing session name. Conversation continuation depends on the
agent's native resume support; restoring files alone does not resume a model turn.

`session stop` refuses a worktree with uncommitted files. Commit or export the
work first. `cws-agent session stop work add-tests --force` explicitly discards
uncommitted worktree files; add `--delete-branch` only when you also want to
delete the branch and its retained work.

## Resume a conversation or restart a worktree agent

The CLI distinguishes a sandbox name, a managed worktree name, and the agent's
native conversation ID:

```bash
cws-agent session history dev1                  # Claude/Codex + OpenCode workspace projects
cws-agent session history dev1 --json           # metadata only; no prompts
cws-agent session resume dev1 CONVERSATION_ID # infer Claude/Codex; use saved cwd
cws-agent session resume dev1 brisk-otter --agent devin
cws-agent session history cursor1 --agent cursor # native interactive picker
cws-agent session resume cursor1 CHAT_ID --agent cursor
cws-agent session resume open1 ses_SESSION_ID --agent opencode
cws-agent session restart dev1 backend          # latest conversation in existing worktree
cws-agent session restart dev1 backend --session-id SESSION_ID --attach
cws-agent session start dev1 docs --agent codex # another already-installed harness
```

`session ls dev1` lists managed worktrees; `session history dev1` reads saved CLI
conversations across Claude and Codex. Devin's public interface exposes history
through `/ls --all`: run `cws-agent connect dev1 --agent devin`, then that command.
Its private history storage is not parsed. Native IDs for Devin require
`--agent devin`. These commands do not enumerate Devin Cloud / Outpost sessions.

OpenCode uses its native metadata API: up to 1000 recent sessions per project in
`/workspace/project` and managed worktrees, without reading its live database.
For another project, use `session history NAME --agent opencode --cwd /path`.
OpenCode resume infers the saved directory; `--cwd` selects another project.
Cursor history opens its native interactive picker, so `--json` is unavailable;
resume requires `--agent cursor` and defaults to `/workspace/project` (override
with `--cwd` for another worktree).

`session restart` reuses the branch and files of a stopped worktree. If its tmux
agent is still running, it reports that status; add `--attach` to join the process.
It never resets the branch
or discards changes. The harness is saved when the worktree starts; old worktrees
fall back to the sandbox's harness, or accept `--agent`. Install and authenticate
additional harnesses before selecting them. `session resume --cwd /remote/path`
can override a saved directory; otherwise a missing directory fails.
Exit the original conversation before resuming it elsewhere to avoid concurrent
writers to its history.

The existing `cws-agent restore dev1` command still restores a sandbox filesystem
snapshot. It is independent of resuming the conversation within that sandbox.

Native command contracts: [Claude sessions](https://code.claude.com/docs/en/sessions),
[Codex resume](https://developers.openai.com/codex/cli/reference/#codex-resume), and
[Devin session history](https://docs.devin.ai/cli/essential-commands#session-history).

## Move CLI conversation history between machines

Claude/Codex support the file-based flow below. OpenCode uses native export/import
with explicit `--agent opencode`; it refuses an existing destination session ID,
including with `--replace`. See [OpenCode transfer](opencode.md).
Cursor has no documented portable CLI import/export format, so transfer fails
with an explanation before accessing a sandbox; snapshot/restore preserves its
state within a sandbox instead.

```bash
# Stop the source agent first, and sync the matching project separately.
cws-agent sync dev1 ./my-repo
cws-agent session transfer dev1 --upload LOCAL_SESSION_ID
cws-agent session resume dev1 LOCAL_SESSION_ID

# Later, in your local checkout (bring remote code changes back separately):
cws-agent session transfer dev1 --download REMOTE_SESSION_ID --replace
# The command prints the exact local resume command.
```

Both transfer directions report the encoded bundle's total size, transferred
bytes, percentage, and speed. Preparation and history installation are separate
stages. A completed transfer bar does not imply installation succeeded; wait for
the final resume instructions. Progress goes to stderr, with plain status lines
when redirected. These commands transfer conversation history, not project files.

`--agent claude|codex` disambiguates an ID. `--cwd` sets the absolute destination
project path (upload default: `/workspace/project`; download default: current
local directory). The original ID is retained. Existing destination files fail
without `--replace`; replacement keeps each previous file beside it with a
`.cws-backup-<unique-id>` suffix. Transfer never launches an agent or runs a model.

This imports native CLI conversation data, not a text summary: Claude's main
JSONL, the session's subagent transcripts/assets, and its file-history snapshots;
Codex's classic `sessions/.../rollout-*.jsonl`. Working-directory metadata,
including recognized Codex workspace roots and world-state paths, is remapped;
prompts and tool results retain their original text. Inline images
are preserved. Existing project files, credentials, tool/MCP configuration,
project memory, and attachments outside the session directory are not copied.
Reattach external images and sync required files separately. File rewind and
old absolute paths in tool outputs may still refer to the source machine.

Only recognized native JSONL layouts are supported; Codex sidecar/paginated
histories, desktop/cloud-only histories, and unfamiliar formats are unsupported.
Keep agent versions compatible. Each transfer is limited to 32 MiB and 256 files,
rejects symlinks, and validates every destination before staging private files.
Stop active writers before exporting or replacing; concurrent updates can make
history inconsistent. Conversation data can contain sensitive prompts, tool
outputs, and images, so transfer only to a sandbox you intend to share it with.

Devin CLI supports [ATIF export](https://docs.devin.ai/cli/reference/commands),
but its published CLI has no documented native import. `--agent devin` fails
with an explanation. [Devin Outposts](https://docs.devin.ai/cloud/outposts/overview)
execute cloud-managed sessions; copying CLI history does not import them.
Start with Claude and Codex CLI for this workflow.
