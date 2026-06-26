# Get utrains

Two ways to install. Most people want **Option A** - download one file and run it.
No source code, no setup.

## Option A - Download & run (no Python, nothing to install)

Grab the single file for your system from the latest release and run it:

**Windows**
1. Download **utrains-windows.exe**:
   <https://github.com/kserge2001/utrains-cli/releases/latest/download/utrains-windows.exe>
2. **Double-click it.** Windows may show a blue "Windows protected your PC" box
   (the app isn't code-signed yet) - click **More info -> Run anyway**.
3. It will ask: *"Install it so you can run 'utrains' from any terminal? [Y/n]"* -
   press **Enter**. Then open a **new** PowerShell and just type `utrains`.

   (Prefer to do it yourself? Run `.\utrains-windows.exe install` from PowerShell,
   or drop the file in any folder that's already on your PATH.)

**macOS**
1. Download **utrains-macos**:
   <https://github.com/kserge2001/utrains-cli/releases/latest/download/utrains-macos>
2. In **Terminal**:
   ```bash
   cd ~/Downloads
   chmod +x utrains-macos
   xattr -d com.apple.quarantine utrains-macos   # clears the "unidentified developer" block
   ./utrains-macos
   ```

That's it - no Python, no git, no source code to look at.

## Option B - Install with Python (auto-updates)

If you already have **Python 3.10+**, a one-liner installs the `utrains` command:

**Windows (PowerShell)**
```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/kserge2001/utrains-cli/main/install.ps1 | iex"
```
**macOS / Linux**
```bash
curl -fsSL https://raw.githubusercontent.com/kserge2001/utrains-cli/main/install.sh | bash
```

## First run

```bash
# Option A - a LOCAL model (private, free, no API key):
utrains setup        # installs Ollama and pulls a model sized to your machine

# Option B - use GPT or Claude: put your key in a .env file next to utrains:
#     OPENAI_API_KEY=sk-...
#     # or
#     ANTHROPIC_API_KEY=sk-ant-...

utrains              # start (classic UI)
utrains --tui        # full-screen animated UI
```

## Troubleshooting
- **Windows SmartScreen / macOS "unidentified developer"** - the binaries aren't
  code-signed yet, so the OS warns the first time. Use "Run anyway" (Windows) or
  the `xattr` line above (macOS). Signing removes this (Apple Developer ID is
  $99/yr; Windows code-signing certs vary).
- **`utrains` not found (Option B)** - the installer prints the folder to add to
  your PATH. Add it and restart your terminal.
