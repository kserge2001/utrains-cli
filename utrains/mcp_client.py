"""
A small Model Context Protocol (MCP) client.

MCP lets utrains borrow tools from external "servers" (a GitHub server, a
database server, a filesystem server, …). This module:
  • reads ~/.utrains/mcp.json,
  • launches each server as a subprocess speaking JSON-RPC over stdio,
  • lists the tools each one offers,
  • and calls a tool when the agent asks for it.

It is entirely OPTIONAL: with no mcp.json (or an empty one) MCP is simply off and
the agent works exactly as before, using the shell.

mcp.json shape:
{
  "servers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}
    }
  }
}

Tool names are exposed to the agent as "server.tool" (e.g. "github.create_issue").
"""

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

MCP_CONFIG = Path.home() / ".utrains" / "mcp.json"
PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    """Something went wrong talking to an MCP server."""


def _expand_env(value: str) -> str:
    """Turn '${NAME}' inside a config value into the real environment variable."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


class MCPServer:
    """One MCP server subprocess and the JSON-RPC plumbing to talk to it."""

    def __init__(self, name: str, command: str, args=None, env=None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self._id = 0
        self._inbox: queue.Queue = queue.Queue()

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        """Launch the server, handshake, and load its tool list."""
        full_env = os.environ.copy()
        full_env.update({k: _expand_env(v) for k, v in self.env.items()})
        self.proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env=full_env,
        )
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._initialize()
        self._load_tools()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    # -- low-level JSON-RPC -------------------------------------------------
    def _read_loop(self) -> None:
        """Background reader: parse every line the server prints into the inbox."""
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._inbox.put(json.loads(line))
            except json.JSONDecodeError:
                continue  # MCP stdio is line-delimited JSON; skip noise

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, message: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        rid = self._next_id()
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(f"'{method}' timed out on server '{self.name}'")
            try:
                msg = self._inbox.get(timeout=remaining)
            except queue.Empty:
                raise MCPError(f"'{method}' timed out on server '{self.name}'")
            if msg.get("id") == rid:
                if "error" in msg:
                    raise MCPError(msg["error"].get("message", "unknown error"))
                return msg.get("result", {})
            # otherwise it's a notification or unrelated reply — ignore it

    def _notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # -- MCP handshake & calls ---------------------------------------------
    def _initialize(self) -> None:
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "utrains", "version": "0.1.0"},
        })
        self._notify("notifications/initialized")

    def _load_tools(self) -> None:
        self.tools = self._request("tools/list").get("tools", [])

    def call(self, tool_name: str, arguments: dict | None = None) -> dict:
        return self._request("tools/call", {"name": tool_name, "arguments": arguments or {}})


class MCPManager:
    """Loads the config, starts the servers, and routes tool calls to the right one."""

    def __init__(self):
        self.servers: dict[str, MCPServer] = {}
        self._tool_index: dict[str, tuple[str, str]] = {}  # "server.tool" -> (server, tool)

    def load(self) -> "MCPManager":
        """Read mcp.json (no-op if it doesn't exist)."""
        if not MCP_CONFIG.exists():
            return self
        try:
            cfg = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise MCPError(f"Could not read {MCP_CONFIG}: {exc}") from exc
        for name, spec in cfg.get("servers", {}).items():
            self.servers[name] = MCPServer(name, spec["command"], spec.get("args"), spec.get("env"))
        return self

    def start_all(self) -> dict[str, str]:
        """
        Start every configured server. Returns {server_name: error_or_'ok'} so the
        caller can report which came up. A failing server never crashes utrains.
        """
        status: dict[str, str] = {}
        for name, srv in list(self.servers.items()):
            try:
                srv.start()
                for tool in srv.tools:
                    self._tool_index[f"{name}.{tool['name']}"] = (name, tool["name"])
                status[name] = "ok"
            except (MCPError, OSError, KeyError) as exc:
                status[name] = str(exc)
                self.servers.pop(name, None)
        return status

    def has_servers(self) -> bool:
        return bool(self.servers)

    def tool_specs(self) -> list[dict]:
        """Flat list of {name, description} the agent can be told about."""
        specs = []
        for qualified, (server, tool) in self._tool_index.items():
            srv = self.servers[server]
            desc = next((t.get("description", "") for t in srv.tools if t["name"] == tool), "")
            specs.append({"name": qualified, "description": desc})
        return specs

    def call(self, qualified_name: str, arguments: dict | None = None) -> str:
        """Call 'server.tool' and return its result as plain text."""
        if qualified_name not in self._tool_index:
            raise MCPError(f"Unknown MCP tool '{qualified_name}'")
        server, tool = self._tool_index[qualified_name]
        result = self.servers[server].call(tool, arguments)
        # MCP returns {"content": [{"type": "text", "text": ...}], "isError": bool}
        chunks = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        text = "\n".join(chunks) or json.dumps(result)
        if result.get("isError"):
            text = "ERROR: " + text
        return text

    def stop_all(self) -> None:
        for srv in self.servers.values():
            srv.stop()
