# Skills and MCP imports

[Back to README](../README.md).

Import local skills and MCP tools to make them available to agents in your sandbox.

Launch, connect, and restore offer to import changed local skills and MCP tools.
In an interactive terminal, **Skills** and **Tools (MCP)** appear as collapsed sections
with all available updates selected. Press **Enter** to upload both in one step,
or **s** to skip all imports.

To customize the selection, use **Up** or **Down** to move, **Right** or **Left** to expand or
collapse a section, and **Space** to toggle an item or a whole section. Unavailable
items can't be selected. Press **Enter** to upload your selection, or **Ctrl-C**
to cancel the command. During launch or restore, the footer labels this as an
abort: it stops the newly created sandbox. Use **s** to skip imports and continue.
Clearing an item leaves any existing remote copy intact.

Terminals without interactive display support use a text prompt with all
available updates selected: **Enter** or **a** uploads them, **s** skips, and
comma-separated names upload only those items. There is no second confirmation.
With an explicit `--select` selection, **Enter** accepts the selected items at
the confirmation prompt. **n** or **s** skips.

```sh
cws-agent config preview dev1 --verbose
cws-agent config sync dev1
cws-agent connect dev1 --no-config-sync
```

Detached launch skips imports. There is no background watcher. Imports support
Claude Code, Codex, Devin CLI, OpenCode, and Cursor CLI.

<a id="what-gets-copied"></a>

## Import sources

| Agent | Local skills | Local MCP configuration |
| --- | --- | --- |
| Claude Code | `~/.claude/skills`, project `.claude/skills` | `~/.claude.json` user `mcpServers`, project `.mcp.json` |
| Codex | `~/.codex/skills`, `~/.agents/skills`, project `.agents/skills` | `~/.codex/config.toml`, project `.codex/config.toml` |
| Devin CLI | `~/.config/devin/skills`, `~/.agents/skills`, project `.devin/skills` and `.agents/skills` | `~/.config/devin/config.json` and `mcp_config.json`. Project `.devin/mcp_config.json` and `.devin/mcp_config.local.json` |
| OpenCode | `~/.config/opencode/skills`, project `.opencode/skills`, compatible skill directories | Global or project `opencode.json` or `.jsonc` |
| Cursor CLI | `~/.cursor/skills`, project `.cursor/skills`, compatible skill directories | `~/.cursor/mcp.json`, project `.cursor/mcp.json` |

Later sources win for duplicate IDs. Plugin caches, ancestor directories, and
alternate config-home locations aren't scanned. `--project-dir PATH` selects the
project. Otherwise the current directory is used.

Skills go into the agent's user directory. MCP names get a `cws-import-` prefix.
Unrelated settings remain. Restart the agent to load changes.

## Review before importing

`--verbose` shows sources, sizes, commands, endpoints, and skip reasons.
Expand a section and highlight an item to see its requirements or skip reason.
Details include required executables and environment variable names, including
missing variables. Values stay hidden. For the text prompt or `--select`, use
`skill:NAME` or `mcp:NAME` for ambiguous names.

Imports have the following limits:

- Each selection is limited to 5 MiB and 500 text files, 256 KiB per file.
- Binary files and symlinks block a skill. Dotfiles, key files, caches,
  dependencies, and build output are excluded.
- Selected MCPs include inline environment values and HTTP headers. Review the
  source: credentials embedded in prose or scripts may escape detection.
- Login stores and dependencies aren't copied. Fix laptop-only paths and
  install required executables remotely. There is no cross-agent conversion.
- Remote MCPs require supported HTTPS configuration. URL credentials,
  query strings or fragments, SSE, servers that are turned off, and unsupported settings are blocked.

Import doesn't execute tools. Starting the agent can connect to MCP servers or
run commands such as `npx` and `uvx` that download packages.

## Environment values

Confirmation includes variables referenced by selected tools and skills. For
variables the skill scanner can't detect, select them explicitly:

```sh
cws-agent config sync dev1 --select skill:review --env-var SERVICE_API_KEY --yes
cws-agent config sync dev1 --project-dir /path/to/project --env-file /path/to/secrets.env
```

Later sources take precedence:

1. Project `.env`, then `.env.local`.
2. Exported variables in the shell running `cws-agent`.
3. Claude's user, project, then project-local `settings.json` environment settings.
4. Explicit `--env-file` files, in supplied order.

Only referenced or selected values are uploaded. Whole environment files and
shell startup files aren't. Empty values and reference defaults are preserved.
Missing local values leave previously imported values in place while their
references remain. Remote `HOME`, `PATH`, and `PWD` stay unchanged.

Values are stored in owner-only files under `/workspace/home`, included in
snapshots, and loaded by new agent processes. Restart after changing them.
When rotating a shared variable, sync all items using it together. Conflicting
values or remote edits block the update. Environment transfer doesn't log into
OAuth servers or install dependencies.

## Automation

Review the preview, then select and confirm:

```sh
cws-agent config sync dev1 --select skill:review --select mcp:docs --yes
```

Replace these IDs with your selections. Noninteractive discovery only previews.
`--yes` confirms a selection. Use `--select all` to select everything.

## Updates and recovery

Remote edits or unmanaged conflicts block updates. Updating a skill removes files
deleted locally. Deleting an entire local skill or server leaves its remote copy.

A crash can leave partial changes. If you need rollback, stop agent activity and run
`cws-agent snapshot NAME` before an import. Import state is
recorded in `/workspace/home/.cws-imports.json`.

Sync copies local configuration. It doesn't update upstream skills, tools, or
agent binaries.
