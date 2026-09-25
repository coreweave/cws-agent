#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["cwsandbox[wandb]>=1.14.2,<2", "rich>=14,<15"]
# ///
# SPDX-FileCopyrightText: 2026 CoreWeave, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Live, billable Cloud runner test; stops its sandbox even when validation fails.

Requires sandbox authentication, SELF_HOSTED_RUNNER_ENVIRONMENT_SECRET, and a
local Claude login with access to --environment. Run from this repository:
    uv run smoke_claude_cloud.py --environment ccpool_... --ref main
"""
import argparse
import secrets
import tempfile
import time
from pathlib import Path

from smoke_harnesses import load_cli


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', required=True)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--ref', default='main')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    cli = load_cli()
    name = 'cloud-smoke-' + secrets.token_hex(4)
    marker = secrets.token_hex(16)
    proof = '/workspace/cloud-smoke-proof.txt'
    # The prompt never contains the expected output. Claude must run the custom
    # command inside this sandbox to discover it and write the proof file.
    goal = ("Run the installed command /usr/local/bin/cws-cloud-proof using Bash. "
            f"Write its exact stdout to {proof}. Read the file back and report success. "
            "Do not change the repository, push commits, or access credentials.")
    with tempfile.TemporaryDirectory(prefix='cws-cloud-smoke-') as temporary:
        setup = Path(temporary) / 'setup.sh'
        setup.write_text("#!/bin/bash\nset -eu\ncat > /usr/local/bin/cws-cloud-proof <<'SH'\n"
                         f"#!/bin/sh\nprintf '%s\\n' '{marker}'\nSH\n"
                         "chmod 755 /usr/local/bin/cws-cloud-proof\n")
        try:
            cli.main(['cloud', 'start', name, '--environment', args.environment,
                      '--setup', str(setup), '--lifetime', '30m'])
            cli.main(['cloud', 'run', name, goal, '--repo', args.repo, '--ref', args.ref])
            sandbox = cli.require_active(name)
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                result = cli.exec_retry(sandbox, ['cat', proof], timeout_seconds=15, attempts=1)
                if result.returncode == 0 and (result.stdout or '').strip() == marker:
                    print('PASS: Claude executed the custom tool and wrote its output in the sandbox')
                    return 0
                time.sleep(3)
            raise SystemExit('FAIL: no matching sandbox proof before timeout; inspect the cloud session for permissions or errors')
        finally:
            sandbox = cli.find_active(name)
            if sandbox:
                sandbox.stop(missing_ok=True).result()
                print(f'Stopped smoke sandbox {name}')


if __name__ == '__main__':
    raise SystemExit(main())
