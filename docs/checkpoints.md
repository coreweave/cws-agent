# Recoverable checkpoint and stop

[Back to everyday usage](usage.md#snapshots).

`down --checkpoint-dir` is an opt-in integration for applications that control
every writer to a sandbox. It records a snapshot request, waits for its READY
receipt, commits a local manifest, and then stops the source. Retrying the same
directory recovers that operation instead of creating a different checkpoint.
It uses filesystem snapshots; processes, memory, sockets, and in-flight model
requests are not restored.

Launch with `--managed-headless` to use the built-in writer gate for CLI commands
and uploads. Other sessions require an external **writer gate** supplied by your
application. A no-op hook cannot make a live workspace consistent.

## Managed headless sessions

Use a digest-pinned Linux image with Python 3.9+ and its `sqlite3` module already
installed. The sandbox must support `/proc`, child subreapers, and `syncfs`.
The CLI installs the selected agent during bootstrap; an agent image is not
required. Export the agent's credentials before launch, since interactive login
is unavailable in this mode.

```bash
cws-agent launch project1 --managed-headless \
  --image REGISTRY/IMAGE@sha256:DIGEST --local-dir .
cws-agent run project1 'Fix the failing test and run the test suite'
cws-agent sync project1 .
cws-agent down project1 --checkpoint-dir ./project1-checkpoint
cws-agent restore project1 --checkpoint-dir ./project1-checkpoint --no-config-sync
```

`--managed-headless` implies `--detach` on launch. It supervises bootstrap,
headless `run`, `exec`, uploads, and config imports. Automatic upload snapshots
are skipped; save and stop with `down --checkpoint-dir`. Restore from its
manifest automatically preserves managed mode and initializes fresh admission
state on the new sandbox, including when it runs on another host.

Admission state lives outside `/workspace` in the source sandbox. Separate CLI
processes and machines using this version share it. Checkpointing closes
admission first, waits for admitted commands and all their descendants to exit,
then flushes the workspace filesystem. A detached child keeps its command
pending even after the parent exits. Commands are allowed to finish normally;
checkpointing does not interrupt an agent turn or capture its RAM.

If a command supervisor disappears, its durable receipt remains unconfirmed and
checkpointing is refused. Timeouts keep admission closed. Inspect the gate with
`cws-agent status project1`, retry the same checkpoint directory, or explicitly
abandon an uncommitted checkpoint:

```bash
cws-agent down project1 --checkpoint-dir ./project1-checkpoint --abort-checkpoint
```

Aborting reopens admission but does not erase unconfirmed commands. There is no
force-clear option: inspect or discard such a source with `down --no-snapshot`.
The gate retains at most 4096 command receipts and 4096 checkpoint-operation
receipts per source, then refuses new admissions. Checkpoint and restore into a
fresh sandbox before reaching that limit. Managed commands are not automatically
replayed after transport errors; inspect their output and workspace before retrying.

`connect`, `login`, tmux worktree sessions, interactive native resume, Remote
Control, messaging bridges, worker backends, and ordinary snapshots are refused.
Use a native headless command to continue a saved conversation:

```bash
cws-agent session history project1
cws-agent exec project1 'claude -p --resume NATIVE_SESSION_ID "Continue the task and run tests"'
```

This gate is cooperative, not a security boundary. Use it only when all workload
commands go through this CLI version. Older clients, direct SDK/API execs, tools
that launch work through another service, and processes modifying the gate can
bypass it. The image must not start independent workspace writers. It does not
provide isolation between simultaneous admitted commands or automatically resume
an interrupted model request.

## Before starting

- Launch a single-container CLI session with an OCI image pinned by digest:
  `--image REGISTRY/IMAGE@sha256:DIGEST`. Keep the image available for restore.
  Managed worker backends and additional volumes are outside this integration.
- Use snapshot helpers that support native filesystem permissions and symlinks.
  This path deliberately skips the ordinary snapshot compatibility workaround:
  it must not rewrite the workspace while a snapshot's outcome is unknown.
- Give the source enough remaining lifetime to finish or recover the operation.
  This command does not extend its lifetime or protect against independent
  expiry, eviction, administrative stop, or deletion.
- Keep the checkpoint directory on durable local storage supporting `flock`,
  atomic rename, and file/directory `fsync`. Its parent must already exist.
  Directories are created with mode 0700; files use 0600. Do not run concurrent
  coordinators against copies of the same directory or rely on network locks.

## Hook contract

For ordinary sessions, route every writer through your application's gate,
including terminals, uploads, bridges, background services, and SDK calls. The
CLI does not add coordination to those sessions. Managed headless sessions use
their built-in gate and reject an external hook override.

Supply an executable file, without shell arguments. The CLI invokes it as:

```text
EXECUTABLE quiesce SOURCE_SANDBOX_ID OPERATION_ID CHECKPOINT_DIRECTORY
EXECUTABLE release SOURCE_SANDBOX_ID OPERATION_ID CHECKPOINT_DIRECTORY
```

`quiesce` must durably claim admission for this source and operation, reject
competing owners, stop admitting new writers, drain active turns and background
writers, and flush application state to `/workspace`. Return zero only when the
workspace is stable. Ownership must survive the hook or coordinator exiting,
including a timeout. Repeating the same operation must be idempotent.

`release` must release only that operation's ownership. It must be idempotent,
including when a quiesce acknowledgement was lost, and prevent a delayed quiesce
for that abandoned operation from reacquiring ownership. The CLI calls it only after
the source is confirmed terminal, or after durably abandoning an uncommitted
checkpoint. A timeout is not permission for the hook to reopen admission.

The hook receives no input and its output is suppressed, since it may contain
credentials. Keep diagnostic logs in your application's private storage.
Use the same executable path when retrying an operation. The hook is application
code that you supply; the built-in gate is limited to managed headless sessions.

## Suspend and recover

For an existing session launched with a pinned image, use a fresh directory for
each checkpoint generation. The example assumes your gate is installed at
`/opt/example/writer-gate` and the parent directory exists:

```bash
cws-agent down project1 --checkpoint-dir ./project1-checkpoint \
  --writer-gate /opt/example/writer-gate --checkpoint-timeout 180
```

The attempt deadline defaults to 180 seconds and accepts 1–600 seconds. If a
command fails or is interrupted, keep the directory and retry **the same command**.
The journal stores the source identity and stable request ID before remote work.
An ambiguous Create response is retried with that request ID. A failed Stop is
retried against the same source after revalidating the committed receipt.
The CLI never treats a timeout or authentication failure as proof of deletion.

Before commit, failures leave the source unstopped by this command and the gate
held. To abandon that generation and reopen writers:

```bash
cws-agent down project1 --checkpoint-dir ./project1-checkpoint \
  --writer-gate /opt/example/writer-gate --abort-checkpoint
```

Abandonment is persisted before release. A snapshot might still finish afterward;
it is excluded from ordinary restore selection. An abandoned directory cannot
be reused for another generation. After manifest commit, abort is refused;
retry the original command to finish stopping the source.

`journal.json` records the operation's progress. `manifest.json` records the exact
snapshot, source volume, session harness, disk size, CPU/memory requests, and OCI
image digest. Neither copies environment credentials. Preserve both files;
do not edit them to bypass an incomplete transition. Reuse the same API route,
authentication mode, and account on recovery. Account identity is enforced by
service access checks, not stored in the local journal.

## Restore

```bash
cws-agent restore project1 --checkpoint-dir ./project1-checkpoint
cws-agent session history project1
```

Restore requires a completed suspend and uses exactly the committed READY
snapshot and image digest, even if newer snapshots exist. It restores disk and
CPU/memory defaults from the checkpoint; normal `--disk`, `--cpu`, and `--memory`
overrides still apply. An image override must match the recorded digest.
Re-export environment-only credentials and pass any required environment
variables through the usual flags. Keep storage private: the filesystem snapshot
itself may contain saved logins and project secrets.

The restored sandbox can run on another host. `/workspace`, including native
agent conversation files, comes from shared snapshot storage. Agent binaries
outside `/workspace` are provisioned again. The image is pinned, but bootstrap
downloads and dependencies are not automatically pinned by this feature; use
your deployment's version controls for reproducible toolchains. Resume a saved
conversation using its native ID; this does not resume the old process or retry
unfinished tool calls automatically.

Use headless native resume through `exec` for managed sessions, as shown above.
For sessions coordinated by an external hook, interactive native resume remains
available through `cws-agent session resume project1 NATIVE_SESSION_ID --agent claude`.

Restore allocation uses the existing restore command's behavior. It is not a
durable or idempotent allocation transaction. If it is interrupted, inspect
`cws-agent list` before retrying.

## Retention

Checkpoint requests use a separate `cwcp1|` namespace. Ordinary restore skips
that namespace, and ordinary pruning retains it, including abandoned generations.
Inspect and resolve in-flight checkpoints before explicitly including them:

```bash
cws-agent snapshots project1
cws-agent prune project1 --keep 3 --include-checkpoints
```

This override can delete snapshots referenced by committed manifests and make
them unrestorable. A local manifest cannot protect snapshots from service-side
retention or deletion through other tools.
