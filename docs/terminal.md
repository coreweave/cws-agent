# Terminal and clipboard

[Back to README](../README.md). These features work in interactive CLI sessions.

## Paste clipboard images into remote Claude Code

Copy a screenshot and press **Ctrl+V** inside Claude. The image uploads to the
sandbox and its path appears in the prompt; add your question and submit.
[Claude image support](https://code.claude.com/docs/en/common-workflows#work-with-images).

Images are limited to **10 MiB** and remain in `/workspace/home/.cws-agent/images/`,
including in snapshots. Delete them when no longer needed.

- **macOS:** PNG and TIFF screenshots work without extra tools.
- **Linux:** install `wl-paste` for Wayland or `xclip` for X11; copy a PNG image.
- **Disable:** set `CWS_AGENT_IMAGE_PASTE=0`.

The clipboard is read only on Ctrl+V. Text paste, other agents, and custom shell
commands retain their normal behavior. If no image is available, the key passes
through. If your terminal intercepts Ctrl+V, change its binding to send the key.

## Copy from an agent to your local clipboard

Ask the agent to run `cws-copy`, or run this inside the attached sandbox:

```sh
printf %s 'the text to copy' | cws-copy
cws-copy < result.txt
```

The limit is **100 KB** of UTF-8 text. Your terminal must permit
[OSC 52 clipboard writes](https://ghostty.org/docs/vt/osc/52) and may impose a
smaller limit. `pbcopy` is also available as an alias.

In tmux, exactly one client must be attached. Check the
[tmux clipboard settings](https://github.com/tmux/tmux/wiki/Clipboard) if copying
fails. Detached and headless agents cannot copy to a local clipboard.

## Troubleshooting

For misplaced cursors or broken redraws, reconnect and resize the terminal.
Restart existing tmux agents to pick up changed terminal settings.
The CLI uses the window's actual size and falls back to `xterm-256color` when
remote terminfo is missing. Image previews depend on the agent version;
the upload supplies a file path.
