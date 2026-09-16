#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["cwsandbox[wandb]>=1.10,<2"]
# ///
"""Live resumable upload check: uv run smoke_upload.py EXISTING_SANDBOX_NAME.

Only synthetic data in temporary directories. Never touches /workspace or agents.
"""
import hashlib
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time


def load_cli():
    loader = importlib.machinery.SourceFileLoader(
        "cws_agent_upload_smoke", str(Path(__file__).resolve().with_name("cws-agent")))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="existing sandbox; it will not be stopped")
    args = parser.parse_args()
    cli = load_cli()
    sb = cli.require_active(args.name)
    print(f"Resumable upload smoke test using cwsandbox {importlib.metadata.version('cwsandbox')}", flush=True)
    started = time.monotonic()
    made = cli.upload_result(sb.exec(
        ["python3", "-c", "import tempfile; print(tempfile.mkdtemp(prefix='cws-upload-smoke-', dir='/tmp'))"],
        timeout_seconds=30).result())
    remote = made.stdout.strip()
    if not re.fullmatch(r"/tmp/cws-upload-smoke-[\w-]+", remote):
        raise RuntimeError("unexpected temporary-directory path")

    class InterruptedSandbox:
        interrupt = True
        puts = []

        def exec(self, command, **kwargs):
            request = json.loads(command[-1])
            if request["action"] == "put":
                index = request["index"]
                if self.interrupt and index == 1:
                    raise KeyboardInterrupt()
                self.puts.append(index)
            return sb.exec(command, **kwargs)

    wrapped = InterruptedSandbox()
    try:
        with tempfile.TemporaryDirectory(prefix="cws-upload-smoke-") as local:
            source = Path(local) / "source"
            source.mkdir()
            payload = os.urandom(8 << 20)
            (source / "payload").write_bytes(payload)
            cache = Path(local) / "cache"
            cache.mkdir(mode=0o700)
            cli.upload_cache_root = lambda: cache
            cli.UPLOAD_REMOTE_ROOT, cli.PROJECT_DIR = remote + "/chunks", remote + "/project"
            cli.UPLOAD_CHUNK_SIZE = 1 << 20
            print("Intentionally interrupting after the first verified chunk ...", flush=True)
            try:
                cli.sync_local_dir(wrapped, str(source), include_git=True, extra_excludes=[], clean=False,
                                   session_name=args.name, transfer_timeout=60)
            except cli.UploadPaused:
                pass
            else:
                raise RuntimeError("interruption did not pause the upload")
            if wrapped.puts != [0]:
                raise RuntimeError("first chunk was not committed")
            uid = next(cache.iterdir()).name
            (source / "payload").write_bytes(b"edited after interruption")
            wrapped.interrupt = False
            wrapped.puts.clear()
            cli.sync_local_dir(wrapped, str(source), include_git=True, extra_excludes=[], clean=False,
                               session_name=args.name, transfer_timeout=60, resume_upload=uid)
            if 0 in wrapped.puts or list(cache.iterdir()):
                raise RuntimeError("resume resent a saved chunk or failed to clean local cache")
            check = cli.upload_result(sb.exec([
                "python3", "-c",
                "import hashlib, pathlib, sys; p=pathlib.Path(sys.argv[1]); "
                "assert not list((p/'chunks').glob('*/*.chunk')); "
                "print(hashlib.sha256((p/'project/payload').read_bytes()).hexdigest())", remote],
                timeout_seconds=30).result())
            if check.stdout.strip() != hashlib.sha256(payload).hexdigest():
                raise RuntimeError("remote checksum mismatch")
            print("PASS: interruption, saved-chunk reuse, immutable cache, extraction, checksum, and cleanup")
    finally:
        # Exact path returned by mkdtemp and validated above, never a project path.
        cli.upload_result(sb.exec(["python3", "-c", "import shutil, sys; shutil.rmtree(sys.argv[1])", remote],
                                  timeout_seconds=30).result())
        print("Removed the temporary smoke-test files; project and agent untouched.")
    print(f"PASS: completed in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # SDK exceptions may include request details; never print credentials.
        raise SystemExit(f"FAIL: {type(error).__name__}; live upload check did not pass") from None
