"""
utrains memory — what the agent is allowed to remember between turns and sessions.

Two kinds of memory:
  • Session memory : the recent task → answer pairs from THIS chat session, so a
    follow-up like "now delete that one" still has context. Held in RAM by the
    chat loop and passed in as context; gone when you quit.
  • Persistent memory : durable notes you (or the agent) save to
    ~/.utrains/memory.md, injected into the prompt on every run. Good for facts
    like "prod cluster is eks-east" or "default AWS profile is acme-prod".

A single switch (`memory_enabled`, default ON) turns BOTH off — handy when you
want a clean, context-free run or a faster prompt on a small model.
"""

from pathlib import Path

from . import config

MEMORY_FILE = Path.home() / ".utrains" / "memory.md"


def is_enabled() -> bool:
    """True unless the user has switched memory off (default: on)."""
    return config.load().get("memory_enabled", True)


def set_enabled(value: bool) -> None:
    """Turn memory on or off and remember the choice."""
    data = config.load()
    data["memory_enabled"] = bool(value)
    config.save(data)


def load_text() -> str:
    """The persistent notes as plain text ('' if there are none)."""
    if MEMORY_FILE.exists():
        return MEMORY_FILE.read_text(encoding="utf-8").strip()
    return ""


def add(note: str) -> None:
    """Append one bullet note to persistent memory."""
    note = note.strip()
    if not note:
        return
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = load_text()
    bullet = f"- {note}"
    combined = f"{existing}\n{bullet}" if existing else bullet
    MEMORY_FILE.write_text(combined.strip() + "\n", encoding="utf-8")


def clear() -> None:
    """Forget all persistent notes."""
    if MEMORY_FILE.exists():
        MEMORY_FILE.unlink()


def build_context(session_notes: list[str] | None = None) -> str:
    """
    Combine persistent notes + this session's recent history into one context
    block for the prompt. Returns '' when memory is disabled or empty.
    """
    if not is_enabled():
        return ""

    parts: list[str] = []
    persistent = load_text()
    if persistent:
        parts.append("Things you saved to remember:\n" + persistent)
    if session_notes:
        recent = "\n".join(session_notes[-6:])  # keep the prompt small
        parts.append("Earlier in this session:\n" + recent)
    return "\n\n".join(parts)
