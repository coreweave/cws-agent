# Cursor CLI

Run Cursor's terminal agent inside a CoreWeave sandbox. This is the native CLI,
not the Cursor desktop editor or a Cursor Cloud Agent worker.

```sh
cws-agent launch --name cursor1 --agent cursor --local-dir .
cws-agent login cursor1
cws-agent connect cursor1
```

Login prints a browser sign-in link; open it on your local machine. For automation,
set `CURSOR_API_KEY` before launch instead. The key is passed as an environment
variable, never a CLI argument. See [Cursor authentication](https://cursor.com/docs/cli/reference/authentication).

Headless runs and Telegram check sign-in before creating a chat. If logged out,
they ask you to run `cws-agent login NAME` instead of waiting on an unauthenticated
request. A configured key still needs a valid Cursor account and model access.

## Commands

```sh
cws-agent run cursor1 "Review the README"
cws-agent connect cursor1 --cmd 'cursor-agent ls'
cws-agent session resume cursor1 CHAT_ID --agent cursor
cws-agent snapshot cursor1
cws-agent restore cursor1 --connect  # after the original sandbox stops
```

`connect` opens Cursor in the running sandbox; `session resume` continues a native
conversation. Cursor's `ls` opens its own conversation picker. Local/remote
conversation upload and download are not supported: Cursor does not document a
portable CLI session export/import format. Snapshots preserve the sandbox's own
Cursor state. See [Cursor's command reference](https://cursor.com/docs/cli/reference/parameters).

## Telegram

```sh
cws-agent launch --name cursor-bot --agent cursor --telegram --dangerously-skip-permissions
```

The bridge retains a native Cursor chat ID for follow-up messages. Use the existing
[Telegram setup](messaging.md) for your bot or manager bot.

## Permissions and configuration

Cursor has no `accept-edits` CLI flag. The default keeps Cursor's native permission
configuration and prints a notice; `--permission-mode native` suppresses the
notice. `--dangerously-skip-permissions` maps to `--force`, which still honors
explicit deny rules. It does **not** auto-approve every MCP server. Headless runs
trust the sandbox workspace but do not silently bypass tool permissions.
See [Cursor permissions](https://cursor.com/docs/cli/reference/permissions).

Reviewed imports install skills in `~/.cursor/skills` and MCP configuration in
`~/.cursor/mcp.json` inside the sandbox. MCP authentication and missing server
executables still need setup there. For example:

```sh
cws-agent connect cursor1 --cmd 'cursor-agent mcp list'
cws-agent connect cursor1 --cmd 'cursor-agent mcp login SERVER_NAME'
```

Cursor binaries install under `/opt/agent`; auth, settings, and chat state use
`HOME=/workspace/home` and are included in snapshots. Restoring reinstalls the
native CLI without replacing saved state. Background CLI auto-updates are disabled
to keep downloaded binaries out of snapshots. The integration installs verified
Cursor CLI release `2026.09.02-c22c1a3` from Cursor's official download service.
