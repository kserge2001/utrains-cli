"""
Looks at the computer utrains is running on.

Two jobs:
  1. Describe the machine (OS, RAM, CPU, shell) so the agent knows what kind of
     commands are valid here.
  2. Find which command-line tools are installed (docker, aws, az, gh, git, …)
     so the agent only reaches for tools that actually exist.

The amount of RAM also decides which local model we recommend - bigger models
need more memory.
"""

import os
import platform
import shutil

try:  # psutil gives reliable RAM numbers on every OS; optional fallback below.
    import psutil
except ImportError:  # pragma: no cover - psutil is a normal dependency
    psutil = None


# The CLIs we teach the agent about. If one is installed, the agent may use it.
KNOWN_TOOLS = [
    "python", "pip", "git", "gh", "docker", "docker-compose", "kubectl",
    "aws", "az", "gcloud", "terraform", "ansible", "node", "npm", "npx",
    "curl", "wget", "ssh", "scp", "helm", "make", "jq",
]

def total_ram_gb() -> float | None:
    """Best-effort total physical RAM in gigabytes (None if it can't be read)."""
    if psutil is not None:
        return round(psutil.virtual_memory().total / 1_000_000_000, 1)

    # Fallbacks so the tool still works if psutil is missing.
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return round(pages * page_size / 1_000_000_000, 1)
    except (ValueError, OSError):
        pass
    return None


def available_tools() -> dict[str, str | None]:
    """Map every known tool to its path on disk, or None if it's not installed."""
    return {name: shutil.which(name) for name in KNOWN_TOOLS}


def installed_tool_names() -> list[str]:
    """Just the names of the tools that are actually installed."""
    return [name for name, path in available_tools().items() if path]


def default_shell_label() -> str:
    """A human-readable name for the shell the executor will use."""
    return "PowerShell" if platform.system() == "Windows" else "bash"


def system_summary() -> dict:
    """One tidy dictionary describing this machine - fed to the agent and shown by `doctor`."""
    return {
        "os": platform.system(),               # Windows / Linux / Darwin
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine": platform.machine(),         # x86_64, arm64, AMD64 …
        "python": platform.python_version(),
        "cpu_cores": os.cpu_count(),
        "ram_gb": total_ram_gb(),
        "shell": default_shell_label(),
        "tools_installed": installed_tool_names(),
    }


# The models utrains knows how to suggest, smallest → largest. `min_ram` is the
# RAM (GB) below which the model will likely feel slow or won't fit comfortably.
MODEL_CATALOG = [
    {"name": "llama3.2:3b", "size": "~2 GB",   "min_ram": 4,  "note": "light & fast, basic reasoning"},
    {"name": "llama3.1:8b", "size": "~4.7 GB", "min_ram": 8,  "note": "great all-rounder"},
    {"name": "qwen2.5:14b", "size": "~9 GB",   "min_ram": 16, "note": "strong reasoning"},
    {"name": "qwen2.5:32b", "size": "~20 GB",  "min_ram": 32, "note": "best quality, needs the RAM"},
]


def recommend_model(ram_gb: float | None) -> str:
    """
    Pick a local model that should run comfortably given the machine's RAM.

    Rough rule of thumb (a model needs ~the size of its weights in free RAM):
      < 8 GB  → a small 3B model
      8–16 GB → a solid 8B model
      16–32 GB→ a strong 14B model
      ≥ 32 GB → a large 32B model
    """
    if ram_gb is None:
        return "llama3.1:8b"          # safe middle-of-the-road default
    if ram_gb < 8:
        return "llama3.2:3b"
    if ram_gb < 16:
        return "llama3.1:8b"
    if ram_gb < 32:
        return "qwen2.5:14b"
    return "qwen2.5:32b"
