#!/usr/bin/env bash
# utrains installer for macOS / Linux — installs straight from GitHub.
# No S3, no git required: pip pulls a zip from GitHub's free archive endpoint.
#
# Users run ONE line (no download, no clone):
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh | bash

set -euo pipefail

# ----------------------- CONFIG: point this at your repo ----------------------
REPO="kserge2001/utrains-cli"
BRANCH="main"
# Leave SUBDIR empty if utrains-cli is the repo ROOT (recommended — no git needed).
# Set it (e.g. "utrains-cli") only if the package lives in a subfolder (needs git).
SUBDIR=""
# -----------------------------------------------------------------------------

say() { printf "  %s\n" "$*"; }

say ""
say "Installing utrains..."

PY=""
for name in python3 python; do
    if command -v "$name" >/dev/null 2>&1; then
        if "$name" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
            PY="$name"; break
        fi
    fi
done

if [ -z "$PY" ]; then
    say "Python 3.10+ is required."
    say "Install it from https://www.python.org/downloads/  (or: brew install python), then re-run."
    exit 1
fi

if [ -n "$SUBDIR" ]; then
    PKG="utrains[cloud] @ git+https://github.com/$REPO.git@$BRANCH#subdirectory=$SUBDIR"
else
    PKG="utrains[cloud] @ https://github.com/$REPO/archive/refs/heads/$BRANCH.zip"
fi

say "Downloading from GitHub and installing (a minute or so)..."
"$PY" -m pip install --user --upgrade "$PKG"

BIN="$("$PY" -c 'import site,os;print(os.path.join(site.USER_BASE,"bin"))')"
say ""
say "Done! utrains is installed."
if ! command -v utrains >/dev/null 2>&1; then
    say "If 'utrains' is not found, add this to your PATH (then restart your shell):"
    say "   export PATH=\"$BIN:\$PATH\""
fi
say ""
say "Next:"
say "  - Local model:   utrains setup        (installs Ollama + pulls a model)"
say "  - GPT / Claude:  put OPENAI_API_KEY (or ANTHROPIC_API_KEY) in a .env file"
say "  - Start:         utrains"
say ""
