"""
The thin wire to Ollama - the local model server that powers the agent.

Ollama exposes a simple HTTP API on http://localhost:11434. We only need three
things from it: check it's up, list the models you've pulled, and run a chat
completion. Override the address with the OLLAMA_HOST env var if your server
lives somewhere else.
"""

import json
import os

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


class OllamaError(RuntimeError):
    """Raised when the Ollama server can't be reached or returns an error."""


def is_running() -> bool:
    """True if an Ollama server answers on the configured host."""
    try:
        requests.get(f"{OLLAMA_HOST}/api/tags", timeout=3)
        return True
    except requests.RequestException:
        return False


def list_models() -> list[str]:
    """Names of the models already pulled locally (e.g. ['llama3.1:8b'])."""
    try:
        resp = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=10)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except requests.RequestException as exc:
        raise OllamaError(f"Could not reach Ollama at {OLLAMA_HOST}: {exc}") from exc


def has_model(model: str) -> bool:
    """True if `model` (with or without an explicit :tag) is available locally."""
    installed = list_models()
    if model in installed:
        return True
    # Allow 'llama3.1' to match 'llama3.1:latest' and friends.
    base = model.split(":")[0]
    return any(name.split(":")[0] == base for name in installed)


def chat(model: str, messages: list[dict], force_json: bool = False,
         temperature: float = 0.1, timeout: int = 600, num_ctx: int = 8192) -> str:
    """
    Send a conversation to the model and return its reply text.

    `messages` is the usual list of {"role": ..., "content": ...} dicts.
    With force_json=True we ask Ollama to constrain the output to valid JSON,
    which keeps the agent's command-planning machine-readable.

    num_ctx widens the model's context window (Ollama defaults to a small 2048,
    which makes the agent "forget" earlier turns). Override with UTRAINS_NUM_CTX.
    """
    num_ctx = int(os.getenv("UTRAINS_NUM_CTX", num_ctx))
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    if force_json:
        payload["format"] = "json"

    try:
        resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise OllamaError(
            f"Chat request to Ollama failed: {exc}\n"
            f"Is the server running? Try `utrains doctor` or `ollama serve`."
        ) from exc

    data = resp.json()
    return data.get("message", {}).get("content", "")