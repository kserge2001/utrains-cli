"""
The reasoning loop — utrains' "brain wiring".

It runs the classic agent cycle against a LOCAL model:

    think → propose an action → (you approve) → run it → read output → repeat

An "action" is normally a shell command, but when MCP is configured it can also
be an MCP tool call. The model answers in a small JSON contract (see prompts.py)
so we can reliably pull out one action at a time, run it, and feed the result
back as the next observation. The loop ends when the model says it's done (or
hits the step limit).
"""

import contextlib
import json
import os
import re
import shutil
import subprocess

from . import executor, prompts
from .providers import ProviderError, chat


# Patterns that point at a specific file + line in error output, most specific
# first. The matched file is always existence-checked before we act on it, so a
# loose match (e.g. a "host:port") that isn't a real file is simply ignored.
_FILE_LINE_PATTERNS = [
    re.compile(r'File "([^"]+)", line (\d+)'),                       # Python traceback
    re.compile(r'on\s+(\S+)\s+line\s+(\d+)'),                        # Terraform / HCL
    re.compile(r'([A-Za-z0-9_.\-/\\]+\.[A-Za-z0-9]+):(\d+)'),        # path:line (linters, gcc…)
]


def _find_file_line(text: str) -> tuple[str | None, int | None]:
    """Pull the first (file, line) reference out of error text, if any."""
    for pattern in _FILE_LINE_PATTERNS:
        match = pattern.search(text or "")
        if match:
            try:
                return match.group(1), int(match.group(2))
            except (ValueError, IndexError):
                continue
    return None, None


def _open_in_vscode(result: dict, emit) -> None:
    """If the error names a real file:line and VS Code's `code` CLI is on PATH,
    open that file at the offending line so the student SEES what's wrong."""
    code_cli = shutil.which("code")
    if not code_cli:
        return
    text = (result.get("stderr") or "") + "\n" + (result.get("stdout") or "")
    path, line = _find_file_line(text)
    if not path:
        return
    full = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
    if not os.path.isfile(full):
        return   # loose match that isn't an actual file — ignore it
    try:
        subprocess.Popen(f'code -g "{full}:{line}"', shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        emit("opened", {"path": path, "line": line})
    except OSError:
        pass


def _parse_step(raw: str) -> dict:
    """Turn the model's reply into a step dict, staying defensive about stray prose."""
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {"thought": "", "command": None, "done": True, "final_answer": raw}


def _decide(value) -> tuple[str, str | None]:
    """
    Normalise a confirm() result into (kind, text).

    Accepts a plain bool (True→run, False→skip) for simple callers/tests, or a
    ("run"|"skip"|"stop"|"say", text) tuple from the interactive CLI.
    """
    if value is True:
        return ("run", None)
    if value is False:
        return ("skip", None)
    if isinstance(value, tuple) and value:
        return (value[0], value[1] if len(value) > 1 else None)
    return ("run", None)


def _natural_reply(model: str, task: str, context: str) -> str:
    """
    Generate a plain-language reply for a conversational turn.

    Used when the model finishes without writing a final_answer (e.g. it answers
    "hi" with an empty JSON object) — instead of a blank "Done." we ask it again,
    without the JSON straitjacket, to just talk to the user.
    """
    system = ("You are utrains, a friendly local terminal assistant. Reply to the "
              "user's message in 1-2 short, natural sentences. Plain text only — no JSON.")
    if context.strip():
        system += "\n\nThings to keep in mind:\n" + context.strip()
    try:
        return chat(model, [{"role": "system", "content": system},
                            {"role": "user", "content": task}], force_json=False).strip()
    except ProviderError:
        return ""


def _coach_error(model: str, command: str, result: dict) -> dict | None:
    """
    Look at a failed command and decide how to teach it.

    Returns {"kind": "tiny"|"real", "hint": "..."} or None.

    - "tiny": a small, learnable slip a DevOps student should fix THEMSELVES — a
      missing quote/bracket/comma, a typo, a wrong name or casing, bad
      indentation. The hint is a playful nudge that points at the spot and the
      KIND of thing to look for, WITHOUT giving the fix away.
    - "real": anything bigger (missing tool, network, permission, logic, config
      that needs real work). The hint plainly explains what's wrong, beginner-
      friendly, because utrains will go on to fix it.
    """
    err = (result.get("stderr") or "").strip() or (result.get("stdout") or "").strip()
    if not err:
        return None
    system = (
        "You are utrains, a warm, PLAYFUL DevOps coach for a complete beginner. A "
        "command just failed. Reply with ONLY a JSON object:\n"
        '{ "kind": "tiny" or "real", "hint": "<one or two short sentences>" }\n\n'
        "Choose kind='tiny' for a small, learnable slip the student should fix "
        "THEMSELVES: a missing or mismatched quote/bracket/comma, a typo, a wrong "
        "name or casing, bad indentation. For 'tiny', the hint must be fun and "
        "encouraging, point them to the offending line and the KIND of thing to "
        "hunt for (e.g. 'a quote that opened but never closed'), and must NOT give "
        "the exact fix — let them find it. Emoji welcome.\n"
        "Choose kind='real' for anything bigger (missing tool, network, "
        "permissions, broken logic, real config work). For 'real', the hint "
        "plainly explains what went wrong in beginner language.\n"
        "JSON only — no markdown, no code fences."
    )
    user = f"Command that failed:\n{command}\n\nError output:\n{err[:1500]}"
    try:
        raw = chat(model, [{"role": "system", "content": system},
                           {"role": "user", "content": user}], force_json=True)
    except ProviderError:
        return None
    data = _parse_step(raw)   # tolerant JSON parse (handles stray prose)
    hint = (data.get("hint") or "").strip()
    if not hint:
        return None
    kind = "tiny" if str(data.get("kind", "")).strip().lower() == "tiny" else "real"
    return {"kind": kind, "hint": hint}


_NEEDS_COMMANDS = "NEEDS_COMMANDS"

# What `/status` asks for — a Copilot-style "where we left off" board.
STATUS_REQUEST = (
    "Give me a status board to resume this session from. Use ONLY what actually "
    "happened in our conversation so far — do NOT run new commands unless truly "
    "necessary. Format it as: a short `## heading`, then a Markdown table of the "
    "steps with a Status column using badges (✅ done · ▶ next · ⏳ pending), then "
    "the next command to run in a fenced ``` code block. End with one short line "
    "asking if I'm ready to continue."
)


def _route(model: str, task: str, context: str, chat_history) -> str | None:
    """
    Action-first gate (this is a TERMINAL — doing beats chatting).

    Returns None for anything that could be run/built/inspected on the machine, so
    the caller drops into the agentic command loop — that's the default. Only when
    the message clearly needs no system access (a greeting, something already
    answered this session, a general-knowledge question) does it return a plain
    text answer, skipping the command loop.

    A quick non-JSON chat call still decides this, so greetings stay snappy and we
    don't re-run commands for things already known — but the bias is to act.
    """
    system = (
        "You are utrains, an agent at the user's TERMINAL. People open a terminal "
        "to DO things — run commands, inspect the system, build, install, deploy, "
        "fix. So your DEFAULT is to TAKE ACTION, not to chat.\n\n"
        f"Reply with exactly the single token {_NEEDS_COMMANDS} whenever the message "
        "could be accomplished or answered by running something on this machine — "
        "listing, inspecting, creating, editing, installing, building, testing, "
        "deploying, fixing, 'show me X', 'what's my Y', or even a bare noun/phrase "
        "like 'the logs', 'git status', 'disk space'. WHEN IN DOUBT, CHOOSE THIS.\n"
        "Also choose it for any imperative to run/re-run/retry, and for any "
        "validate/test/build/lint/status check (its result changes when files "
        "change, so a past run never answers it — never say you already ran it).\n\n"
        "ONLY answer directly in words (no commands) when the message clearly needs "
        "NO system access:\n"
        "- a greeting, thanks, or small talk,\n"
        "- a question you ALREADY answered or ran earlier THIS session,\n"
        "- a general-knowledge / 'how does X work' / explain-this question,\n"
        "- the user explicitly just wants to talk or asks what you can do.\n\n"
        f"Reply with EITHER a normal answer OR exactly {_NEEDS_COMMANDS} — nothing else."
    )
    if context.strip():
        system += "\n\nWhat you already know this session:\n" + context.strip()
    messages = [{"role": "system", "content": system}]
    messages.extend(chat_history or [])
    messages.append({"role": "user", "content": task})
    try:
        reply = chat(model, messages, force_json=False).strip()
    except ProviderError:
        return None   # let the agent loop run; it will surface any real error
    if not reply or reply.upper().startswith(_NEEDS_COMMANDS):
        return None
    return reply


def run_task(task: str, model: str, system: dict, *, confirm, dry_run: bool = False,
             max_steps: int = 25, on_event=None, context: str = "",
             mcp_manager=None, status=None, chat_history=None, coach: bool = True) -> str:
    """
    Drive one goal to completion.

    confirm   – callback(action, is_dangerous) -> bool, decides if we run it.
    context   – persistent memory text injected into the prompt.
    mcp_manager – optional MCPManager whose tools the agent may call.
    status    – optional factory status(text) -> context manager (a spinner).
    chat_history – prior clean (user/assistant) turns from this chat session, so
                   follow-up questions keep their context across prompts.
    """
    def emit(kind: str, payload):
        if on_event:
            on_event(kind, payload)

    def wait(text: str):
        return status(text) if status else contextlib.nullcontext()

    mcp_tools = mcp_manager.tool_specs() if mcp_manager and mcp_manager.has_servers() else []

    # Seed with prior conversation turns so follow-ups have context. The verbose
    # command/observation turns from THIS task get appended below but are not
    # carried back to the chat loop (it only keeps clean question/answer pairs).
    history: list[dict] = list(chat_history or [])
    recent_commands: list[str] = []     # loop guard against repeated commands
    consecutive_failures = 0            # loop guard against flailing on failures
    original_task = task                # for the conversational fallback
    did_act = False                     # did we ever run a command/tool this task?
    next_input = task

    # Answer-first: try a plain chat reply. Only if the task genuinely needs the
    # machine do we enter the agentic command loop below.
    with wait("Thinking"):
        direct = _route(model, original_task, context, chat_history)
    if direct is not None:
        return direct

    for step in range(1, max_steps + 1):
        messages = prompts.build_messages(system, next_input, history, context, mcp_tools)
        try:
            with wait("Thinking"):
                reply = chat(model, messages, force_json=True)
        except ProviderError as exc:
            emit("error", str(exc))
            return f"utrains could not reach the model: {exc}"

        history.append({"role": "user", "content": next_input})
        history.append({"role": "assistant", "content": reply})

        step_data = _parse_step(reply)
        thought = step_data.get("thought") or ""
        command = step_data.get("command")
        tool = step_data.get("tool")
        done = bool(step_data.get("done"))

        # --- finished (a plain answer, no command to run) -----------------
        # Return BEFORE printing a "Step" line, so greetings and direct answers
        # don't get a spurious "Step 1" header.
        is_tool_call = bool(not done and tool and mcp_tools)
        if not is_tool_call and (done or not command):
            final = (step_data.get("final_answer") or "").strip()
            if final:
                return final
            if not did_act:
                # Conversational turn with no answer (e.g. "hi" → {}). Generate a
                # real reply instead of a blank "Done.".
                with wait("Thinking"):
                    reply_text = _natural_reply(model, original_task, context)
                if reply_text:
                    return reply_text
            return thought or "Done."

        # We're about to act — now the step header makes sense.
        emit("thought", {"step": step, "thought": thought})

        # --- MCP tool call ------------------------------------------------
        if is_tool_call:
            did_act = True
            args = step_data.get("tool_args") or {}
            emit("tool", {"step": step, "tool": tool, "args": args})
            if dry_run:
                observation = "(dry-run: tool not called)"
            else:
                kind, said = _decide(confirm(f"[MCP] {tool} {json.dumps(args)}", False))
                if kind == "stop":
                    emit("error", "Stopped by user.")
                    return "Stopped at your request."
                if kind == "say":
                    emit("skipped", {"command": tool})
                    next_input = f'The user did NOT run the tool. They said: "{said}". Re-plan.'
                    continue
                if kind == "skip":
                    observation = "User skipped this tool."
                    emit("skipped", {"command": tool})
                else:
                    try:
                        with wait(f"Calling {tool}"):
                            observation = mcp_manager.call(tool, args)
                        emit("tool_result", {"tool": tool, "output": observation})
                    except Exception as exc:  # noqa: BLE001 - surface MCP failure to the model
                        observation = f"MCP tool error: {exc}"
                        emit("error", observation)
            next_input = prompts.observation_text(f"TOOL RESULT ({tool}):", observation)
            continue

        # --- shell command ------------------------------------------------
        # Auto-resolve a `cd` into a loosely-named folder to the real one, so the
        # user approves the corrected command (similarity match + confirm).
        command, cd_fix = executor.resolve_cd_command(command)
        if cd_fix:
            emit("note", {"text": f"↪ matched that to '{cd_fix}'"})
        did_act = True
        dangerous = executor.is_dangerous(command)
        emit("command", {"step": step, "command": command, "dangerous": dangerous})

        # Loop guard: if the model keeps proposing the same command, give up
        # rather than spinning forever.
        recent_commands.append(command)
        if recent_commands.count(command) >= 3:
            emit("error", "Already ran that command — not repeating it.")
            return (f"I've already run `{command}` a couple of times this turn and keep "
                    "getting the same result, so I won't run it again. If that output "
                    "showed errors, those are the real thing to fix — tell me to fix "
                    "them, or let me know what you'd like to do next.")

        if dry_run:
            result = {"returncode": 0, "stdout": "(dry-run: not executed)", "stderr": ""}
            emit("exit", {"returncode": 0, "output": result["stdout"]})
        else:
            kind, said = _decide(confirm(command, dangerous))
            if kind == "stop":
                emit("error", "Stopped by user.")
                return "Stopped at your request."
            if kind == "say":
                emit("skipped", {"command": command})
                next_input = (f'The user did NOT run that command. Instead they said: '
                              f'"{said}". Re-plan based on this.')
                continue
            if kind == "skip":
                result = {"returncode": 1, "stdout": "", "stderr": "User skipped this command."}
                emit("skipped", {"command": command})
            else:
                # Stream output live so long commands don't go silent. track_cwd
                # makes a `cd` persist to the next command, like a real terminal.
                result = executor.run_command(
                    command, on_output=lambda text, term: emit("stream", {"text": text}),
                    track_cwd=True)
                emit("exit", {"returncode": result["returncode"], "output": result["stdout"]})
                # Track a streak of failures so we don't flail forever trying
                # one broken approach after another.
                if result.get("returncode"):
                    consecutive_failures += 1
                    # Teach first. For a small slip, nudge the student to the line
                    # and let THEM fix it; for a real problem, explain and fix.
                    with wait("Reading the error"):
                        coached = _coach_error(model, command, result)
                    if coached:
                        if coach and coached["kind"] == "tiny":
                            # Coaching: send them to the line and let THEM fix it.
                            _open_in_vscode(result, emit)
                            emit("challenge", {"command": command})
                            return "👀  " + coached["hint"]
                        # Coach off (or a real problem): explain and fix it ourselves.
                        emit("explain", {"command": command, "text": coached["hint"]})
                else:
                    consecutive_failures = 0
                if consecutive_failures >= 4:
                    emit("error", "Several commands failed in a row — stopping.")
                    last_err = (result.get("stderr") or result.get("stdout") or "").strip()
                    return ("I tried several approaches but commands kept failing on this "
                            "system, so I stopped. Last error:\n" + last_err[:400]
                            + "\n\nTell me how you'd like to proceed, or try rephrasing.")

        next_input = prompts.command_observation(command, result)

    emit("error", f"Reached the {max_steps}-step limit without finishing.")
    return (f"Stopped after {max_steps} steps. The task may be partly done — "
            f"re-run utrains with a more specific request to continue.")
