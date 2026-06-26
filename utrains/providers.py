"""
Model backends — local Ollama, Anthropic Claude, or OpenAI GPT.

utrains picks the backend from the model NAME, so the rest of the app doesn't
care which one is in use:

  - "claude-*" / "anthropic*"         -> Anthropic API  (needs ANTHROPIC_API_KEY)
  - "gpt-*", "chatgpt*", "o1/o3/o4*"  -> OpenAI API      (needs OPENAI_API_KEY)
  - anything else (e.g. "qwen2.5:14b")-> local Ollama

The cloud SDKs (`anthropic`, `openai`) are optional — they're imported lazily,
so an Ollama-only user never needs them installed. API keys come from the
environment (or ~/.utrains/.env — see config.load_env()).
"""

import os

from . import ollama_client


class ProviderError(RuntimeError):
    """Any backend failure (missing key, missing package, API error)."""


def detect_provider(model: str) -> str:
    """Return 'anthropic', 'openai', or 'ollama' for a model name."""
    m = (model or "").lower()
    if m.startswith(("claude", "anthropic")):
        return "anthropic"
    if m.startswith(("gpt", "chatgpt", "o1", "o3", "o4")):
        return "openai"
    return "ollama"


def chat(model: str, messages: list[dict], force_json: bool = False,
         temperature: float = 0.1, timeout: int = 600, num_ctx: int = 8192) -> str:
    """Send a conversation to whichever backend `model` names; return reply text."""
    provider = detect_provider(model)
    if provider == "anthropic":
        return _anthropic_chat(model, messages, timeout)
    if provider == "openai":
        return _openai_chat(model, messages, force_json, temperature, timeout)
    try:
        return ollama_client.chat(model, messages, force_json=force_json,
                                  temperature=temperature, timeout=timeout, num_ctx=num_ctx)
    except ollama_client.OllamaError as exc:
        raise ProviderError(str(exc)) from exc


# --------------------------------------------------------------------------
# Anthropic (Claude)
# --------------------------------------------------------------------------

def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Anthropic keeps `system` separate from the user/assistant messages."""
    system_parts, convo = [], []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        else:
            convo.append({"role": m["role"], "content": m["content"]})
    return "\n\n".join(system_parts), convo


def _anthropic_chat(model: str, messages: list[dict], timeout: int) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise ProviderError("Claude models need the 'anthropic' package. "
                            "Run: pip install anthropic") from exc
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ProviderError("ANTHROPIC_API_KEY is not set. Put it in your environment "
                            "or ~/.utrains/.env to use Claude models.")

    system, convo = _split_system(messages)
    kwargs = {
        "model": model,
        "max_tokens": 4096,
        # Note: temperature / thinking are intentionally omitted — Opus 4.8/4.7
        # reject `temperature`, and the JSON-only instruction in our prompt keeps
        # the reply terse without enabling thinking.
        "messages": convo or [{"role": "user", "content": "continue"}],
    }
    if system.strip():
        kwargs["system"] = system
    try:
        client = anthropic.Anthropic(timeout=timeout)
        resp = client.messages.create(**kwargs)
    except anthropic.AnthropicError as exc:
        raise ProviderError(f"Claude API error: {exc}") from exc
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


# --------------------------------------------------------------------------
# OpenAI (GPT)
# --------------------------------------------------------------------------

def _openai_chat(model: str, messages: list[dict], force_json: bool,
                 temperature: float, timeout: int) -> str:
    try:
        import openai
    except ImportError as exc:
        raise ProviderError("GPT models need the 'openai' package. "
                            "Run: pip install openai") from exc
    if not os.getenv("OPENAI_API_KEY"):
        raise ProviderError("OPENAI_API_KEY is not set. Put it in your environment "
                            "or ~/.utrains/.env to use GPT models.")

    reasoning = model.lower().startswith(("o1", "o3", "o4"))
    kwargs = {"model": model, "messages": messages}
    if reasoning:
        kwargs["max_completion_tokens"] = 4096   # o-series: no temperature/json mode
    else:
        kwargs["max_tokens"] = 4096
        kwargs["temperature"] = temperature
        if force_json:
            kwargs["response_format"] = {"type": "json_object"}
    try:
        client = openai.OpenAI(timeout=timeout)
        resp = client.chat.completions.create(**kwargs)
    except openai.OpenAIError as exc:
        raise ProviderError(f"OpenAI API error: {exc}") from exc
    return resp.choices[0].message.content or ""
