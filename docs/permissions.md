# Agent permission modes

CLI invocations request edit permissions by default where supported:

| Agent | Default flags | `--yolo` |
| --- | --- | --- |
| Claude Code | `--permission-mode acceptEdits` | `--dangerously-skip-permissions` |
| Devin CLI | `--permission-mode accept-edits` | `--permission-mode bypass` |
| Codex | `--sandbox workspace-write` with approval policy `on-request` | `--dangerously-bypass-approvals-and-sandbox` |
| OpenCode | Edit/read defaults with existing restrictions preserved | `--auto` (explicit denies still apply) |
| Cursor CLI | Native permissions, with a notice: no accept-edits flag exists | `--force` (explicit denies still apply) |

Claude and Devin still ask for shell commands. Codex's closest equivalent also
allows commands inside its workspace sandbox; requests outside that boundary
can ask for approval. The sandbox user is root, and Claude Code refuses
`--dangerously-skip-permissions` as root unless `IS_SANDBOX=1` is set, so every
agent invocation exports it. Noninteractive runs cannot answer a permission prompt, so
actions requiring approval may fail. Use explicit bypass for unattended tasks
that need it. Vendor or organization restrictions still apply.

```bash
cws-agent launch --name dev1
cws-agent connect dev1 --dangerously-skip-permissions
cws-agent run dev1 'fix the tests' --yolo
cws-agent session start dev1 fix-tests --yolo
cws-agent restore dev1 --connect --yolo
cws-agent connect dev1 --permission-mode native
```

`--yolo`, `--dangerously-skip-permissions`, and
`--dangerously-bypass-approvals-and-sandbox` are aliases understood across all
five CLI harnesses. They are mutually exclusive with `--permission-mode`.
`--permission-mode native` omits permission flags and uses the agent's own
configuration. Policies apply to this invocation: `launch --detach` does not
start an agent, and a later `connect` takes its own flags. A tmux agent keeps the
policy selected at `session start`. Authentication and custom `connect --cmd`
commands retain their own behavior. Devin Outpost permission policies are
controlled by Devin Cloud, so CLI overrides are rejected there.
Claude Managed Agents (`--claude-env`, harness `ant`) likewise uses its cloud
agent's policy; these CLI permission modes do not configure the worker backend.

OpenCode rejects unanswered headless approval requests. Cursor headless runs trust
the selected sandbox workspace but do not automatically approve MCP servers.
See the [OpenCode](opencode.md) and [Cursor](cursor.md) integration details.

Sources: [Claude modes](https://code.claude.com/docs/en/permission-modes),
[Devin modes](https://docs.devin.ai/cli/essential-commands#modes),
[Codex security](https://learn.chatgpt.com/docs/security), and
[Codex CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

Validation: `python3 -m unittest discover -s tests -p 'test_permissions.py'`.
These tests inspect actual CLI dispatch and generated remote commands without
creating a sandbox or calling a model. Live agent behavior needs a sandbox with
the corresponding agent and credentials.
