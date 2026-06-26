"""
The `utrains` command itself - argument parsing and the user-facing loop.

Usage shapes:
    utrains setup [--model NAME] [--yes]   # one-time install + model pull
    utrains "deploy the docker stack"      # run a single task
    utrains chat                           # interactive session
    utrains models                         # list locally available models
    utrains memory [show|on|off|clear|add <note>]   # manage what it remembers
    utrains mcp                            # list configured MCP servers + tools
    utrains doctor                         # show machine + Ollama health
    utrains version

Global flags for tasks:
    -y / --auto     run commands without asking (dangerous ones still confirm)
    --force         also auto-run dangerous commands (use with care)
    --model NAME    override the model for this run
    --dry-run       show the commands the agent would run, but don't run them
"""

import os
import sys
import textwrap
from pathlib import Path

from . import (__version__, agent, config, enable_utf8_output, executor, fun,
               installer, memory, ollama_client, ui)
from .mcp_client import MCPError, MCPManager
from .providers import detect_provider
from .system_info import system_summary

# Env var that holds the API key for each cloud provider.
_PROVIDER_KEY = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}

SUBCOMMANDS = {"setup", "chat", "models", "memory", "mcp", "doctor", "version", "help", "run"}

# ----- pretty output --------------------------------------------------------

def _is_noise(text: str) -> bool:
    """True for blank lines or pure spinner frames (- \\ | /) we don't want to show."""
    s = "".join(text.split())   # drop all whitespace (incl. internal)
    return not s or set(s) <= {"-", "\\", "|", "/"}


class StepRenderer:
    """
    Stateful renderer for one chat session.

    While a command runs it shows a SINGLE live status line (updated in place, so
    progress bars don't flood the screen). When the command finishes, short output
    is shown inline; long output is FOLDED to a one-line summary that the user can
    expand with `/output`.
    """

    FOLD_AFTER = 12          # lines; longer output gets folded

    def __init__(self):
        self.last_output = ""     # full output of the most recent command (for /output)
        self.actions: list[str] = []   # "`cmd` → first line of output" log for memory
        self._live = False        # is a status line currently on screen?
        self._cur_command = None
        self._challenges: set[str] = set()   # commands the student is fixing themselves

    # -- event sink handed to agent.run_task -------------------------------
    def on_event(self, kind: str, payload):
        if kind == "thought":
            dot = ui.style("●", "purple", "bold")
            print(f"\n{dot} {ui.style(payload['thought'], 'dim')}")
        elif kind == "command":
            self._cur_command = payload["command"]
            title = "Run (DANGEROUS)" if payload["dangerous"] else "Run command"
            color = "danger" if payload["dangerous"] else "accent"
            print()
            print(ui.box([ui.style(payload["command"], "bold")], title=title, color=color))
        elif kind == "tool":
            print()
            print(ui.box([f"{payload['tool']} {ui.style(str(payload['args']), 'dim')}"],
                         title="MCP tool", color="accent"))
        elif kind == "run_start":
            pass
        elif kind == "stream":
            self._status(payload["text"])
        elif kind == "exit":
            self._clear_status()
            output = payload.get("output", "")
            cmd = self._cur_command
            self._record_action(output)
            self._render_output(output, payload["returncode"])
            # Did the student just fix a command they were challenged on? Cheer.
            if payload["returncode"] == 0 and cmd in self._challenges:
                self._challenges.discard(cmd)
                print(ui.style(f"  {fun.pick(fun.PRAISE)}", "ok", "bold"))
        elif kind == "tool_result":
            self._clear_status()
            self._render_output(payload.get("output", "") or "", 0, exit_line=False)
        elif kind == "skipped":
            self._clear_status()
            print(ui.style("  └ skipped.", "dim"))
        elif kind == "explain":
            self._clear_status()
            wrapped = textwrap.wrap(payload["text"], ui.term_width() - 8) or [payload["text"]]
            print()
            print(ui.box([ui.style(l, "warn") for l in wrapped],
                         title="⚠ What went wrong", color="warn"))
        elif kind == "challenge":
            self._challenges.add(payload["command"])
        elif kind == "note":
            self._clear_status()
            print(ui.style(f"  {payload['text']}", "accent"))
        elif kind == "opened":
            self._clear_status()
            print(ui.style(f"  📂 opened {payload['path']}:{payload['line']} in VS Code "
                           "- take a look 👀", "accent"))
        elif kind == "error":
            self._clear_status()
            print(ui.style(f"  ✗ {payload}", "danger"))

    # -- remember what was run, so follow-ups reuse it instead of re-running --
    def _record_action(self, output: str):
        if not self._cur_command:
            return
        first = next((l.strip() for l in (output or "").splitlines() if l.strip()), "(no output)")
        self.actions.append(f"`{self._cur_command}` → {first[:100]}")
        del self.actions[:-10]   # keep the last 10
        self._cur_command = None

    def actions_context(self) -> str:
        """A memory block listing commands already run + their results this session."""
        if not self.actions:
            return ""
        return ("Commands already run this session and their results - reuse these "
                "answers, do NOT run the same command again for the same info:\n"
                + "\n".join(self.actions))

    # -- live single-line status (collapses progress bars / spinners) ------
    def _status(self, text: str):
        if not sys.stdout.isatty() or _is_noise(text):
            return
        line = text.strip()
        width = ui.term_width() - 6
        if len(line) > width:
            line = line[:width - 1] + "…"
        sys.stdout.write(f"\r  {ui.style('▌', 'accent')} {ui.style(line, 'dim')}\033[K")
        sys.stdout.flush()
        self._live = True

    def _clear_status(self):
        if self._live and sys.stdout.isatty():
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
        self._live = False

    # -- final output: inline if short, folded if long ---------------------
    def _render_output(self, output: str, returncode: int, exit_line: bool = True):
        self.last_output = output or ""
        bar = ui.style("▌", "accent")
        lines = [l for l in (output or "").splitlines() if l.strip()]
        tone = "ok" if returncode == 0 else "danger"

        if lines and len(lines) <= self.FOLD_AFTER:
            for l in lines:
                print(f"  {bar} {ui.style(l, 'dim')}")
        elif lines:
            print(f"  {bar} {ui.style(lines[0], 'dim')}")
            print(f"  {bar} " + ui.style(f"… {len(lines) - 1} more lines - type /output to view", "warn"))

        if exit_line:
            print(ui.style(f"  └ exit {returncode}", tone))


def _cwd_short() -> str:
    """Current directory with the home folder shown as ~."""
    cwd = Path.cwd()
    try:
        rel = cwd.relative_to(Path.home())
        return "~" if str(rel) == "." else "~/" + rel.as_posix()
    except ValueError:
        return cwd.as_posix()


def _git_branch() -> str | None:
    """The current git branch by reading .git/HEAD (no subprocess), or None."""
    here = Path.cwd()
    for parent in [here, *here.parents]:
        head = parent / ".git" / "HEAD"
        if head.exists():
            try:
                txt = head.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            return txt.rsplit("/", 1)[-1] if txt.startswith("ref:") else txt[:7]
        if (parent / ".git").is_file():   # worktree/submodule - skip cheaply
            return None
    return None


def _context_line(ai_on: bool, state: dict | None = None) -> str:
    """The dim status bar shown just above the prompt: where you are + AI state."""
    parts = [ui.style("📁 " + _cwd_short(), "dim")]
    branch = _git_branch()
    if branch:
        parts.append(ui.style("⎇ " + branch, "purple"))
    parts.append(ui.style("AI " + ("on" if ai_on else "off"), "ok" if ai_on else "warn"))
    if state and state.get("auto"):
        parts.append(ui.style("AUTO" + (" FORCE" if state.get("force") else ""), "danger"))
    if state and not state.get("coach", True):
        parts.append(ui.style("coach off", "warn"))
    return "  " + ui.style(" · ", "dim").join(parts)


def _run_shell(command: str, renderer) -> None:
    """Run a raw command directly (AI off), handling `cd` so the dir persists."""
    stripped = command.strip()
    if stripped == "cd" or stripped.startswith("cd "):
        target = stripped[2:].strip().strip('"').strip("'") or "~"
        expanded = os.path.expanduser(target)
        if not os.path.isabs(expanded) and not os.path.isdir(expanded):
            base = os.path.basename(target.replace("\\", "/").rstrip("/")) or target
            match = executor.closest_existing_dir(base)
            if match and input(ui.style(f"  Did you mean '{match}'? [Y/n] ", "accent")
                               ).strip().lower() in ("", "y", "yes"):
                target = match
        try:
            os.chdir(os.path.expanduser(target))
        except OSError as exc:
            print(ui.style(f"  ✗ {exc}", "danger"))
        return
    print()
    renderer._cur_command = None   # don't pollute the AI's action memory
    result = executor.run_command(
        command, on_output=lambda text, term: renderer.on_event("stream", {"text": text}),
        track_cwd=True)   # a `cd` inside a compound command still persists
    renderer.on_event("exit", {"returncode": result["returncode"], "output": result["stdout"]})


def _make_confirm(state: dict):
    """
    Approval prompt before each action - an arrow-navigable menu (↑/↓, numbers,
    Enter). Option 1 is the safe default (Run normally, Skip when dangerous).

    `state` is a live dict {"auto", "force"} so /auto can flip it mid-session:
    - auto + safe command           → run automatically
    - auto + dangerous + force       → run automatically (true hands-free YOLO)
    - auto + dangerous + NOT force   → SKIP and log it, so an unattended run keeps
                                       going instead of blocking on a wipe-the-disk
                                       command while you're asleep
    - not auto                       → ask (the interactive menu)

    Returns ("run"|"skip"|"stop", None), or ("say", text) when the user types a
    custom instruction to steer the agent instead of running the command.
    """
    def confirm(action: str, dangerous: bool):
        auto, force = state["auto"], state["force"]
        if auto:
            if not dangerous or force:
                return ("run", None)
            # dangerous + hands-free but not forced → don't run unattended; skip it
            print(ui.style(f"  ⚠ auto-skipped a DANGEROUS command (review later): {action}",
                           "warn"))
            return ("skip", None)
        if dangerous and not force:
            options = [("Run anyway (DANGEROUS)", "danger"), ("Skip", "ok"),
                       ("Stop task", "warn"), ("Type an instruction…", "accent")]
            default = 1   # highlight Skip for dangerous actions
        else:
            options = [("Run", "ok"), ("Skip", "dim"),
                       ("Stop task", "warn"), ("Type an instruction…", "accent")]
            default = 0
        idx = ui.select(options, default=default)
        if idx == 0:
            return ("run", None)
        if idx == 1:
            return ("skip", None)
        if idx == 2:
            return ("stop", None)
        text = input(ui.style("  › Tell utrains what to do instead: ", "accent")).strip()
        return ("say", text) if text else ("skip", None)
    return confirm


# ----- model / server preflight --------------------------------------------

def _resolve_model(override: str | None) -> str | None:
    """The model to use, or None if nothing is set up yet (→ first-run screen)."""
    model = override or config.get_model()
    if model:
        return model
    # No saved model: if a cloud key is present, just use that provider's default.
    if os.getenv("OPENAI_API_KEY"):
        return "gpt-4.1"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude-opus-4-8"
    return None   # nothing configured yet → caller shows the welcome/first-run screen


def _first_run_screen() -> None:
    """Friendly onboarding shown when no model and no API key are set up yet."""
    print()
    print(ui.style("  👋  Welcome to utrains!", "heading", "bold"))
    print(ui.style("  Pick how to power it, then run utrains again.", "dim"))
    print()
    print("  " + ui.style("1) Use GPT or Claude", "accent", "bold")
          + ui.style("  - fastest to start", "dim"))
    print(ui.style("       Add an API key to a .env file next to utrains:", "dim"))
    print("         OPENAI_API_KEY=sk-...")
    print(ui.style("         (or ANTHROPIC_API_KEY=sk-ant-...)", "dim"))
    print()
    print("  " + ui.style("2) Run a local model", "accent", "bold")
          + ui.style("  - private, offline, free", "dim"))
    print("       " + ui.style("utrains setup", "ok", "bold")
          + ui.style("   (installs Ollama and pulls a model)", "dim"))
    print()


def _preflight(model: str) -> bool:
    provider = detect_provider(model)
    if provider in _PROVIDER_KEY:           # cloud model - just needs its API key
        key = _PROVIDER_KEY[provider]
        if not os.getenv(key):
            print(ui.style(f"✗ {key} isn't set. Add it to your environment or "
                           f"~/.utrains/.env to use '{model}'.", "danger"))
            return False
        return True
    # local Ollama model
    if not ollama_client.is_running():
        print(ui.style("✗ Ollama server isn't reachable. Run `utrains setup` or `ollama serve`.", "danger"))
        return False
    if not ollama_client.has_model(model):
        print(ui.style(f"✗ Model '{model}' isn't pulled yet. Run: ollama pull {model}", "danger"))
        return False
    return True


def _start_mcp():
    """Load and start MCP servers if configured. Returns a manager or None."""
    manager = MCPManager().load()
    if not manager.has_servers():
        return None
    with ui.spinner("Starting MCP servers"):
        status = manager.start_all()
    for name, state in status.items():
        if state == "ok":
            count = len([t for t in manager.tool_specs() if t["name"].startswith(name + ".")])
            print(ui.style(f"  ⚙ MCP '{name}': {count} tool(s) ready", "ok"))
        else:
            print(ui.style(f"  ⚙ MCP '{name}': {state}", "warn"))
    return manager


# ----- the commands ---------------------------------------------------------

def cmd_task(task: str, *, model_override, auto, force, dry_run) -> int:
    model = _resolve_model(model_override)
    if model is None:
        _first_run_screen()
        return 1
    if not _preflight(model):
        return 1
    mode = "DRY-RUN" if dry_run else "live"
    print(f"{ui.style('utrains', 'accent', 'bold')} {ui.style('· model=' + model + ' · ' + mode, 'dim')}")
    manager = _start_mcp()
    renderer = StepRenderer()
    try:
        answer = agent.run_task(
            task, model, system_summary(),
            confirm=_make_confirm({"auto": auto, "force": force}),
            dry_run=dry_run, on_event=renderer.on_event,
            context=memory.build_context(), mcp_manager=manager, status=ui.spinner,
            coach=not auto,   # a one-shot/unattended task fixes rather than coaches
        )
    finally:
        if manager:
            manager.stop_all()
    print("\n" + ui.rule("Answer"))
    print(ui.md(answer))
    return 0


def cmd_chat(*, model_override, auto, force, dry_run, use_tui=False) -> int:
    model = _resolve_model(model_override)
    if model is None:
        _first_run_screen()
        return 1
    if not _preflight(model):
        return 1

    # Classic colourful line UI is the default; the full-screen Textual TUI is
    # opt-in via `utrains chat --tui`.
    if use_tui:
        try:
            from .tui import run_tui
        except ImportError:
            run_tui = None
            print(ui.style("(textual not installed - `pip install textual` for the TUI)", "dim"))
        if run_tui is not None:
            manager = _start_mcp()
            try:
                return run_tui(model, manager, auto, force, dry_run)
            finally:
                if manager:
                    manager.stop_all()

    print(ui.welcome())
    print("  " + ui.style(fun.pick(fun.GREETINGS), "purple"))
    mem_state = "on" if memory.is_enabled() else "off"
    print("  " + ui.style(f"model {model}  ·  memory {mem_state}  ·  {fun.pick(fun.TIPS)}", "dim"))
    print("  " + ui.style("tip: type /ai to toggle between AI and a plain shell.", "dim"))
    manager = _start_mcp()
    renderer = StepRenderer()
    conversation: list[dict] = []   # clean user/assistant turns for follow-up context
    ai_on = True
    # Live session flags so /auto and /coach can flip them mid-chat.
    state = {"auto": auto, "force": force, "coach": True}
    try:
        while True:
            try:
                # status bar (where you are, branch, AI state) above the prompt
                print("\n" + _context_line(ai_on, state))
                prompt = (ui.style("❯", "purple", "bold") if ai_on
                          else ui.style("$", "accent", "bold")) + " "
                task = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print(ui.style("\n" + fun.pick(fun.GOODBYES), "dim"))
                return 0
            # /status → ask the agent for a resume board (runs as a normal task)
            if task.lower() in ("/status", "status") and ai_on:
                task = agent.STATUS_REQUEST
            lower = task.lower()
            if lower in ("exit", "quit", "q", ""):
                print(ui.style(fun.pick(fun.GOODBYES), "dim"))
                return 0
            if lower in ("clear", "cls", "/clear"):
                ui.clear_screen()
                continue
            if lower in ("help", "?", "/help"):
                _print_chat_help()
                continue
            if lower in ("/models", "models"):
                cmd_models()
                continue
            if lower in ("/mcp", "mcp"):
                _print_mcp(manager)
                continue
            if lower in ("/output", "output", "/show"):
                if renderer.last_output.strip():
                    print(ui.style(renderer.last_output.rstrip(), "dim"))
                else:
                    print(ui.style("(no command output to show yet)", "dim"))
                continue
            parts = task.split(maxsplit=1)
            head = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            if head in ("/memory", "memory"):
                _memory_command(arg)
                continue
            if head in ("/model", "model"):
                model = _switch_model(model, arg or None)
                continue
            if head in ("/ai", "ai"):
                ai_on = {"on": True, "off": False}.get(arg.lower(), not ai_on)
                if ai_on:
                    print(ui.style("✓ AI ON - type a goal and I'll do it.", "ok"))
                else:
                    print(ui.style("✓ AI OFF - this is a plain shell now; "
                                   "type raw commands and I'll just run them.", "warn"))
                continue
            if head in ("/coach", "coach"):
                state["coach"] = {"on": True, "off": False}.get(arg.lower(), not state["coach"])
                if state["coach"]:
                    print(ui.style("✓ Coach ON - small slips become a nudge for you to "
                                   "fix yourself. 👀", "ok"))
                else:
                    print(ui.style("✓ Coach OFF - I'll just fix small errors myself.", "warn"))
                continue
            if head in ("/auto", "auto"):
                a = arg.lower()
                if a == "off":
                    state["auto"], state["force"] = False, False
                    print(ui.style("✓ Auto OFF - I'll ask before each command.", "ok"))
                elif a == "force":
                    state["auto"], state["force"] = True, True
                    print(ui.style("⚠ Auto FORCE - running EVERYTHING unattended, "
                                   "including dangerous commands. Use only in throwaway "
                                   "environments.", "danger"))
                else:   # "on" or bare
                    state["auto"], state["force"] = True, False
                    print(ui.style("✓ Auto ON - hands-free. Safe commands run on their "
                                   "own; dangerous ones are skipped & logged for you to "
                                   "review. Go get some sleep. 😴", "ok"))
                continue

            # AI off → run the typed text as a plain shell command, no agent.
            if not ai_on:
                _run_shell(task, renderer)
                continue

            remember = memory.is_enabled()
            context = memory.build_context()
            if remember and renderer.actions_context():
                context = (context + "\n\n" if context else "") + renderer.actions_context()
            try:
                answer = agent.run_task(
                    task, model, system_summary(),
                    confirm=_make_confirm(state),
                    dry_run=dry_run, on_event=renderer.on_event,
                    context=context,
                    mcp_manager=manager, status=ui.spinner,
                    chat_history=conversation if remember else None,
                    coach=state["coach"] and not state["auto"],
                )
            except KeyboardInterrupt:
                # Ctrl+C cancels just this task, not the whole session.
                print("\n" + ui.style("  └ cancelled.", "dim"))
                continue
            label = ui.style("⏺", "purple", "bold") + " "
            print("\n" + label + ui.md(answer))
            tag = fun.win_tag(answer)
            if tag:
                print("  " + ui.style(tag, "ok"))
            if remember:
                conversation.append({"role": "user", "content": task})
                conversation.append({"role": "assistant", "content": answer})
                del conversation[:-20]   # keep the last ~10 exchanges
    finally:
        if manager:
            manager.stop_all()


def _print_chat_help():
    print("\n" + ui.style("Just describe what you want done, e.g.:", "heading"))
    for example in ("show my running docker containers",
                    "create a python venv and install requests",
                    "what's my git status and the last 5 commits"):
        print(ui.style("  • ", "accent") + example)
    print("\n" + ui.style("Commands:", "heading"))
    rows = [
        ("/status", "status board of this session - what's done & what's next"),
        ("/ai [on|off]", "toggle AI; off = a plain shell (run raw commands)"),
        ("/coach [on|off]", "on (default): nudge you to fix small slips yourself"),
        ("/auto [on|off|force]", "hands-free: run without asking (force = even dangerous)"),
        ("/model [name]", "switch model (menu if no name)"),
        ("/models", "list local models"),
        ("/memory [on|off|show|clear|add <note>]", "manage memory"),
        ("/mcp", "list MCP servers and tools"),
        ("/output", "show the last command's full output"),
        ("clear", "clear the screen"),
        ("exit", "quit"),
    ]
    for cmd, desc in rows:
        print(f"  {ui.style(cmd, 'accent'):<48} {ui.style(desc, 'dim')}")
    print()


def _memory_command(arg: str) -> None:
    """Handle `/memory ...` in chat and `utrains memory ...` from the shell."""
    sub, _, rest = arg.partition(" ")
    sub = sub.lower()
    if sub in ("", "show"):
        state = "on" if memory.is_enabled() else "off"
        print(ui.style(f"memory is {state}.", "heading"))
        text = memory.load_text()
        print(text if text else ui.style("(no saved notes)", "dim"))
    elif sub == "on":
        memory.set_enabled(True)
        print(ui.style("✓ memory on.", "ok"))
    elif sub == "off":
        memory.set_enabled(False)
        print(ui.style("✓ memory off.", "ok"))
    elif sub == "clear":
        memory.clear()
        print(ui.style("✓ persistent notes cleared.", "ok"))
    elif sub == "add":
        if rest.strip():
            memory.add(rest.strip())
            print(ui.style("✓ noted.", "ok"))
        else:
            print(ui.style("usage: memory add <note>", "warn"))
    else:
        print(ui.style("usage: memory [show|on|off|clear|add <note>]", "warn"))


def _print_mcp(manager) -> None:
    if not manager or not manager.has_servers():
        print(ui.style("No MCP servers configured. See ~/.utrains/mcp.json.", "dim"))
        return
    for spec in manager.tool_specs():
        print(f"  {ui.style('⚙ ' + spec['name'], 'accent')} "
              f"{ui.style(spec.get('description', ''), 'dim')}")


def _switch_model(current: str, requested: str | None = None) -> str:
    """Change the model mid-session; pull it if needed. Returns the model to use."""
    new_model = requested or installer.choose_model(system_summary())
    if new_model == current:
        print(ui.style(f"Already using '{current}'.", "dim"))
        return current
    # Cloud model (claude-*/gpt-*): no pull, just verify the API key is present.
    if detect_provider(new_model) in _PROVIDER_KEY:
        if not _preflight(new_model):
            print(ui.style(f"Staying on '{current}'.", "dim"))
            return current
    elif not ollama_client.has_model(new_model):
        print(ui.style(f"Model '{new_model}' isn't pulled yet.", "warn"))
        answer = input("Pull it now? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes") or not installer.pull_model(new_model):
            print(ui.style(f"Staying on '{current}'.", "dim"))
            return current
    config.set_model(new_model)
    print(ui.style(f"✓ Switched to '{new_model}' (saved as your default).", "ok"))
    return new_model


def cmd_models() -> int:
    if not ollama_client.is_running():
        print(ui.style("✗ Ollama server isn't reachable. Run `utrains setup` or `ollama serve`.", "danger"))
        return 1
    models = ollama_client.list_models()
    current = config.get_model()
    if not models:
        print(ui.style("No models pulled yet. Run `utrains setup`.", "dim"))
        return 0
    print(ui.style("Locally available models:", "heading"))
    for m in models:
        marker = ui.style("  ← default", "accent") if m == current else ""
        print(f"  {ui.style('•', 'accent')} {m}{marker}")
    return 0


def cmd_mcp() -> int:
    manager = MCPManager().load()
    if not manager.has_servers():
        print(ui.style("No MCP servers configured.", "dim"))
        print("Create ~/.utrains/mcp.json - see the README for the format.")
        return 0
    try:
        status = manager.start_all()
        for name, state in status.items():
            tone = "ok" if state == "ok" else "warn"
            print(ui.style(f"⚙ {name}: {state}", tone))
        _print_mcp(manager)
    except MCPError as exc:
        print(ui.style(f"✗ {exc}", "danger"))
        return 1
    finally:
        manager.stop_all()
    return 0


def _row(label: str, value: str) -> str:
    return f"{ui.style(label.rjust(15), 'dim')}: {value}"


def cmd_doctor() -> int:
    info = system_summary()
    print(ui.style("utrains doctor", "heading", "bold"))
    print(ui.rule())
    for key, value in info.items():
        if key == "tools_installed":
            value = ", ".join(value) or "none"
        print(_row(key, str(value)))
    print(ui.rule())
    running = ollama_client.is_running()
    server_state = ui.style("reachable", "ok") if running else ui.style("NOT reachable", "danger")
    print(_row("ollama server", server_state))
    if running:
        print(_row("models", ", ".join(ollama_client.list_models()) or "none pulled"))
    print(_row("default model", config.get_model() or "not set (run setup)"))
    for provider, key in _PROVIDER_KEY.items():
        present = ui.style("key set", "ok") if os.getenv(key) else ui.style("no key", "dim")
        print(_row(provider, present))
    print(_row("memory", "on" if memory.is_enabled() else "off"))
    print(_row("mcp servers", f"{len(MCPManager().load().servers)} configured"))
    return 0


def _print_help() -> None:
    """A polished, colourful help screen for `utrains help` / -h / --help."""
    print(ui.banner())
    print("  " + ui.style("Tell it what you want; it runs the commands to do it.", "dim") + "\n")

    sections = [
        ("Run a task", [
            ('utrains "show running docker containers"', "do one task and stop"),
            ("utrains chat", "interactive session (colourful classic UI)"),
            ("utrains chat --tui", "full-screen Textual UI (experimental)"),
            ("utrains", "same as chat"),
        ]),
        ("Setup & info", [
            ("utrains setup", "install Ollama + pick/pull a model"),
            ("utrains doctor", "machine + Ollama/memory/MCP health"),
            ("utrains models", "list local models"),
            ("utrains version", "show version"),
        ]),
        ("Memory & tools", [
            ("utrains memory [on|off|show|clear|add <note>]", "what it remembers"),
            ("utrains mcp", "list MCP servers and their tools"),
        ]),
        ("Flags (with a task)", [
            ("-y / --auto", "run without asking (dangerous still confirm)"),
            ("--force", "also auto-run dangerous actions"),
            ("--model NAME", "use a specific model this run"),
            ("--dry-run", "show the plan, run nothing"),
        ]),
    ]
    for title, rows in sections:
        print(ui.style("  " + title, "heading", "bold"))
        for left, right in rows:
            print(f"    {ui.style(left, 'accent'):<52} {ui.style(right, 'dim')}")
        print()
    print(ui.style("  In chat, type ", "dim") + ui.style("/help", "accent")
          + ui.style(" for /model, /memory, /mcp and more.", "dim") + "\n")


def _launched_by_double_click() -> bool:
    """True if a frozen binary was started from Explorer/Finder (its own fresh
    console window), so we should hold the window open instead of vanishing."""
    if not getattr(sys, "frozen", False):
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            buf = (ctypes.c_uint * 4)()
            # Only THIS process attached to the console → it was double-clicked.
            return ctypes.windll.kernel32.GetConsoleProcessList(buf, 4) <= 1
        except Exception:
            return False
    return False


def main(argv: list[str] | None = None) -> int:
    """Entry point - runs the CLI and turns Ctrl+C into a clean exit."""
    enable_utf8_output()
    config.load_env()   # pull API keys from ~/.utrains/.env (and ./.env) if present
    hold = _launched_by_double_click()
    try:
        return _dispatch(argv)
    except KeyboardInterrupt:
        print("\n" + ui.style("Interrupted. Bye!", "dim"))
        return 130
    finally:
        if hold:
            try:
                input("\nPress Enter to close this window...")
            except (EOFError, KeyboardInterrupt):
                pass


def _dispatch(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    auto = _take_flag(argv, "-y") or _take_flag(argv, "--auto")
    force = _take_flag(argv, "--force")
    dry_run = _take_flag(argv, "--dry-run")
    use_tui = _take_flag(argv, "--tui")
    _take_flag(argv, "--classic")   # accepted (now the default); harmless no-op
    model_override = _take_option(argv, "--model")

    if not argv:
        return cmd_chat(model_override=model_override, auto=auto, force=force,
                        dry_run=dry_run, use_tui=use_tui)

    first = argv[0]

    if first in ("version", "--version", "-V"):
        print(f"utrains {__version__}")
        return 0
    if first in ("help", "--help", "-h"):
        _print_help()
        return 0
    if first == "setup":
        return installer.run_setup(model=model_override, auto=auto)
    if first == "models":
        return cmd_models()
    if first == "memory":
        _memory_command(" ".join(argv[1:]))
        return 0
    if first == "mcp":
        return cmd_mcp()
    if first == "doctor":
        return cmd_doctor()
    if first == "chat":
        return cmd_chat(model_override=model_override, auto=auto, force=force,
                        dry_run=dry_run, use_tui=use_tui)
    if first == "run":
        task = " ".join(argv[1:]).strip()
        if not task:
            print("Usage: utrains run \"<what you want done>\"")
            return 1
        return cmd_task(task, model_override=model_override, auto=auto, force=force, dry_run=dry_run)

    task = " ".join(argv).strip()
    return cmd_task(task, model_override=model_override, auto=auto, force=force, dry_run=dry_run)


# ----- tiny flag parsing (keeps us dependency-free) -------------------------

def _take_flag(argv: list[str], name: str) -> bool:
    if name in argv:
        argv.remove(name)
        return True
    return False


def _take_option(argv: list[str], name: str) -> str | None:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            value = argv[i + 1]
            del argv[i:i + 2]
            return value
        del argv[i]
    return None


if __name__ == "__main__":
    sys.exit(main())
