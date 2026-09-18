# Contributing to cws-agent

cws-agent is a single Python source file (`cws-agent.py`) run by `uv`, with offline
tests in `tests/` and optional live smoke scripts. Clone the repository, then run
the offline suite from the checkout:

```bash
uv run --no-project --with 'cwsandbox[wandb]>=1.14.2,<2' --with 'segno>=1.6,<2' --with 'truststore>=0.10,<1' --with 'markdown-it-py>=3,<5' --with 'python-dotenv>=1,<2' --with 'openai>=3.14,<4' --with 'discord.py>=2.6,<3' python -m unittest discover -s tests
```

## Checklist

Every change must:

- Keep the offline suite green without cloud calls. Tests that need a real
  sandbox belong in the `smoke*` scripts and must say what they bill.
- Keep documented commands valid. `tests/test_readme_commands.py` parses every
  `cws-agent` example in `README.md` and `docs/` against the real argument
  parser and checks that local links resolve.
- Update `docs/` when behavior or flags change. The README is the quickstart;
  detail goes in the topic guide.
- Use only synthetic values in fixtures (`example.test`, `example.invalid`).
  Never commit real credentials, sandbox IDs, or personal paths.
- Pin new dependencies in the script's inline metadata block with an upper bound.

## Style

Plain, direct English. Short sentences. No filler. Comments only where the why
is not obvious from the code. Run the tests before opening a PR.

Do not commit `.env`, logs, or local planning documents.

## Contributor License Agreement

Contributors must agree to the [CoreWeave CLA](./CLA.md) when pushing code to this project.

Agreement with the CoreWeave CLA must be signified by including a `Signed-off-by`
trailer in every submitted Git commit to this repository. By signing off, you certify that you have the right to submit the contribution and that you agree to and are bound by the CoreWeave Contributor License Agreement in effect at the date of your submission, found in [`CLA.md`](./CLA.md) in the root of this repository, which governs your submission. If you are contributing on behalf of an entity, you further certify that you are authorized to bind that entity to the CLA.

Sign each commit with the `--signoff` (`-s`) option to [`git commit`](https://git-scm.com/docs/git-commit#Documentation/git-commit.txt---signoff). Git has no configuration option that adds the trailer automatically; if you want it on every commit, use an alias such as `git config alias.ci "commit -s"` or a `prepare-commit-msg` hook.

## Licensing

This project is licensed under Apache-2.0 (see [`LICENSE`](./LICENSE)) and follows the [REUSE](https://reuse.software/) specification. REUSE requires the license text in [`LICENSES/Apache-2.0.txt`](./LICENSES/Apache-2.0.txt). Licensing metadata lives in [`REUSE.toml`](./REUSE.toml): its aggregate annotation covers every file by default, so new files need no SPDX header. If you add material under a different license or copyright, declare it with an inline SPDX header or a `REUSE.toml` annotation and include any additional license text in `LICENSES/<SPDX-License-Identifier>.txt`. Run `reuse lint` from the repository root before opening a PR.
