# Installation

For macOS, Linux, and WSL.

## Install script

```bash
curl -fsSL https://raw.githubusercontent.com/coreweave/cws-agent/main/install.sh | sh
```

Open a new terminal, or run `export PATH="$HOME/.local/bin:$PATH"` in the current one.
The script installs `cws-agent` and [uv](https://docs.astral.sh/uv/getting-started/installation/),
then configures future zsh/bash sessions. Run it again to update. Python and
dependencies download on first use.

## Manual installation

Requires Git and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/coreweave/cws-agent.git
cd cws-agent
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/cws-agent" "$HOME/.local/bin/cws-agent"
export PATH="$HOME/.local/bin:$PATH"
cws-agent --help
```

Add the `export PATH=...` line to `~/.zshrc` (or `~/.bashrc`) for future sessions.
Keep the checkout: the installed command links to it. Run `git pull` inside it to update.

If `~/.local/bin/cws-agent` already exists, inspect it before replacing it.
For a custom zsh configuration directory, use `$ZDOTDIR/.zshrc` instead.
