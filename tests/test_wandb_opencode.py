"""Opt-in native OpenCode checks; only a loopback mock receives fake credentials.

Run with CWS_TEST_OPENCODE_BIN=/absolute/path/to/opencode and unittest discovery.
No provider calls, sandbox resources, or existing user configuration are used.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_terminal import agent


@unittest.skipUnless(os.environ.get("CWS_TEST_OPENCODE_BIN"), "set CWS_TEST_OPENCODE_BIN for native loopback integration")
class WandbOpenCodeNativeTests(unittest.TestCase):
    def test_native_config_and_reasoning_tool_round_trip(self):
        binary = str(Path(os.environ["CWS_TEST_OPENCODE_BIN"]).resolve())
        requests = []
        with tempfile.TemporaryDirectory(prefix="cws-wandb-native-") as temporary:
            root = Path(temporary).resolve()
            project = root / "project"
            project.mkdir()
            fixture = project / "fixture.txt"
            fixture.write_text("local mock fixture\n")

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                    messages = body["messages"]
                    tool_seen = any(message.get("role") == "tool" for message in messages)
                    tools = {tool["function"]["name"] for tool in body.get("tools", [])}
                    if "read" in tools and not tool_seen:
                        deltas = [
                            {"role": "assistant", "reasoning": "I will inspect the local fixture."},
                            {"tool_calls": [{"index": 0, "id": "call_fixture", "type": "function", "function": {
                                "name": "read", "arguments": json.dumps({"filePath": str(fixture)})}}]},
                        ]
                        finish = "tool_calls"
                    else:
                        deltas = [{"role": "assistant", "reasoning": "The fixture confirms the result."},
                                  {"content": "Local mock reply complete."}]
                        finish = "stop"
                    if body.get("stream"):
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.end_headers()
                        for delta in deltas:
                            chunk = {"id": "chatcmpl_fixture", "object": "chat.completion.chunk", "created": 1,
                                     "model": body["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
                            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                        chunk = {"id": "chatcmpl_fixture", "object": "chat.completion.chunk", "created": 1,
                                 "model": body["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                                 "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
                        self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
                    else:
                        payload = {"id": "chatcmpl_fixture", "object": "chat.completion", "created": 1,
                                   "model": body["model"], "choices": [{"index": 0, "finish_reason": "stop", "message": {
                                       "role": "assistant", "content": "Local fixture title"}}],
                                   "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps(payload).encode())

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            config = agent.wandb_opencode_config(
                types.SimpleNamespace(wandb=True, wandb_model=None), agent.HARNESSES["opencode"],
                {"WANDB_API_KEY": "fake-local-test-key"})
            config["provider"]["cws-wandb"]["options"]["baseURL"] = f"http://127.0.0.1:{server.server_port}/v1"
            config["permission"] = {"*": "deny", "read": "allow"}
            config["share"] = "disabled"
            config["compaction"] = {"auto": False}
            # Never inherit user auth, native configuration, proxies, plugins, or history.
            env = {
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(root / "home"),
                "XDG_CONFIG_HOME": str(root / "config"), "XDG_DATA_HOME": str(root / "data"),
                "XDG_STATE_HOME": str(root / "state"), "XDG_CACHE_HOME": str(root / "cache"),
                "WANDB_API_KEY": "fake-local-test-key", "OPENCODE_CONFIG_CONTENT": json.dumps(config),
                "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
                "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1",
                "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "NO_COLOR": "1",
            }

            def run(*arguments):
                result = subprocess.run([binary, *arguments], cwd=project, env=env, capture_output=True,
                                        text=True, timeout=90)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                return result.stdout

            parsed = json.loads(run("debug", "config"))
            self.assertEqual(parsed["model"], "cws-wandb/" + agent.WANDB_OPENCODE_MODEL)
            model = parsed["provider"]["cws-wandb"]["models"][agent.WANDB_OPENCODE_MODEL]
            self.assertEqual(model["interleaved"], {"field": "reasoning"})
            output = run("run", "--pure", "--format", "json", "--title", "Local mock integration", "--",
                         "Read fixture.txt, then acknowledge it.")
            events = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
            self.assertFalse(any(event.get("type") == "error" for event in events), output)
            self.assertTrue(any(event.get("type") == "text" for event in events), output)
            session = next(event["sessionID"] for event in events if event.get("sessionID"))
            second_output = run("run", "--pure", "--format", "json", "--session", session, "--",
                                "Continue the same local mock session.")
            second_events = [json.loads(line) for line in second_output.splitlines() if line.startswith("{")]
            self.assertFalse(any(event.get("type") == "error" for event in second_events), second_output)
            self.assertTrue(any(event.get("type") == "text" for event in second_events), second_output)
            self.assertTrue(requests)
            for request in requests:
                self.assertEqual(request["path"], "/v1/chat/completions")
                self.assertEqual(request["auth"], "Bearer fake-local-test-key")
                self.assertEqual(request["body"]["model"], agent.WANDB_OPENCODE_MODEL)
            tool_turns = [request["body"]["messages"] for request in requests
                          if any(message.get("role") == "tool" for message in request["body"]["messages"])]
            self.assertTrue(tool_turns, "native OpenCode did not send a tool result back to the mock")
            for messages in tool_turns:
                assistant = next(message for message in messages if message.get("tool_calls"))
                self.assertEqual(assistant.get("reasoning"), "I will inspect the local fixture.")
                self.assertNotIn("reasoning_content", assistant)
                tool = next(message for message in messages if message.get("role") == "tool")
                self.assertEqual(tool["tool_call_id"], "call_fixture")
                self.assertIn("local mock fixture", str(tool["content"]))
            self.assertTrue(any("Continue the same local mock session." in str(request["body"]["messages"])
                                and any(message.get("role") == "tool" for message in request["body"]["messages"])
                                for request in requests), "second native invocation did not preserve first-turn history")


if __name__ == "__main__":
    unittest.main()
