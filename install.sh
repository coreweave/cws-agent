#!/bin/sh
# Install the standalone command; an optional path uses a local checkout.

main() {
    set -eu

    if [ "$#" -gt 1 ]; then
        echo "Usage: sh install.sh [path/to/cws-agent.py]" >&2
        exit 1
    fi

    bin_dir="$HOME/.local/bin"
    mkdir -p "$bin_dir"
    if [ -d "$bin_dir/cws-agent" ]; then
        echo "$bin_dir/cws-agent is a directory; move it before installing." >&2
        exit 1
    fi
    staging=$(mktemp -d "$bin_dir/.cws-agent.XXXXXX")
    trap 'rm -rf "$staging"' EXIT
    trap 'exit 1' HUP INT TERM

    if [ "$#" -eq 1 ]; then
        cp "$1" "$staging/cws-agent"
    else
        curl -fsSL https://raw.githubusercontent.com/coreweave/cws-agent/main/cws-agent.py \
            -o "$staging/cws-agent"
    fi
    chmod 755 "$staging/cws-agent"

    export PATH="$bin_dir:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
        curl -fsSL https://astral.sh/uv/install.sh -o "$staging/uv-install.sh"
        UV_INSTALL_DIR="$bin_dir" UV_NO_MODIFY_PATH=1 sh "$staging/uv-install.sh" </dev/null
    fi
    command -v uv >/dev/null 2>&1 || { echo "uv installation failed." >&2; exit 1; }

    # Replace a previous installation without modifying a symlink's target.
    mv -f "$staging/cws-agent" "$bin_dir/cws-agent"

    # Expand these variables when the user's shell starts.
    # shellcheck disable=SC2016
    path_line='export PATH="$HOME/.local/bin:$PATH"'
    add_path() {
        if ! grep -qxF "$path_line" "$1" 2>/dev/null; then
            printf '\n%s\n' "$path_line" >> "$1"
        fi
    }

    mkdir -p "${ZDOTDIR:-$HOME}"
    add_path "${ZDOTDIR:-$HOME}/.zshrc"
    add_path "$HOME/.bashrc"
    # Bash login shells read only the first existing file in this list.
    bash_profile="$HOME/.bash_profile"
    for profile in "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile"; do
        if [ -f "$profile" ]; then
            bash_profile="$profile"
            break
        fi
    done
    add_path "$bash_profile"

    printf 'Installed %s/cws-agent. Open a new terminal, or run:\n' "$bin_dir"
    printf '  %s\n' "$path_line"
}

main "$@"
