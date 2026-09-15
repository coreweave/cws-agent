# Terminal and clipboard

[Back to README](../README.md). These features need an interactive terminal attachment, not a Managed Agents API client.

## Paste clipboard images into remote Claude Code

Copy a screenshot, attach to Claude Code, and press **Ctrl+V**. `cws-agent` reads
the local clipboard at that keypress, uploads the PNG, and pastes its remote
path into the prompt. Add your question and submit it; Claude supports
[images supplied by file path](https://code.claude.com/docs/en/common-workflows#work-with-images).
The image stays under `/workspace/home/.cws-agent/images/` for later turns and
filesystem snapshots. Each image is limited to 10 MiB; delete old images when
their conversations no longer need them.

macOS uses the built-in AppKit clipboard API (PNG and TIFF screenshots). Linux
requires `wl-paste` on Wayland or `xclip` on X11 with a PNG clipboard selection.
No clipboard is read until Ctrl+V is pressed; ordinary pasted text and other
keystrokes pass through. When there is no image or clipboard utility, Ctrl+V
passes through to the remote program. Upload failures show a message and also
preserve the keypress.

This is enabled for Claude launches, attaches, and Claude tmux sessions.
Custom `connect --cmd` shells and other agents keep their normal Ctrl+V behavior.
To disable image handling, set `CWS_AGENT_IMAGE_PASTE=0`. If Ghostty maps Ctrl+V
to its own paste action, restore a binding that sends Ctrl+V to the application.
This bridge supplies a file path; the agent version controls whether it shows
an inline image preview. Remote-control browser sessions are outside this path.

## Copy from an agent to your local clipboard

During an interactive attach, ask the agent to **run `cws-copy` to copy text to
my clipboard**. It reads UTF-8 text from stdin, for example:

```sh
printf %s 'the text to copy' | cws-copy
cws-copy < result.txt
```

`pbcopy` is also installed as a compatibility command. The helper writes to the
attached PTY even when the agent captures command stdout. It uses the terminal's
[OSC 52 clipboard support](https://ghostty.org/docs/vt/osc/52); Ghostty must allow
clipboard writes. In tmux it uses the native clipboard command, scoped to the
agent's session, which must have exactly one attached client. See the
[tmux clipboard requirements](https://github.com/tmux/tmux/wiki/Clipboard) if
copying is disabled by your tmux settings or an outer local tmux session.

Copying is limited to 100 KB of text. The terminal can impose a smaller limit
or deny a write, and OSC 52 provides no delivery acknowledgement. Headless
commands and detached sessions have no local clipboard target. This helper
does not read your local clipboard.

## Troubleshooting

Attach forwards terminal output as bytes, reads the actual PTY size (including
resizes), and supplies the local terminal type to the remote agent. A direct
remote PTY resize supplements the SDK resize call. If the image
does not have that terminfo entry (common with Ghostty's `xterm-ghostty`), it uses
`xterm-256color`. Exported `COLUMNS`/`LINES` do not override live window geometry.
This addresses stale dimensions and missing terminal capabilities that disrupt
cursor movement and redraws; it does not guarantee that every agent's renderer
is artifact-free. Compound `connect --cmd` shell commands are supported, and
input forwarding and local terminal state are cleaned up on exit or transport
failure. Existing tmux panes may need their agent restarted
to pick up changed terminal settings.

For a manual check, attach from Ghostty, enter a prompt long enough to wrap,
move through input history, resize narrower and wider, and scroll. Confirm the
cursor and cleared lines remain correct, then detach and check the local shell
still accepts input. Offline regressions: `uv run --with 'cwsandbox>=1.1'
python -m unittest discover -s tests` (no sandbox required).
