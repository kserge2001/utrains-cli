# utrains installer for Windows (PowerShell) — installs straight from GitHub.
# No S3, no git required: pip pulls a zip from GitHub's free archive endpoint.
#
# Users run ONE line (no download, no clone):
#   powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/OWNER/REPO/main/install.ps1 | iex"

$ErrorActionPreference = "Stop"

# ----------------------- CONFIG: point this at your repo ----------------------
$Repo   = "kserge2001/utrains-cli"
$Branch = "main"
# Leave $Subdir EMPTY if utrains-cli is the repo ROOT (recommended — no git needed).
# Set it (e.g. "utrains-cli") only if the package lives in a subfolder (needs git).
$Subdir = ""
# -----------------------------------------------------------------------------

function Find-Python {
    foreach ($name in @("python", "python3", "py")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $v = & $cmd.Source -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
            if ($v -and [version]$v -ge [version]"3.10") { return $cmd.Source }
        }
    }
    return $null
}

Write-Host ""
Write-Host "  Installing utrains..." -ForegroundColor Cyan

$py = Find-Python
if (-not $py) {
    Write-Host "  Python 3.10+ is required." -ForegroundColor Red
    Write-Host "  Install it from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  (tick 'Add python.exe to PATH' during setup), then re-run this." -ForegroundColor Yellow
    exit 1
}

if ($Subdir) {
    $pkg = "utrains[cloud] @ git+https://github.com/$Repo.git@$Branch#subdirectory=$Subdir"
} else {
    $pkg = "utrains[cloud] @ https://github.com/$Repo/archive/refs/heads/$Branch.zip"
}

Write-Host "  Downloading from GitHub and installing (a minute or so)..." -ForegroundColor Cyan
& $py -m pip install --user --upgrade $pkg

$scripts = & $py -c "import site,os;print(os.path.join(site.USER_BASE,'Scripts'))"
Write-Host ""
Write-Host "  Done! utrains is installed." -ForegroundColor Green
if (-not (Get-Command utrains -ErrorAction SilentlyContinue)) {
    Write-Host "  If 'utrains' is not found, add this folder to your PATH:" -ForegroundColor Yellow
    Write-Host "     $scripts" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  Next:" -ForegroundColor Cyan
Write-Host "    - Local model:   utrains setup        (installs Ollama + pulls a model)"
Write-Host "    - GPT / Claude:  put OPENAI_API_KEY (or ANTHROPIC_API_KEY) in a .env file"
Write-Host "    - Start:         utrains"
Write-Host ""
