# Cursor CLI

Run Cursor's terminal agent in a sandbox:

```sh
cws-agent launch cursor1 --agent cursor
```

Use `cws-agent login cursor1` and open its browser link locally, or export
`CURSOR_API_KEY` before launch. You need Cursor account and model access.
[Authentication](https://cursor.com/docs/cli/reference/authentication).

## Commands

```sh
cws-agent run cursor1 "Review the README"
cws-agent session history cursor1 --agent cursor
cws-agent session resume cursor1 CHAT_ID --agent cursor
cws-agent down cursor1
cws-agent restore cursor1 --connect
```

History opens Cursor's conversation picker. Snapshots preserve saved logins,
settings, and chats; re-export environment-only keys before restoring.
Conversation upload/download is unsupported. This runs the CLI, not the desktop
editor or a Cursor Cloud Agent worker.
[Command reference](https://cursor.com/docs/cli/reference/parameters).

## Telegram

```sh
cws-agent launch cursor-bot --agent cursor --telegram
```

Follow-up messages share a Cursor chat. See [Telegram setup](messaging.md).

## Permissions and configuration

YOLO uses `--force` by default, honoring explicit deny rules without automatically
approving every MCP server. `--permission-mode native` keeps Cursor's policy.
Cursor has no `accept-edits` flag; that mode also uses its native policy.
[Permissions](https://cursor.com/docs/cli/reference/permissions).

[Config sync](config-import.md) imports skills and MCP definitions. Install
missing server executables and authenticate MCP servers in the sandbox:

```sh
cws-agent connect cursor1 --cmd 'cursor-agent mcp list'
cws-agent connect cursor1 --cmd 'cursor-agent mcp login SERVER_NAME'
```
