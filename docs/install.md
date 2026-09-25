# Installation

For macOS, Linux, and WSL.

## Install script

```bash
curl -fsSL https://raw.githubusercontent.com/coreweave/cws-agent/main/install.sh | sh
```

Open a new terminal, or run `export PATH="$HOME/.local/bin:$PATH"` in the current one.
The script installs `cws-agent` and [uv](https://docs.astral.sh/uv/getting-started/installation/),
then configures future zsh and bash sessions. It installs the Python source
`cws-agent.py` as the command `cws-agent`. Run the install script again to update. Python and
dependencies download on first use.

## Manual installation

Requires Git and `uv`.

```bash
git clone https://github.com/coreweave/cws-agent.git
cd cws-agent
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/cws-agent.py" "$HOME/.local/bin/cws-agent"
export PATH="$HOME/.local/bin:$PATH"
cws-agent --help
```

Add the `export PATH=...` line to `~/.zshrc` (or `~/.bashrc`) for future sessions.
Keep the checkout: the installed command links to it. Run `git pull` inside it to update.

If `~/.local/bin/cws-agent` already exists, inspect it before replacing it.
For a custom zsh configuration directory, use `$ZDOTDIR/.zshrc` instead.

Repoint existing checkout symlinks that target `cws-agent` to `cws-agent.py`.

## Test a local checkout

Configure [sandbox credentials](usage.md#authentication) before opening a sandbox.
From the checkout you want to test, run:

```bash
uv run --script cws-agent.py --help
uv run --script cws-agent.py shell test-shell
```

This runs that checkout without changing your installed `cws-agent` command.
`uv run --script` reads the Python dependencies declared in `cws-agent.py` and
provides them for the command. The installed `cws-agent` handles this automatically,
so normal use only needs `cws-agent shell test-shell`.

After exiting the remote shell, stop the test sandbox from the same checkout:

```bash
uv run --script cws-agent.py down test-shell --no-snapshot
```
