"""
First-run setup: get Ollama installed, get the server running, and pull a model.

`utrains setup` calls run_setup(). It is deliberately conservative — it tells you
what it's about to do, and on Windows/macOS it falls back to printing the
official download link rather than silently installing system software.
"""

import platform
import shutil
import subprocess
import time

from . import config, ollama_client
from .system_info import MODEL_CATALOG, recommend_model, system_summary


def _ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def install_ollama(auto: bool) -> bool:
    """
    Make sure the `ollama` binary exists. Returns True if it's available afterward.

    Linux: uses the official install script.
    Windows: tries winget, else points you to the installer.
    macOS: tries Homebrew, else points you to the installer.
    """
    if _ollama_installed():
        print("✓ Ollama is already installed.")
        return True

    os_name = platform.system()
    print("Ollama is not installed yet.")

    if os_name == "Linux":
        if auto or _ask("Install Ollama now via the official script?"):
            print("→ Running: curl -fsSL https://ollama.com/install.sh | sh")
            subprocess.run("curl -fsSL https://ollama.com/install.sh | sh",
                           shell=True, check=False)
    elif os_name == "Windows":
        if shutil.which("winget") and (auto or _ask("Install Ollama via winget?")):
            print("→ Running: winget install --id Ollama.Ollama -e")
            subprocess.run(["winget", "install", "--id", "Ollama.Ollama", "-e",
                            "--accept-source-agreements", "--accept-package-agreements"],
                           check=False)
        else:
            print("Please install Ollama from: https://ollama.com/download/windows")
    elif os_name == "Darwin":
        if shutil.which("brew") and (auto or _ask("Install Ollama via Homebrew?")):
            print("→ Running: brew install ollama")
            subprocess.run(["brew", "install", "ollama"], check=False)
        else:
            print("Please install Ollama from: https://ollama.com/download/mac")
    else:
        print("Unsupported OS. Install Ollama manually from https://ollama.com/download")

    if not _ollama_installed():
        print("✗ Ollama still not found on PATH. Install it, then re-run `utrains setup`.")
        return False
    print("✓ Ollama installed.")
    return True


def ensure_server() -> bool:
    """Make sure an Ollama server is reachable, starting `ollama serve` if needed."""
    if ollama_client.is_running():
        print("✓ Ollama server is running.")
        return True
    if not _ollama_installed():
        return False

    print("→ Starting the Ollama server (ollama serve)…")
    try:
        # Launch detached so it keeps running after setup returns.
        subprocess.Popen(["ollama", "serve"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        print(f"✗ Could not start the server: {exc}")
        return False

    for _ in range(15):  # give it a few seconds to come up
        if ollama_client.is_running():
            print("✓ Ollama server is running.")
            return True
        time.sleep(1)
    print("✗ The server didn't come up. Try running `ollama serve` in another terminal.")
    return False


def pull_model(model: str) -> bool:
    """Download the chosen model with `ollama pull` (progress streams to the screen)."""
    if ollama_client.is_running() and ollama_client.has_model(model):
        print(f"✓ Model '{model}' is already available.")
        return True
    print(f"→ Pulling model '{model}' (this can take a while on first download)…")
    result = subprocess.run(["ollama", "pull", model], check=False)
    if result.returncode == 0:
        print(f"✓ Model '{model}' is ready.")
        return True
    print(f"✗ Failed to pull '{model}'. Pick another with `utrains setup --model <name>`.")
    return False


def choose_model(info: dict, model_arg: str | None = None, auto: bool = False) -> str:
    """
    Interactively suggest a model based on the machine's RAM and let the user pick.

    A model named on the command line wins outright; --auto takes the
    recommendation silently; otherwise we print a menu and read a choice.
    """
    from . import enable_utf8_output
    enable_utf8_output()

    recommended = recommend_model(info["ram_gb"])
    if model_arg:
        return model_arg
    if auto:
        return recommended

    ram = info["ram_gb"]
    print("Suggested models for this machine (based on your RAM):\n")
    for i, m in enumerate(MODEL_CATALOG, 1):
        fits = ram is None or ram >= m["min_ram"]
        marker = "  ← recommended" if m["name"] == recommended else ""
        warn = "" if fits else "  (⚠ may be slow on this machine)"
        print(f"  {i}. {m['name']:<13} {m['size']:<8} {m['note']}{marker}{warn}")
    print(f"  {len(MODEL_CATALOG) + 1}. other (type any Ollama model name)\n")

    choice = input(f"Pick a model [Enter = {recommended}]: ").strip()
    if not choice:
        return recommended
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(MODEL_CATALOG):
            return MODEL_CATALOG[idx - 1]["name"]
        if idx == len(MODEL_CATALOG) + 1:
            typed = input("Enter the Ollama model name: ").strip()
            return typed or recommended
    # Anything else: treat what they typed as a model name.
    return choice


def run_setup(model: str | None = None, auto: bool = False) -> int:
    """The whole `utrains setup` flow. Returns a process exit code."""
    info = system_summary()
    print("=" * 60)
    print("utrains setup")
    print("=" * 60)
    print(f"OS         : {info['os']} {info['os_release']} ({info['machine']})")
    print(f"CPU / RAM  : {info['cpu_cores']} cores / {info['ram_gb']} GB")
    print(f"Shell      : {info['shell']}")
    print(f"Tools found: {', '.join(info['tools_installed']) or 'none'}")
    print("-" * 60)

    chosen = choose_model(info, model_arg=model, auto=auto)
    print("-" * 60)

    if not install_ollama(auto):
        return 1
    if not ensure_server():
        return 1
    if not pull_model(chosen):
        return 1

    config.set_model(chosen)
    print("-" * 60)
    print(f"✓ Setup complete. Default model saved as '{chosen}'.")
    print("Try it now:")
    print('   utrains "show me the running docker containers"')
    print("   utrains chat")
    return 0


def _ask(question: str) -> bool:
    """Tiny yes/no prompt (defaults to yes)."""
    answer = input(f"{question} [Y/n] ").strip().lower()
    return answer in ("", "y", "yes")