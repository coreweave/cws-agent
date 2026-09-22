# Security

## Reporting a vulnerability

Report suspected vulnerabilities in cws-agent privately through GitHub's
private vulnerability reporting on this repository ("Security" → "Report a
vulnerability"). Please don't open a public issue or pull request for a
security problem.

## Scope

cws-agent is a client-side CLI. It runs on your machine with your CoreWeave,
W&B, and agent-provider credentials, and it creates sandboxes billed to your
account. Reports about the CLI's handling of those credentials, of uploaded
project files, or of workspace snapshots are in scope.

The CoreWeave Sandboxes service itself, the agent harnesses it installs (Claude
Code, Codex, Devin CLI, OpenCode, Cursor CLI), and the model providers are
separate products with their own reporting channels.

## Known operating constraints

- `--import-codex-auth` explicitly copies a local ChatGPT login into the sandbox.
  Sandbox processes can read those tokens, and workspace snapshots retain them.
  The import does not export OS-keyring credentials or change local login files.
- `launch --local-dir` and `sync` upload the directory as-is and do not honor
  `.gitignore`. Exclude secret files before uploading.
- Snapshot preparation temporarily widens file permissions inside the sandbox
  while the archive is captured. See the snapshot caveat in
  [docs/usage.md](docs/usage.md#snapshots).
