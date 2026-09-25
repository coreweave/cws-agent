# Open a sandbox shell

Exiting a shell leaves the sandbox running and consuming compute until you stop
it or its maximum lifetime expires. This also applies to `--cmd`.

`cws-agent shell NAME` creates a sandbox or opens a new shell in a running one.
It starts Bash, or `sh` if Bash isn't available. The command doesn't install or
start a coding agent.

Before starting, [install cws-agent](install.md) and configure
[sandbox credentials](usage.md#authentication). No agent login is needed.

## Open and reconnect

In your local terminal, open a sandbox shell:

```bash
cws-agent shell dev1
```

The prompt opens in `/workspace/project`, with `HOME` set to `/workspace/home`.
The connection uses a pseudo-terminal (PTY) automatically, with terminal resizing
and Ctrl-C handling.

Type `exit` to return to your local terminal. The sandbox keeps running.
Run `cws-agent shell dev1` again to open a new shell in the same sandbox.
To stop it without saving a new snapshot, run locally:

```bash
cws-agent down dev1 --no-snapshot
```

New sandboxes have an 8-hour maximum lifetime. Reconnecting doesn't extend it or
resume the previous shell process. Omit `NAME` to generate a new name, which the
CLI prints. Names identify `cws-agent` sessions, including ones created by `launch`.

To debug a running sandbox created by `cws-agent launch`, list your sessions, then
open a shell using its existing name. Replace `NAME` with that session name:

```bash
cws-agent list
cws-agent shell NAME
```

## Use a GPU

GPU access must be [enabled for your organization](https://docs.coreweave.com/products/sandboxes/gpu-sandboxes).
Create a sandbox with one GPU:

```bash
cws-agent shell gpu1 --gpu any:1 --cpu 4 --memory 8Gi
```

At the remote prompt, run `nvidia-smi` to inspect the GPU. The platform supplies
the driver and this tool. For CUDA programs, choose an image with the
[compatible runtime and libraries](https://docs.coreweave.com/products/sandboxes/gpu-sandboxes#container-images).

Use `any[:COUNT]`. The platform selects the host. Allocation depends on access,
limits, and capacity. CPU and memory don't increase automatically with GPU count.

## Run a command

Use `--cmd` or `-c` instead of opening the default shell:

```bash
cws-agent shell dev1 --cmd 'python --version'
cws-agent shell gpu1 -c 'nvidia-smi'
cws-agent shell dev1 --cmd 'python -m pip list' > packages.txt
```

Quote shell expressions so your local shell doesn't expand them. For output
redirection and non-interactive use, see [automation and limitations](#automation-and-limitations).

## Choose creation options

Use these options with a new name. An existing sandbox accepts `--cmd`, but
rejects creation options. Use its name without those options to reconnect.

| Creation flag | Value and default |
| --- | --- |
| `--mode` | `serverless` or `cks`. Default: serverless, or CKS when `--volume` is present. |
| `--image` | Container image. Default: `python:3.11`. |
| `--cpu` | Cores or millicores, such as `2`, `0.5`, or `500m`. Default: `2`. |
| `--memory` | MiB as a number (`4096`) or a quantity (`4Gi`). Default: `4Gi`. |
| `--gpu` | `any` requests one GPU. `any:1` through `any:8` set the count. Default: no GPU. |
| `--add-local` | File or directory to copy to `/mnt/BASENAME`. Repeatable. |
| `--secret` | Existing W&B secret name. Repeatable. W&B serverless only. |
| `--snapshot` | Snapshot ID, exact `request_id`, or session name. Restores `/workspace`. |
| `--volume` | Registered volume `ID` or `ID:/mnt/PATH`. Repeatable. Selects CKS. |

CPU and memory requests equal limits. Images must provide `sh`, `sleep`, and basic
file utilities, and permit writes to `/workspace` and `/mnt`. No packages are
installed automatically. Use `cws-agent shell --help` (or `-h`) for CLI help.

To choose where a new sandbox runs, set `--mode`:

```bash
cws-agent shell cloud1 --mode serverless
cws-agent shell cluster1 --mode cks
```

CKS placement requires a CoreWeave API access token and an available runner on
your CoreWeave Kubernetes Service (CKS) cluster. It works without volume mounts.
See [placement setup](https://docs.coreweave.com/products/sandboxes/get-started#choose-a-placement-mode).
The CLI doesn't fall back to another mode if placement fails.

### Copy local files

```bash
cws-agent shell files1 --add-local ./src --add-local ./requirements.txt
```

These become `/mnt/src` and `/mnt/requirements.txt`. This is a one-time copy.
Remote edits don't change local files. Hidden files are included and `.gitignore`
isn't applied.

Regular files and directories, including empty ones, are supported.
Symbolic links and special files are rejected. Permissions are copied without
set-user-ID or set-group-ID bits.

Destinations must be unique, must not overlap each other or volume mounts, and
must not already exist in the image. `/mnt` copies aren't in workspace snapshots.
Move files you want to snapshot into `/workspace`.

### Inject W&B secrets

```bash
cws-agent shell secrets1 --secret HF_TOKEN --secret SERVICE_TOKEN
```

Each [W&B secret](https://docs.coreweave.com/products/sandboxes/secrets) becomes an
environment variable with the same name. The CLI sends references, not secret
values. Names must be valid environment variable names. `CWS_AGENT_` is reserved.
Local agent credentials and configuration aren't copied automatically.

Use W&B authentication and serverless placement. `--secret` can't be combined
with `--mode cks`, `--volume`, or CoreWeave token authentication.
If `CWSANDBOX_API_KEY` is set, unset it to use W&B instead.

### Mount CKS volumes

With a CoreWeave API access token and [registered volumes](https://docs.coreweave.com/products/sandboxes/volumes)
on your CKS deployment, mount the volumes:

```bash
cws-agent shell data1 --volume datasets --volume models:/mnt/models
```

`datasets` mounts at `/mnt/datasets`. Explicit paths must be below `/mnt/`.
Each volume ID must be unique. Mount paths can't overlap. Mounts request write
access. A volume registered as read-only remains read-only.

Without `--mode`, `--volume` selects CKS placement. Combining `--volume` with
`--mode serverless` is rejected. Volumes can be combined with all other creation
options except W&B secrets.

## Save work and stop compute

`down` snapshots `/workspace` and stops the sandbox. In your local terminal, run:

```bash
cws-agent down dev1
cws-agent shell restored1 --snapshot dev1
```

The second command opens a new sandbox from the latest ready snapshot of `dev1`.
Use `cws-agent snapshot dev1` to save without stopping, or `down --no-snapshot`
to stop without requesting a new snapshot.

`--snapshot` also accepts an ID or exact snapshot `request_id`. Lookup order is
ID, request ID, then session name. Ambiguous request IDs and snapshots that aren't
ready are rejected. [Managed checkpoints](checkpoints.md) use their own restore
workflow.

Snapshots contain `/workspace` files, including any credentials saved there.
They exclude the image's root filesystem, `/mnt` copies, and registered volumes.
Repeat any mode, image, GPU, CPU, memory, secret, and volume options you need on restore.

The workspace defaults to 10 GiB. Restore reuses a recorded size when available.
Otherwise, it allocates at least 10 GiB based on archive size. Large compressed
snapshots may need more space than this estimate. Restoring agent snapshots
requires `python3` for workspace metadata. Plain shell snapshots use the
[platform snapshot behavior](https://docs.coreweave.com/products/sandboxes/file-system-snapshots)
without the agent metadata helper.

## Automation and limitations

| Situation | Behavior |
| --- | --- |
| Both stdin and stdout are terminals | Automatic PTY, including with `--cmd`. |
| Either stream isn't a terminal | Requires `--cmd`. Runs without a PTY and closes remote stdin. |
| Non-interactive output | Separate stdout and stderr, returned after completion. Exit status matches the command. Timeout: 5 minutes. |
| Pipe or file input | Not forwarded to the remote command. The CLI warns that input is ignored. `/dev/null` is quiet. |
| Failed setup or local-file upload | The CLI attempts to stop the newly allocated sandbox. |
| Command failure or disconnected terminal | The sandbox remains running. Reconnect or stop it explicitly. |

Names use 1 to 40 lowercase letters, digits, or hyphens and start with a letter or
digit. They aren't platform sandbox IDs or Python function references.
Interactive Windows use requires WSL.

On serverless sandboxes, `tty` can report `not a tty` even during a PTY session.
Programs that require terminal device paths may fail.
