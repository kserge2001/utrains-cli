# Installing utrains

utrains installs straight from GitHub — no S3, no servers, nothing to pay for.
You only need **Python 3.10+** (the installer checks and tells you if it's missing).

## Windows (PowerShell)

Open **PowerShell** and paste one line:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/kserge2001/utrains-cli/main/install.ps1 | iex"
```

## macOS / Linux (Terminal)

```bash
curl -fsSL https://raw.githubusercontent.com/kserge2001/utrains-cli/main/install.sh | bash
```

## What it does
1. Checks for Python 3.10+ (and points you to the download if it's missing).
2. `pip install`s utrains from GitHub's free zip archive (no git needed).
3. Installs the `utrains` command for your user account.

## After installing

```bash
# Option A — run a LOCAL model (private, free, no API key):
utrains setup        # installs Ollama and pulls a model sized to your machine

# Option B — use GPT or Claude:
#   put your key in a .env file next to where you run utrains:
#     OPENAI_API_KEY=sk-...
#     # or
#     ANTHROPIC_API_KEY=sk-ant-...

# Start chatting:
utrains              # classic UI (default)
utrains --tui        # full-screen animated UI
```

## Updating

Re-run the same one-liner — it always pulls the latest from GitHub.

## Uninstalling

```bash
pip uninstall utrains
```

## Troubleshooting
- **`utrains` not found** after install → the installer prints the folder to add to
  your `PATH`. Add it and restart your terminal.
- **`pip` not found** → install Python from <https://www.python.org/downloads/>
  (on Windows, tick *"Add python.exe to PATH"*).
