# Skills and MCP imports

[Back to README](../README.md).

Launch, connect, and restore offer to import changed local skills and MCP tools.
Choose **a** for all, comma-separated names for a few, or Enter to skip; then
confirm with **y**. Nothing is copied without confirmation.

```sh
cws-agent config preview dev1 --verbose
cws-agent config sync dev1
cws-agent connect dev1 --no-config-sync
```

Detached launch skips imports. There is no background watcher. Imports support
Claude Code, Codex, Devin CLI, OpenCode, and Cursor CLI.

## What gets copied

| Agent | Local skills | Local MCP configuration |
| --- | --- | --- |
| Claude Code | `~/.claude/skills`, project `.claude/skills` | `~/.claude.json` user `mcpServers`, project `.mcp.json` |
| Codex | `~/.codex/skills`, `~/.agents/skills`, project `.agents/skills` | `~/.codex/config.toml`, project `.codex/config.toml` |
| Devin CLI | `~/.config/devin/skills`, `~/.agents/skills`, project `.devin/skills` and `.agents/skills` | `~/.config/devin/config.json` and `mcp_config.json`; project `.devin/mcp_config.json` and `.devin/mcp_config.local.json` |
| OpenCode | `~/.config/opencode/skills`, project `.opencode/skills`, compatible skill directories | Global/project `opencode.json` or `.jsonc` |
| Cursor CLI | `~/.cursor/skills`, project `.cursor/skills`, compatible skill directories | `~/.cursor/mcp.json`, project `.cursor/mcp.json` |

Later sources win for duplicate IDs. Plugin caches, ancestor directories, and
alternate config-home locations are not scanned. `--local-dir PATH` selects the
project; otherwise the current directory is used.

Skills go into the agent's user directory. MCP names get a `cws-import-` prefix;
unrelated settings remain. **Restart the agent to load changes.**

## Review before importing

`--verbose` shows sources, sizes, commands, endpoints, and skip reasons.
Before confirmation, the importer lists required executables and environment
variable names, including missing variables. Values stay hidden.
Use `skill:NAME` or `mcp:NAME` for ambiguous names.

- Limit: **5 MiB / 500 text files per selection**, 256 KiB per file.
- Binary files and symlinks block a skill. Dotfiles, key files, caches,
  dependencies, and build output are excluded.
- Selected MCPs include inline environment values and HTTP headers. Review the
  source: credentials embedded in prose or scripts may escape detection.
- Login stores and dependencies are not copied. Fix laptop-only paths and
  install required executables remotely. There is no cross-agent conversion.
- Remote MCPs require supported HTTPS configuration. URL credentials,
  query strings/fragments, SSE, disabled servers, and unsupported settings are blocked.

Import does not execute tools. Starting the agent can connect to MCP servers or
run commands such as `npx` and `uvx` that download packages.

## Environment values

Confirmation includes variables referenced by selected tools and skills. For
variables the skill scanner cannot detect, select them explicitly:

```sh
cws-agent config sync dev1 --select skill:review --env-var SERVICE_API_KEY --yes
cws-agent config sync dev1 --local-dir /path/to/project --env-file /path/to/secrets.env
```

Later sources take precedence:

1. Project `.env`, then `.env.local`.
2. Exported variables in the shell running `cws-agent`.
3. Claude's user, project, then project-local `settings.json` environment settings.
4. Explicit `--env-file` files, in supplied order.

Only referenced or selected values are uploaded; whole environment files and
shell startup files are not. Empty values and reference defaults are preserved.
Missing local values leave previously imported values in place while their
references remain. Remote `HOME`, `PATH`, and `PWD` stay unchanged.

Values are stored in owner-only files under `/workspace/home`, **included in
snapshots**, and loaded by new agent processes. Restart after changing them.
When rotating a shared variable, sync all items using it together; conflicting
values or remote edits block the update. Environment transfer does not log into
OAuth servers or install dependencies.

## Automation

Review the preview, then select and confirm:

```sh
cws-agent config sync dev1 --select skill:review --select mcp:docs --yes
```

Replace these IDs with your selections. Noninteractive discovery only previews.
`--yes` confirms a selection; use `--select all` to select everything.

## Updates and recovery

Remote edits or unmanaged conflicts block updates. Updating a skill removes files
deleted locally; deleting an entire local skill or server leaves its remote copy.

A crash can leave partial changes. Stop agent activity and run
`cws-agent snapshot NAME` before an import if you need rollback. Import state is
recorded in `/workspace/home/.cws-imports.json`.

Sync copies local configuration; it does not update upstream skills, tools, or
agent binaries.
