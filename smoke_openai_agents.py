#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["cwsandbox[wandb]>=1.10,<2", "openai>=3.14,<4", "python-dotenv>=1,<2"]
# ///
"""Billable end-to-end Agents API test; creates and cleans up its own resources."""
import argparse
import json
import os
import secrets
import sys

from smoke_harnesses import load_cli


def verify_file(cli, name, expected):
    sb = cli.require_active(name)
    result = sb.exec(["cat", cli.PROJECT_DIR + "/cws-smoke.txt"], timeout_seconds=30).result()
    if result.returncode != 0 or result.stdout.strip() != expected:
        raise RuntimeError("independent CWSandbox file verification failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-6-astra")
    args = parser.parse_args(argv)
    missing = [key for key in ("OPENAI_API_KEY", "OPENAI_EXECUTOR_API_KEY")
               if not os.environ.get(key)]
    if missing:
        raise SystemExit("Missing credentials: " + ", ".join(missing))
    cli = load_cli()
    name = "openai-smoke-" + secrets.token_hex(4)
    nonce = secrets.token_hex(12)
    session_id = None
    report = {"name": name, "passed": False, "checks": []}
    try:
        if cli.main(["launch", "--name", name, "--agent", "openai",
                     "--openai-model", args.model, "--lifetime", "15m", "--disk", "1Gi"]) != 0:
            raise RuntimeError("launch failed")
        state = cli.read_backend_config(cli.require_active(name))
        session_id = state["target"]
        report["api_session_id"] = session_id
        report["checks"].append("executor connected")
        prompt = (f"Use your shell tool to write exactly {nonce} to cws-smoke.txt in "
                  f"{cli.PROJECT_DIR}. Read the file to verify it. Do not just describe commands.")
        if cli.main(["run", name, prompt, "--timeout", "180"]) != 0:
            raise RuntimeError("first model turn failed")
        verify_file(cli, name, nonce)
        report["checks"].append("model tool wrote file; independently read through CWSandbox")
        followup = ("Read cws-smoke.txt and replace its contents with the previous value followed "
                    "by -continued, with no whitespace between them. Use the shell tool.")
        if cli.main(["run", name, followup, "--timeout", "180"]) != 0:
            raise RuntimeError("follow-up failed")
        verify_file(cli, name, nonce + "-continued")
        report["checks"].append("follow-up changed the file in the same API session")
        if cli.main(["down", name]) != 0:
            raise RuntimeError("snapshot and stop failed")
        if cli.main(["restore", name, "--lifetime", "15m"]) != 0:
            raise RuntimeError("restore failed")
        restored = cli.read_backend_config(cli.require_active(name))
        if restored["target"] != session_id:
            raise RuntimeError("restore changed the API session")
        verify_file(cli, name, nonce + "-continued")
        report["checks"].append("snapshot restored file and reused API session")
        final = ("Use the shell tool to replace cws-smoke.txt with only the original value I "
                 "provided in my first message, followed by -restored. This checks your conversation memory.")
        if cli.main(["run", name, final, "--timeout", "180"]) != 0:
            raise RuntimeError("post-restore turn failed")
        verify_file(cli, name, nonce + "-restored")
        report["checks"].append("post-restore tool execution and conversation memory")
        report["passed"] = True
    finally:
        cleanup_errors = []
        try:
            for sb in cli.Sandbox.list(tags=[cli.SESSION_TAG, cli.name_tag(name)], auth=cli.sandbox_auth()).result(timeout=30):
                sb.stop(missing_ok=True).result(timeout=60)
        except Exception as error:
            cleanup_errors.append("sandbox: " + type(error).__name__)
        try:
            for snap in cli.session_snapshots(name):
                cli.Sandbox.delete_snapshot(snap.file_system_snapshot_id, missing_ok=True, auth=cli.sandbox_auth()).result(timeout=60)
        except Exception as error:
            cleanup_errors.append("snapshot: " + type(error).__name__)
        if session_id:
            try:
                with cli.openai_client() as client:
                    client.beta.agents.sessions.delete(session_id)
            except Exception as error:
                cleanup_errors.append("API session: " + type(error).__name__)
        report["cleanup_errors"] = cleanup_errors
        report["passed"] = report["passed"] and not cleanup_errors
        print(json.dumps(report, indent=2), flush=True)
        if cleanup_errors:
            print(f"Cleanup incomplete for {name}; inspect the report's resource IDs.", file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
