# Skills and MCP imports

[Back to README](../README.md).

Launch, normal connect, and restore check for changed local skills and MCP
definitions. **Nothing is imported without confirmation.** The preview lists
tool and skill names. Type **a** for all available items, **s** to skip, or
comma-separated names to choose a few; Enter also skips. Then confirm with **y**.
Typos prompt again, and quoted choices such as `'all'` are accepted.

```sh
cws-agent config preview dev1
cws-agent config preview dev1 --verbose
cws-agent config sync dev1
cws-agent connect dev1 --no-config-sync
```

Detached launch skips imports. There is no background watcher. Changed local
items and changed environment values appear at the next check; unchanged imports
are skipped by hash.
This supports Claude Code, Codex, Devin CLI, OpenCode, and Cursor CLI,
not Managed Agents or Outposts.

## What gets copied

OpenCode uses its native `mcp` schema and `{env:NAME}` references; Cursor uses
`mcpServers` and `${env:NAME}`. Neither imports provider or MCP OAuth login stores.
Local executable dependencies and nonportable file references need remote setup.

| Agent | Local skills | Local MCP configuration |
| --- | --- | --- |
| OpenCode | `~/.config/opencode/skills`, project `.opencode/skills`, compatible skill directories | Global/project `opencode.json` or `.jsonc` |
| Cursor CLI | `~/.cursor/skills`, project `.cursor/skills`, compatible skill directories | `~/.cursor/mcp.json`, project `.cursor/mcp.json` |
| Claude Code | `~/.claude/skills`, project `.claude/skills` | `~/.claude.json` user `mcpServers`, project `.mcp.json` |
| Codex | `~/.codex/skills`, `~/.agents/skills`, project `.agents/skills` | `~/.codex/config.toml`, project `.codex/config.toml` |
| Devin CLI | `~/.config/devin/skills`, `~/.agents/skills`, project `.devin/skills` and `.agents/skills` | User `~/.config/devin/config.json` and `mcp_config.json`; project `.devin/mcp_config.json` and `.devin/mcp_config.local.json` |

Later sources win for duplicate IDs. Only these roots are scanned, not plugin
caches, ancestor directories, or alternate config-home locations.
Use `--local-dir PATH` to choose the project source; otherwise it is the current directory.

Skills go under the agent's user directory in `/workspace/home`.
MCP entries are merged with names prefixed `cws-import-`; unrelated settings remain.
Restart the agent to load changes.

## Review before importing

Use `--verbose` (or `-v`) on launch, connect, restore, or config preview/sync to show
source paths, sizes, hashes, MCP endpoints/commands, and skip reasons. The default
lists only names and marks skipped items. Required executables/environment names
are shown for selected tools before confirmation, including which variables will
be copied and which are missing locally. Values are never printed. For duplicate names, select
`skill:NAME` or `mcp:NAME` to disambiguate. Review the source files too:
secrets embedded in ordinary prose or scripts cannot always be detected.

- Limit: **5 MiB / 500 text files per selection**, 256 KiB per file.
- Binary content and symlinks block a skill. Dotfiles, key files, caches,
  dependencies, and build output are excluded.
- Selected MCPs retain their inline `env` values and HTTP headers. Referenced
  variables are copied from local environment files, the `cws-agent` process,
  and Claude's `env` settings.
  Login stores are not copied.
- MCP executables and dependencies are not installed. A later invocation of
  `npx` or `uvx` may download packages under the agent's permissions.
- Local executable paths, unsupported laptop-relative arguments, and
  apparent credentials in arguments require manual correction.
- Remote MCPs support HTTPS, literal headers, and explicit environment references. Embedded URL
  credentials, query strings/fragments, SSE, disabled servers,
  and unsupported advanced settings are blocked.

Importing configuration does not execute its tools or scripts. Loading it in
the agent later can. There is no cross-agent configuration conversion.

## Environment values

Environment transfer is part of confirming the selected imports. It includes
MCP `${VAR}` / `${env:VAR}` references, Codex `env_vars`, bearer-token variables,
and environment-backed headers. Inline server values stay scoped to that server;
aliases and `${VAR:-default}` expressions retain their original meaning. Claude's
[environment expansion rules](https://code.claude.com/docs/en/mcp#environment-variable-expansion-in-mcpjson)
apply to its imported configuration.

Skills are scanned for shell `$VAR` / `${VAR}` references and common Python
`os.getenv`, `os.environ` and JavaScript `process.env` access. This is a static
scan: dynamically constructed names and plain prose declarations may need an
explicit variable selection:

```sh
cws-agent config sync dev1 --select skill:review --env-var SERVICE_API_KEY --yes
```

The importer reads these sources, with later sources taking precedence:

1. The selected project's `.env`, then `.env.local`.
2. Exported variables in the shell that runs `cws-agent`.
3. For Claude, `env` in `~/.claude/settings.json`, project
   `.claude/settings.json`, then project `.claude/settings.local.json`.
4. Files explicitly supplied with `--env-file PATH`, in the order supplied.

```sh
cws-agent config sync dev1 --local-dir /path/to/project --env-file /path/to/secrets.env
```

Use `--verbose` to see which source supplied each variable; values stay hidden.
Only referenced or explicitly selected variables are uploaded. Dotenv files are
parsed as data, including quotes, empty values, `${VAR}` aliases and defaults;
shell commands are never executed. Whole environment files and shell startup
files are not copied. A variable exported only in another terminal session must
be supplied through one of these sources. Empty values are preserved.
Variables missing locally are listed so you can set them locally and sync again,
or provide them remotely. References with defaults do not require a local value.
An unset local variable preserves a previously imported remote value while its
reference remains; removing the reference removes that item's saved value.
Local process settings such as `HOME`, `PATH`, and `PWD` keep their remote values.

Referenced values are sent over stdin and stored with mode `0600` in
`/workspace/home/.cws-import-env.json` and `.cws-import-env.sh`. The shared launch
wrapper loads them for new agent processes, commands, shells, and tmux sessions.
They persist in snapshots alongside agent credentials. MCP configuration files
also use mode `0600`; the import manifest stores hashes rather than credential
values. Restart existing agent processes to load updated values.

Only selected items contribute environment values. If several imported items use
the same variable, sync those items together when rotating its value. Conflicting
values block the batch and name the variable without revealing its values.
Remote edits to the managed environment files also block a sync.

Environment transfer does not install missing binaries or authenticate OAuth
servers. The importer reports missing executables; `/mcp` still needs a live
connection check after restarting the agent. A failed server without a missing
variable warning can have a separate endpoint, authentication, or dependency issue.

## Automation

After reviewing the preview, explicitly select and confirm items:

```sh
cws-agent config sync dev1 --select skill:review --select mcp:docs --yes
```

Replace those IDs with your selections. Without a terminal, automatic discovery
only prints a preview. `--yes` does not select everything; `--select all` is explicit.

## Updates and recovery

Updates replace previously imported files only if their remote hashes still match.
Remote edits or unmanaged conflicts block the batch. Source-deleted files within
an updated skill are removed; deleting an entire local skill/server does not
automatically remove its remote copy.

The manifest is `/workspace/home/.cws-imports.json`. A lock serializes imports,
but a crash during a multi-file update can leave partial changes. Stop agent
activity and run `cws-agent snapshot NAME` first if you need rollback.

This refreshes local configuration, **not upstream skills, tools, or agent binaries**.
Automatic Claude binary updates remain disabled; fresh sandboxes reinstall binaries
outside `/workspace` to keep snapshots smaller.

Tests run offline with the [README test command](../README.md#development);
they do not verify live MCP/provider authentication.
