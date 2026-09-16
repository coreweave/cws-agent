#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["cwsandbox[wandb]>=1.10,<2", "python-dotenv>=1,<2"]
# ///
"""Probe an installed OpenCode/Cursor CLI in an explicitly named running sandbox.

    uv run smoke_harnesses.py EXISTING_SANDBOX
    uv run smoke_harnesses.py EXISTING_SANDBOX --model-turns

Default: no model requests or workspace edits; capture and redact native output.
--model-turns: two small model requests (possibly billable), creating one saved
conversation and resuming it. Prompts ask for no tools or file access. No sandbox
is ever created, stopped, deleted, restored, or snapshotted by this runner.
History transfer and snapshot/restore need separate disposable-sandbox checks.
"""
import argparse
import importlib.machinery
import importlib.util
import inspect
import json
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import types


def load_cli():
    # Source-generated remote helpers use inspect.getsource. Freeze the file so
    # concurrent local development cannot change its line offsets mid-smoke.
    frozen = tempfile.TemporaryDirectory(prefix="cws-harness-smoke-cli-")
    source = Path(frozen.name) / "cws-agent.py"
    shutil.copy2(Path(__file__).resolve().with_name("cws-agent.py"), source)
    loader = importlib.machinery.SourceFileLoader(
        "cws_agent_harness_smoke", str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    module._smoke_source = frozen
    return module


def native_probe(agent):
    """Runs remotely; deliberately return booleans/counts, never captured output."""
    import json
    import os
    import subprocess
    import tempfile

    binary = {"opencode": "/opt/agent/bin/opencode",
              "cursor": "/opt/agent/.local/bin/cursor-agent"}[agent]
    results = {}

    def capture(args):
        # Native status/config output may contain credentials. Use unlinked,
        # owner-only temporary files, discard stderr, and never print output.
        try:
            with tempfile.TemporaryFile() as output:
                process = subprocess.run([binary, *args], stdin=subprocess.DEVNULL,
                                         stdout=output, stderr=output if "--help" in args else subprocess.DEVNULL,
                                         timeout=45)
                output.seek(0)
                data = output.read((8 << 20) + 1)
            return process.returncode, data if len(data) <= 8 << 20 else b""
        except (OSError, subprocess.TimeoutExpired):
            return None, b""

    results["executable"] = os.path.isfile(binary) and os.access(binary, os.X_OK)
    if not results["executable"]:
        return results
    code, _ = capture(["--version"])
    results["version_command"] = code == 0
    code, output = capture(["--help"])
    results["resume_flag"] = code == 0 and (
        b"--session" in output if agent == "opencode" else b"--resume" in output)
    if agent == "opencode":
        code, _ = capture(["auth", "list"])
        results["auth_command"] = code == 0
        # Auth list can succeed with no credentials; do not claim authenticated.
        code, output = capture(["session", "list", "--format", "json", "--max-count", "5"])
        try:
            # Native OpenCode prints nothing for an empty session listing.
            history = json.loads(output.strip() or b"[]")
            results["history_command"] = code == 0 and isinstance(history, list)
            if results["history_command"]:
                results["history_count"] = len(history)
        except (ValueError, TypeError):
            results["history_command"] = False
    else:
        code, _ = capture(["status", "--format", "json"])
        # A nonzero exit can simply mean not signed in. Show no identity/token.
        results["auth_command"] = code == 0
    return results


def probe_existing(cli, sb, agent):
    script = inspect.getsource(native_probe) + "\nimport json\nprint(json.dumps(native_probe(" + repr(agent) + ")))"
    command = "python3 -c " + shlex.quote(script)
    result = cli.exec_retry(sb, ["sh", "-lc", cli.SH_WRAP.format(cmd=command)],
                            timeout_seconds=240, attempts=1)
    if result.returncode:
        raise RuntimeError("native probe did not complete")
    value = json.loads(result.stdout or "{}")
    if not isinstance(value, dict):
        raise RuntimeError("invalid probe metadata")
    # Validate remote metadata before anything is printed locally.
    permitted = {"executable", "version_command", "resume_flag", "auth_command", "history_command", "history_count"}
    if set(value) - permitted or any(type(v) is not bool for k, v in value.items() if k != "history_count"):
        raise RuntimeError("invalid probe metadata")
    if "history_count" in value and (type(value["history_count"]) is not int or not 0 <= value["history_count"] <= 5):
        raise RuntimeError("invalid history metadata")
    return value


class SmokeLoginRequired(RuntimeError):
    pass


def model_turns(cli, sb, harness, timeout):
    """Use the real native JSON response/resume path; never print model output."""
    if harness.name == "cursor" and not cli.cursor_auth_status(sb):
        raise SmokeLoginRequired("Cursor is not signed in. Run cws-agent login for this sandbox, "
                                 "then rerun --model-turns. No model prompt was sent.")
    state = {}
    args = types.SimpleNamespace(yolo=False, permission_mode="native")
    first = cli.telegram_agent_reply(
        sb, harness, "Reply with exactly CWS_SMOKE_FIRST. Do not use tools, access files, or run commands.",
        state, timeout, args)
    sid = state.get("id")
    if not sid or state.get("uncertain") or "CWS_SMOKE_FIRST" not in first:
        raise RuntimeError("initial native model turn did not pass")
    second = cli.telegram_agent_reply(
        sb, harness, "What exact token did I ask you to reply with in my previous message? "
        "Reply only with that token. Do not use tools, access files, or run commands.",
        state, timeout, args)
    if state.get("id") != sid or state.get("uncertain") or "CWS_SMOKE_FIRST" not in second:
        raise RuntimeError("native conversation resume did not pass")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="existing running sandbox; no create/delete operations")
    parser.add_argument("--model-turns", action="store_true",
                        help="send two small possibly billable prompts; creates one saved test conversation")
    parser.add_argument("--timeout", type=int, default=120, help="timeout per optional model turn (1-300 seconds)")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 300:
        parser.error("--timeout must be between 1 and 300 seconds")
    cli = load_cli()
    sb = cli.require_active(args.name)
    harness = cli.active_harness(sb)
    if harness.name not in ("opencode", "cursor"):
        raise RuntimeError("sandbox must use OpenCode or Cursor")
    print("Probing installed " + harness.name + " CLI; native output is hidden.", flush=True)
    results = probe_existing(cli, sb, harness.name)
    required = ("executable", "version_command", "resume_flag")
    if harness.name == "opencode":
        required += ("history_command",)
    for key in required:
        print(("PASS: " if results.get(key) else "FAIL: ") + key.replace("_", " "), flush=True)
    print("Auth status command: " + ("completed" if results.get("auth_command") else "not successful; sign-in may be required")
          + ". This does not prove model access.")
    if harness.name == "cursor":
        print("SKIP: Cursor history is an interactive picker; private storage is not parsed.")
    if not all(results.get(key) for key in required):
        return 1
    if args.model_turns:
        model_turns(cli, sb, harness, args.timeout)
        print("PASS: two native model turns preserved the same conversation and context.")
        print("One small test conversation remains in agent history; no automatic deletion was attempted.")
    else:
        print("SKIP: model access and conversation resume (opt in with --model-turns).")
    print("NOT TESTED: fresh installation, history upload/download, snapshot/restore, interactive terminal, Telegram delivery.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeLoginRequired as error:
        raise SystemExit("FAIL: " + str(error)) from None
    except Exception as error:
        # SDK/native exception strings can include requests, prompts, or secrets.
        raise SystemExit("FAIL: " + type(error).__name__ + "; harness smoke did not pass (details suppressed)") from None
