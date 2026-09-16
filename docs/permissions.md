# Agent permission modes

[Back to README](../README.md).

CLI agents use **YOLO mode** by default, bypassing tool approval prompts.
To choose another policy:

```bash
cws-agent connect my-claude --permission-mode accept-edits
cws-agent connect my-claude --permission-mode native
```

| Mode | Behavior |
| --- | --- |
| YOLO (default) | Bypass tool approval prompts; vendor restrictions still apply |
| `accept-edits` | Accept file edits where supported; other actions may need approval |
| `native` | Use the agent's own permission configuration |

With `accept-edits`, Claude and Devin still ask before shell commands. Codex also
allows commands inside its workspace sandbox. Cursor has no edits-only mode, so
it keeps native permissions and prints a notice. OpenCode preserves configured
restrictions. Headless runs cannot answer approval prompts.

`--yolo`, `--dangerously-skip-permissions`, and
`--dangerously-bypass-approvals-and-sandbox` explicitly select the default mode.
They cannot be combined with `--permission-mode`.

Policies apply per invocation. A later `connect` uses its own flags; a running
tmux agent keeps the policy selected when started. Authentication and custom
`connect --cmd` commands keep their own behavior.

Managed Agents, Outposts, and OpenAI API executors use provider-managed policies.
CLI permission flags do not configure those workers.

Agent details: [OpenCode](opencode.md) · [Cursor](cursor.md) ·
[Claude](https://code.claude.com/docs/en/permission-modes) ·
[Devin](https://docs.devin.ai/cli/essential-commands#modes) ·
[Codex](https://learn.chatgpt.com/docs/security).
