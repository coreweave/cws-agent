# Recoverable checkpoint and stop

[Back to everyday usage](usage.md#snapshots).

`down --checkpoint-dir` is an opt-in integration for applications that control
every writer to a sandbox. It records a snapshot request, waits for its READY
receipt, commits a local manifest, and then stops the source. Retrying the same
directory recovers that operation instead of creating a different checkpoint.
It uses filesystem snapshots; processes, memory, sockets, and in-flight model
requests are not restored.

This mode requires an external **writer gate**. The CLI does not yet enforce that
gate across `connect`, `exec`, uploads, parallel sessions, messaging bridges,
background services, or direct SDK calls. Only use checkpoint mode when your
application routes all of these writers through the gate. A no-op hook cannot
make a live workspace consistent.

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
code that you supply; this repository does not ship a universal writer gate.

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
cws-agent session resume project1 NATIVE_SESSION_ID --agent claude
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
