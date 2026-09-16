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
