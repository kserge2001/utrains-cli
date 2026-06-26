"""
Small persistent settings store for utrains.

Lives at ~/.utrains/config.json and currently remembers which model you chose
during setup. Everything is plain JSON so you can edit it by hand if you like.
"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".utrains"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load() -> dict:
    """Read the saved settings (empty dict if nothing saved yet)."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save(data: dict) -> None:
    """Write settings to disk, creating ~/.utrains if needed."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_env() -> None:
    """
    Load a .env file into the environment (without overriding existing vars).

    Checks ./.env first (project-local), then ~/.utrains/.env (global) - so users
    can keep ANTHROPIC_API_KEY / OPENAI_API_KEY in a file instead of exporting
    them every session. Simple KEY=VALUE lines; # comments allowed.
    """
    for env_path in (Path.cwd() / ".env", CONFIG_DIR / ".env"):
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        except OSError:
            pass


def get_model() -> str | None:
    """The model to use: env var wins, then the saved config, else None."""
    return os.getenv("UTRAINS_MODEL") or load().get("model")


def set_model(model: str) -> None:
    """Remember the chosen model for next time."""
    data = load()
    data["model"] = model
    save(data)