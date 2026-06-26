"""
The agent's "hands" — it actually runs the shell commands.

We run each command through the machine's native shell (PowerShell on Windows,
bash elsewhere) so that anything you could type yourself — docker, aws, az, gh,
python, git, kubectl, … — just works. Output is captured and handed back to the
agent so it can decide what to do next.

A short list of obviously dangerous patterns is flagged so the CLI can insist on
a confirmation before running them, even in automatic mode.
"""

import codecs
import difflib
import os
import platform
import re
import subprocess
import threading


def closest_existing_dir(name: str, base: str = ".") -> str | None:
    """The real sub-directory of `base` that best matches a loosely-typed `name`.

    Case-insensitive exact → single substring match → fuzzy. Returns None when
    nothing is a confident match (or the name is already real / ambiguous).
    """
    if not name:
        return None
    try:
        dirs = [e for e in os.listdir(base) if os.path.isdir(os.path.join(base, e))]
    except OSError:
        return None
    low = name.casefold()
    for d in dirs:                                   # case-insensitive exact
        if d.casefold() == low:
            return None if d == name else d
    subs = [d for d in dirs if low in d.casefold()]  # obvious partial match
    if len(subs) == 1:
        return subs[0]
    close = difflib.get_close_matches(name, subs or dirs, n=1, cutoff=0.6)
    return close[0] if close else None


_CD_RE = re.compile(
    r'^\s*(cd|chdir|sl|Set-Location|pushd)\s+(?:-Path\s+)?(["\']?)([^"\';|&]+)\2\s*$',
    re.IGNORECASE)


def resolve_cd_command(command: str) -> tuple[str, str | None]:
    """If `command` is a plain `cd` into a MISSING relative folder, rewrite it to
    the closest existing one. Returns (command, matched_name|None) — unchanged
    when it isn't a simple cd, the target already exists, or there's no match.
    """
    match = _CD_RE.match(command or "")
    if not match:
        return command, None
    verb, _, target = match.group(1), match.group(2), match.group(3).strip()
    expanded = os.path.expanduser(target)
    if os.path.isabs(expanded) or os.path.isdir(expanded):
        return command, None
    base = os.path.basename(target.replace("\\", "/").rstrip("/")) or target
    best = closest_existing_dir(base)
    return (f'{verb} "{best}"', best) if best else (command, None)

# After a tracked command we make the shell print its final directory on this
# sentinel line, so a `cd` (or pushd, Set-Location, etc.) PERSISTS to the next
# command — exactly like a real terminal. The line is stripped from the output
# the user/model sees.
_CWD_MARKER = "<<UTRAINS_CWD::"


def _split_cwd(text: str) -> tuple[str, str | None]:
    """Pull the trailing cwd-marker line out of output; return (clean_text, cwd)."""
    cwd = None
    kept = []
    for line in (text or "").splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(_CWD_MARKER) and stripped.endswith(">>"):
            cwd = stripped[len(_CWD_MARKER):-2].strip()
        else:
            kept.append(line)
    return "".join(kept), cwd


def _apply_cwd(new_cwd: str | None) -> None:
    """Move the utrains process into new_cwd so the change sticks for next time."""
    if new_cwd and os.path.isdir(new_cwd):
        try:
            os.chdir(new_cwd)
        except OSError:
            pass

# Commands that can wipe data or take a machine down. Matching ONE of these
# doesn't block anything — it just forces an extra "are you sure?" prompt.
DANGEROUS_PATTERNS = [
    r"\brm\s+-[a-z]*r[a-z]*f",      # rm -rf
    r"\brmdir\b.*\b/s\b",
    r"\bdel\b.*\b/[sq]\b",
    r"Remove-Item.*-Recurse",
    r"\bmkfs\b", r"\bdd\b\s+if=", r"\b:\(\)\s*\{",   # fork bomb
    r"\bformat\b\s+[a-z]:", r"\bshutdown\b", r"\breboot\b",
    r"DROP\s+(TABLE|DATABASE)", r"TRUNCATE\s+TABLE",
    r"\bgit\s+push\b.*--force", r"\b--force\b.*\bdelete\b",
    r">\s*/dev/sd", r"\bchmod\b\s+-R\s+777\s+/",
]


def is_dangerous(command: str) -> bool:
    """True if the command looks destructive enough to deserve a second look."""
    return any(re.search(p, command, re.IGNORECASE) for p in DANGEROUS_PATTERNS)


def shell_prefix() -> list[str]:
    """The shell invocation for this OS; the command string is appended to it."""
    if platform.system() == "Windows":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
    return ["/bin/bash", "-lc"]


def _wrap_for_shell(command: str, track_cwd: bool = False) -> list[str]:
    """Build the full shell invocation, forcing UTF-8 and fixing Windows aliases.

    When track_cwd is set, the command is followed by a print of the shell's
    final working directory (on the _CWD_MARKER line) so a `cd` carries over to
    the next command.
    """
    if platform.system() == "Windows":
        # Force UTF-8 (so Unicode like docker's "…" isn't mojibake) and drop
        # PowerShell's curl/wget ALIASES (which point at Invoke-WebRequest and
        # choke on Unix-style flags) so the real curl.exe / wget.exe are used.
        run_command_str = (
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "Remove-Item Alias:curl -Force -ErrorAction SilentlyContinue; "
            "Remove-Item Alias:wget -Force -ErrorAction SilentlyContinue; "
            + command
        )
        if track_cwd:
            run_command_str += f'; Write-Output "{_CWD_MARKER}$($PWD.Path)>>"'
    else:
        run_command_str = command
        if track_cwd:
            run_command_str += f'; printf "\\n{_CWD_MARKER}%s>>\\n" "$PWD"'
    return shell_prefix() + [run_command_str]


def run_command(command: str, timeout: int = 600, cwd: str | None = None,
                on_output=None, track_cwd: bool = False) -> dict:
    """
    Run one shell command and return what happened.

    Returns a dict with: returncode, stdout, stderr. A timeout reports code 124,
    and a missing shell reports code 127 — so the agent always gets a clean result
    instead of an exception.

    If on_output is given, it is called with each output line AS IT ARRIVES, so
    long-running commands (winget, docker pull, npm install…) stream live instead
    of going silent until they finish. The full output is still captured and
    returned for the agent to read.

    If track_cwd is set, a `cd` in the command PERSISTS: the process moves into
    the command's final directory so the next command starts there too.
    """
    invocation = _wrap_for_shell(command, track_cwd=track_cwd)
    run_cwd = cwd or (os.getcwd() if track_cwd else None)

    if on_output is None:
        try:
            proc = subprocess.run(invocation, capture_output=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, cwd=run_cwd,
                                  stdin=subprocess.DEVNULL)
            stdout = proc.stdout or ""
            if track_cwd:
                stdout, new_cwd = _split_cwd(stdout)
                _apply_cwd(new_cwd)
            return {"returncode": proc.returncode,
                    "stdout": stdout, "stderr": proc.stderr or ""}
        except subprocess.TimeoutExpired:
            return {"returncode": 124, "stdout": "",
                    "stderr": f"Command timed out after {timeout}s."}
        except FileNotFoundError as exc:
            return {"returncode": 127, "stdout": "", "stderr": f"Shell not found: {exc}"}

    # Streaming mode: read RAW bytes so carriage returns (progress bars) are
    # preserved, and forward each segment with its terminator ("\r" = in-place
    # update, "\n" = a finished line). Only "\n" lines are captured for the model
    # so its view stays clean of progress spam.
    try:
        proc = subprocess.Popen(invocation, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                cwd=run_cwd, bufsize=0)
    except FileNotFoundError as exc:
        return {"returncode": 127, "stdout": "", "stderr": f"Shell not found: {exc}"}

    timed_out = {"hit": False}

    def _kill():
        timed_out["hit"] = True
        try:
            proc.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout, _kill)
    timer.start()
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    captured: list[str] = []
    captured_cwd = {"path": None}
    buf = ""
    pending_cr = False   # saw a '\r' — wait one char to tell CRLF from a lone CR

    def _finish_line(line: str) -> None:
        # The cwd marker line is swallowed (used to persist `cd`), never shown.
        if track_cwd and line.strip().startswith(_CWD_MARKER):
            captured_cwd["path"] = line.strip()[len(_CWD_MARKER):].rstrip(">").strip()
            return
        on_output(line, "\n")
        captured.append(line + "\n")

    try:
        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            for ch in decoder.decode(chunk):
                if pending_cr:
                    pending_cr = False
                    if ch == "\n":
                        _finish_line(buf)      # "\r\n" — a real (Windows) line ending
                        buf = ""
                        continue
                    # lone "\r" — an in-place progress redraw; show, don't capture
                    on_output(buf, "\r")
                    buf = ""
                if ch == "\r":
                    pending_cr = True
                elif ch == "\n":
                    _finish_line(buf)
                    buf = ""
                else:
                    buf += ch
        if buf:                       # trailing partial line at EOF
            _finish_line(buf)
        proc.wait()
    finally:
        timer.cancel()
        proc.stdout.close()

    if track_cwd:
        _apply_cwd(captured_cwd["path"])

    if timed_out["hit"]:
        return {"returncode": 124, "stdout": "".join(captured),
                "stderr": f"Command timed out after {timeout}s."}
    return {"returncode": proc.returncode, "stdout": "".join(captured), "stderr": ""}