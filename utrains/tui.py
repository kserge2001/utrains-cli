"""
Textual TUI for utrains - a Cursor/Copilot-style chat interface.

This is the rich front-end: a scrollable conversation, command output folded into
collapsible panels you can expand/collapse (and click), a status line, and a
docked input box at the bottom. The agent/executor/MCP/memory core is unchanged -
this module only renders it.

The agent runs in a background worker thread; UI updates are marshalled back with
call_from_thread, and the approval prompt blocks that worker on a threading.Event
until you click (or key) a choice.

Falls back gracefully: if Textual isn't installed, cli.py uses the classic
line-based chat instead.
"""

from __future__ import annotations

import os
import threading

from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Collapsible, Input, Link, Markdown, OptionList, Static

from . import agent, executor, fun, memory
from .system_info import system_summary


def _is_noise(text: str) -> bool:
    """Blank lines / pure spinner frames we keep out of the panels."""
    s = "".join(text.split())   # drop all whitespace (incl. internal)
    return not s or set(s) <= {"-", "\\", "|", "/"}


class PasteInput(Input):
    """A single-line Input that keeps the WHOLE multi-line paste.

    Textual's default Input drops everything after the first newline on paste.
    Here we flatten newlines to spaces so the entire pasted block is captured
    (shown on one line, exactly like other terminals do).
    """

    def _on_paste(self, event: events.Paste) -> None:
        if event.text:
            # Collapse ALL whitespace runs (newlines, tabs, indentation) to single
            # spaces so pasted code doesn't sprawl with big gaps.
            text = " ".join(event.text.split())
            if self.selection.is_empty:
                self.insert_text_at_cursor(text)
            else:
                self.replace(text, *self.selection)
        event.stop()


class UtrainsApp(App):
    """The utrains chat, as a terminal app."""

    _SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    _PULSE = ["#7e6aa6", "#8f74b8", "#9c84c4", "#a892cc", "#9c84c4", "#8f74b8"]

    CSS = """
    Screen { background: #14161b; }

    /* scrollbars: applied to every scrollable region so none stay default-blue */
    VerticalScroll, OptionList, Collapsible, Markdown {
        scrollbar-background: #14161b;
        scrollbar-background-hover: #14161b;
        scrollbar-background-active: #14161b;
        scrollbar-color: #2f323b;
        scrollbar-color-hover: #4a4e58;
        scrollbar-color-active: #5fa093;
        scrollbar-size-vertical: 1;
    }

    #log { padding: 0 1; }

    .welcome-ad { color: #c9b8ff; text-style: bold; padding: 0 1; margin: 1 0 0 0; }
    .welcome-contact { height: auto; padding: 0 1 1 1; }
    .welcome-contact Static { width: auto; color: #5fd1bf; }
    .welcome-contact Link { width: auto; color: #5fd1bf; }
    .welcome-contact Link:hover { color: #9fe9d8; text-style: underline; }
    .welcome { border: round #5fa093; padding: 0 1; margin: 0 0 1 0; height: auto; max-width: 100; background: #1b1f26; }
    .welcome-tag { color: #9aa3b3; }
    .welcome-cmds { color: #5fd1bf; }
    .welcome-greet { padding: 0 1; height: 1; }
    .user { color: #5fd1bf; text-style: bold; margin-top: 1; max-width: 100; }
    .thought { color: #9aa3b3; }
    .command { border: round #5fa093; padding: 0 1; color: #e3e7ee; background: #1b1f26; width: auto; max-width: 100; }
    .command.danger { border: round #d08a8a; }
    .answer { margin: 1 0; padding: 0; color: #e3e7ee; max-width: 100; }
    /* Markdown answers, themed to the teal/violet palette (Copilot-style) */
    .answer MarkdownH1, .answer MarkdownH2, .answer MarkdownH3 {
        color: #c9b8ff; text-style: bold; background: #14161b; margin: 0; }
    .answer MarkdownFence { background: #1b1f26; color: #5fd1bf; border-left: wide #5fa093; }
    .answer MarkdownTable { background: #14161b; }
    .answer MarkdownTable > DataTable { background: #14161b; }
    .answer DataTable > .datatable--header { background: #1b1f26; color: #c9b8ff; text-style: bold; }
    .answer DataTable > .datatable--cursor { background: #14161b; }
    .skipped { color: #9aa3b3; }
    .win { color: #7fcf9a; }
    .explain { color: #e9c46a; border-left: wide #e9c46a; padding: 0 1; margin: 1 0; background: #211d12; max-width: 100; }
    .opened { color: #5fd1bf; padding: 0 1; }

    .confirm-box { border: round #5fa093; padding: 0 1; margin: 1 0; height: auto; width: auto; background: #1b1f26; }
    .confirm-box.danger { border: round #d08a8a; }
    .confirm-title { color: #e3e7ee; text-style: bold; }
    #c_opts { background: #1b1f26; height: auto; width: auto; border: none; padding: 0; }
    #c_opts > .option-list--option { padding: 0 1; color: #c2c9d6; }
    #c_opts > .option-list--option-highlighted { background: #4fb3a1; color: #10141a; text-style: bold; }
    #c_opts:focus > .option-list--option-highlighted { background: #4fb3a1; color: #10141a; text-style: bold; }

    #status { height: 1; padding: 0 1; color: #9aa3b3; }
    #prompt { dock: bottom; border: round #5fa093; background: #1b1f26; color: #e3e7ee; }

    Collapsible { margin: 0 0 1 0; border: none; border-left: wide #3a3e48; background: #14161b; height: auto; }
    Collapsible > Contents { background: #14161b; padding: 0 0 0 1; height: auto; }
    CollapsibleTitle { background: #1b1f26; color: #c2c9d6; padding: 0 1; }
    CollapsibleTitle:hover { background: #262b34; color: #5fd1bf; }
    CollapsibleTitle:focus { background: #262b34; color: #5fd1bf; text-style: bold; }
    .outbody { color: #c2c9d6; padding: 0 1; height: auto; }
    """

    BINDINGS = [("ctrl+c", "quit", "Quit"), ("ctrl+l", "clear_log", "Clear")]

    def __init__(self, model, manager=None, auto=False, force=False, dry_run=False):
        super().__init__()
        self.model = model
        self.manager = manager
        self.auto = auto
        self.force = force
        self.coach = True   # nudge the student to fix small slips themselves
        self.dry_run = dry_run
        self.system = system_summary()
        self.conversation: list[dict] = []
        self.last_output = ""
        self.actions: list[str] = []        # commands run + results, for follow-up memory
        self._cur_command = None
        self._challenges: set[str] = set()  # commands the student is fixing themselves
        self.ai_on = True                   # False = plain shell passthrough
        self._busy = False
        self._await_say = False
        self._confirm_event: threading.Event | None = None
        self._confirm_result = None
        self._confirm_row = None
        # animation state
        self._tick = 0
        self._pulse_i = 0
        self._phrase = fun.pick(fun.THINKING)
        self._status_detail = ""
        self._active_thought = None   # (widget, text) of the pulsing current step

    # -- layout ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        from . import ui
        yield Static(f"🎓  [b]{ui.BRAND_SLOGAN}[/]", classes="welcome-ad")
        tel = "tel:" + "".join(c for c in ui.BRAND_PHONE if c.isdigit() or c == "+")
        with Horizontal(classes="welcome-contact"):
            yield Static("🌐 ")
            yield Link(ui.BRAND_WEB, url=ui.BRAND_WEB)         # opens on a plain click
            yield Static("    ☎ ")
            yield Link(ui.BRAND_PHONE.strip(), url=tel)
        with Vertical(classes="welcome"):
            yield Static("", id="banner")
            yield Static("Tell me what you want - I'll run the commands to do it.",
                         classes="welcome-tag")
            yield Static("/status · /ai · /coach · /auto · /model · /memory · /output · exit",
                         classes="welcome-cmds")
        yield Static("", id="greeting", classes="welcome-greet")
        yield VerticalScroll(id="log")
        yield Static("", id="status")
        yield PasteInput(placeholder="Tell me what to do…", id="prompt")

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        # animation timers: spinner, the title shimmer, and the typewriter intro.
        self.set_interval(0.09, self._spin_tick)
        self.set_interval(0.1, self._pulse_tick)
        self._greet = fun.pick(fun.GREETINGS)
        self._greet_i = 0
        self._greet_done = False
        self.set_interval(0.05, self._type_greeting)
        self._idle_status()   # show the context bar (dir · branch · AI state)

    def _type_greeting(self) -> None:
        """Reveal the greeting character by character with a blinking cursor."""
        if self._greet_done:
            return
        if self._greet_i <= len(self._greet):
            shown = self._greet[:self._greet_i]
            cursor = "[#5fd1bf]▋[/]" if self._greet_i < len(self._greet) else ""
            self.query_one("#greeting", Static).update(f"[#b08cdd]{shown}[/]{cursor}")
            self._greet_i += 1
        else:
            self._greet_done = True
            mem = "on" if memory.is_enabled() else "off"
            self.query_one("#greeting", Static).update(
                f"[#b08cdd]{self._greet}[/]   "
                f"[dim]model {self.model} · memory {mem}[/]  [#a8e063]✨[/]")

    # -- animation ---------------------------------------------------------
    def _spin_tick(self) -> None:
        if not self._busy or self._await_say:
            return
        self._tick += 1
        if self._tick % 16 == 0:
            self._phrase = fun.pick(fun.THINKING)
        frame = self._SPIN[self._tick % len(self._SPIN)]
        base = self._status_detail or (self._phrase + "…")
        self.query_one("#status", Static).update(f"[#9c84c4]{frame}[/] {base}")
        # pulse the current step's dot through the purple ramp
        if self._active_thought is not None:
            widget, text = self._active_thought
            color = self._PULSE[self._tick % len(self._PULSE)]
            widget.update(f"[{color}]●[/] {text}")

    _GLOBE = ["🌍", "🌎", "🌏"]   # spinning-globe frames (the brand globe)

    def _pulse_tick(self) -> None:
        # Animated logo (grad cap + spinning globe) + a highlight sweeping the title.
        self._pulse_i += 1
        globe = self._GLOBE[(self._pulse_i // 3) % 3]
        title = "Utrains · local terminal agent"
        head = self._pulse_i % (len(title) + 8)
        out = []
        for i, ch in enumerate(title):
            if 0 <= head - i <= 2:
                out.append(f"[#e8dcff]{ch}[/]")     # bright sweep window
            elif ch.strip():
                out.append(f"[#9678c3]{ch}[/]")      # base purple
            else:
                out.append(ch)
        self.query_one("#banner", Static).update(f"🎓{globe} " + "".join(out))

    def _settle_active(self, color: str = "#7fcf9a") -> None:
        """Freeze the current step's dot to a solid colour (green = done)."""
        if self._active_thought is not None:
            widget, text = self._active_thought
            widget.update(f"[{color}]●[/] {text}")
            self._active_thought = None

    # -- mounting helpers (UI thread) --------------------------------------
    def _write(self, widget):
        log = self.query_one("#log", VerticalScroll)
        log.mount(widget)
        # Scroll AFTER the new widget is laid out, so the view always follows the
        # latest content instead of leaving it buried below the fold.
        self.call_after_refresh(self._scroll_bottom)
        return widget

    def _scroll_bottom(self) -> None:
        self.query_one("#log", VerticalScroll).scroll_end(animate=False)

    def action_clear_log(self) -> None:
        self.query_one("#log", VerticalScroll).remove_children()

    # -- input -------------------------------------------------------------
    def on_paste(self, event: events.Paste) -> None:
        """Route a paste into the prompt when it isn't focused (PasteInput handles
        the focused case itself, keeping the full multi-line text)."""
        inp = self.query_one("#prompt", Input)
        if inp.has_focus:
            return   # PasteInput._on_paste already captured it
        text = " ".join(event.text.split())
        if not text:
            return
        inp.focus()
        inp.insert_text_at_cursor(text)
        event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#prompt", Input).value = ""

        # Mid-task: the user is typing a steering instruction for the agent.
        if self._await_say:
            self._await_say = False
            self.query_one("#status", Static).update("⏳ working…")
            self._confirm_result = ("say", text)
            if self._confirm_event:
                self._confirm_event.set()
            return

        if self._busy or not text:
            return

        low = text.lower()
        if low in ("exit", "quit", "q"):
            self.exit()
            return
        if low in ("clear", "cls", "/clear"):
            self.action_clear_log()
            return
        if low in ("/output", "output", "/show"):
            self._write(Static(self.last_output.rstrip() or "(no command output yet)",
                               classes="thought"))
            return
        if low in ("/copy", "copy"):
            if self.last_output.strip():
                self.copy_to_clipboard(self.last_output.rstrip())
                self._write(Static("📋 last output copied to clipboard.", classes="win"))
            else:
                self._write(Static("(nothing to copy yet)", classes="thought"))
            return
        if low in ("/help", "help", "?"):
            self._write(Static(self._help_text(), classes="thought"))
            return
        head, _, arg = text.partition(" ")
        head, arg = head.lower(), arg.strip()
        if head in ("/ai", "ai"):
            self.ai_on = {"on": True, "off": False}.get(arg.lower(), not self.ai_on)
            msg = ("✓ AI ON - type a goal and I'll do it." if self.ai_on
                   else "✓ AI OFF - plain shell; type raw commands and I'll run them.")
            self._write(Static(msg, classes="win" if self.ai_on else "skipped"))
            self._idle_status()
            return
        if head in ("/memory", "memory"):
            self._tui_memory(arg)
            return
        if head in ("/model", "model"):
            self._tui_switch_model(arg)
            return
        if low in ("/status", "status") and self.ai_on:
            self._write(Static("❯ status", classes="user"))
            self._busy = True
            self.query_one("#status", Static).update("⏳ building status board…")
            self.run_agent(agent.STATUS_REQUEST)
            return
        if head in ("/coach", "coach"):
            self.coach = {"on": True, "off": False}.get(arg.lower(), not self.coach)
            msg = ("✓ Coach ON - small slips become a nudge for you to fix. 👀"
                   if self.coach else "✓ Coach OFF - I'll fix small errors myself.")
            self._write(Static(msg, classes="win" if self.coach else "skipped"))
            self._idle_status()
            return
        if head in ("/auto", "auto"):
            a = arg.lower()
            if a == "off":
                self.auto, self.force = False, False
                self._write(Static("✓ Auto OFF - I'll ask before each command.", classes="win"))
            elif a == "force":
                self.auto, self.force = True, True
                self._write(Static("⚠ Auto FORCE - running EVERYTHING unattended, including "
                                   "dangerous commands. Throwaway environments only.",
                                   classes="command danger"))
            else:
                self.auto, self.force = True, False
                self._write(Static("✓ Auto ON - hands-free. Safe commands run on their own; "
                                   "dangerous ones are skipped & logged. Go get some sleep. 😴",
                                   classes="win"))
            self._idle_status()
            return

        # AI off → run the typed text directly as a shell command.
        if not self.ai_on:
            self._write(Static(f"$ {text}", classes="user"))
            self._busy = True
            self.query_one("#status", Static).update("⏳ running…")
            self.run_shell(text)
            return

        self._write(Static(f"❯ {text}", classes="user"))
        self._busy = True
        self.query_one("#status", Static).update("⏳ working…")
        self.run_agent(text)

    # -- the agent, in a worker thread -------------------------------------
    @work(thread=True)
    def run_agent(self, task: str) -> None:
        remember = memory.is_enabled()
        context = memory.build_context()
        if remember and self.actions:
            context = (context + "\n\n" if context else "") + (
                "Commands already run this session and their results - reuse these "
                "answers, do NOT run the same command again for the same info:\n"
                + "\n".join(self.actions[-10:]))
        answer = agent.run_task(
            task, self.model, self.system,
            confirm=self._confirm, dry_run=self.dry_run,
            on_event=self._on_event, context=context,
            mcp_manager=self.manager, status=None,
            chat_history=self.conversation if remember else None,
            coach=self.coach and not self.auto,
        )
        if remember:
            self.conversation.append({"role": "user", "content": task})
            self.conversation.append({"role": "assistant", "content": answer})
            del self.conversation[:-20]
        self.call_from_thread(self._finish, answer)

    def _finish(self, answer: str) -> None:
        self._settle_active()   # last step → done (green dot)
        self._write(Markdown(answer, classes="answer"))
        tag = fun.win_tag(answer)
        if tag:
            self._write(Static(tag, classes="win"))
        self._busy = False
        self._idle_status()
        self.query_one("#prompt", Input).focus()

    # -- plain-shell mode (AI off) -----------------------------------------
    @work(thread=True)
    def run_shell(self, command: str) -> None:
        stripped = command.strip()
        if stripped == "cd" or stripped.startswith("cd "):
            target = stripped[2:].strip().strip('"').strip("'") or "~"
            expanded = os.path.expanduser(target)
            if not os.path.isabs(expanded) and not os.path.isdir(expanded):
                base = os.path.basename(target.replace("\\", "/").rstrip("/")) or target
                match = executor.closest_existing_dir(base)
                if match:
                    target = match
                    self.call_from_thread(self._write, Static(
                        f"↪ entering '{match}' (closest match)", classes="opened"))
            try:
                os.chdir(os.path.expanduser(target))
            except OSError as exc:
                self.call_from_thread(self._write, Static(f"✗ {exc}", classes="command danger"))
            self.call_from_thread(self._shell_done)
            return
        self._cur_command = None   # shell output isn't fed to the AI's memory
        result = executor.run_command(
            command, on_output=lambda t, term: self._on_event("stream", {"text": t}),
            track_cwd=True)   # a `cd` inside a compound command still persists
        self._on_event("exit", {"returncode": result["returncode"], "output": result["stdout"]})
        self.call_from_thread(self._shell_done)

    def _shell_done(self) -> None:
        self._busy = False
        self._idle_status()
        self.query_one("#prompt", Input).focus()

    # -- context bar: where you are + AI state (shown when idle) ------------
    def _idle_status(self) -> None:
        from .cli import _cwd_short, _git_branch
        branch = _git_branch()
        b = f" · ⎇ {branch}" if branch else ""
        ai = "AI on" if self.ai_on else "AI off"
        extra = ""
        if self.auto:
            extra += " · [#e09090]AUTO" + (" FORCE" if self.force else "") + "[/]"
        if not self.coach:
            extra += " · [#e0b060]coach off[/]"
        self.query_one("#status", Static).update(f"[dim]📁 {_cwd_short()}{b} · {ai}[/]{extra}")

    # -- /memory and /model inside the TUI ---------------------------------
    def _tui_memory(self, arg: str) -> None:
        sub, _, rest = arg.partition(" ")
        sub = sub.lower()
        if sub in ("", "show"):
            state = "on" if memory.is_enabled() else "off"
            self._write(Static(f"memory is {state}.\n{memory.load_text() or '(no notes)'}",
                               classes="thought"))
        elif sub in ("on", "off"):
            memory.set_enabled(sub == "on")
            self._write(Static(f"✓ memory {sub}.", classes="win"))
        elif sub == "clear":
            memory.clear()
            self._write(Static("✓ notes cleared.", classes="win"))
        elif sub == "add" and rest.strip():
            memory.add(rest.strip())
            self._write(Static("✓ noted.", classes="win"))
        else:
            self._write(Static("usage: /memory [on|off|show|clear|add <note>]", classes="thought"))

    def _tui_switch_model(self, name: str) -> None:
        from . import config, ollama_client
        from .providers import detect_provider
        key_for = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
        if not name:
            self._write(Static("usage: /model <name>  (e.g. gpt-4.1, claude-opus-4-8, "
                               "qwen2.5:14b)", classes="thought"))
            return
        provider = detect_provider(name)
        if provider in key_for and not os.getenv(key_for[provider]):
            self._write(Static(f"✗ {key_for[provider]} isn't set - can't use '{name}'.",
                               classes="command danger"))
            return
        if provider == "ollama" and not ollama_client.has_model(name):
            self._write(Static(f"✗ '{name}' isn't pulled. Run: ollama pull {name}",
                               classes="command danger"))
            return
        config.set_model(name)
        self.model = name
        self._write(Static(f"✓ switched to '{name}'.", classes="win"))

    # -- event sink (called from worker thread) ----------------------------
    def _on_event(self, kind: str, payload) -> None:
        self.call_from_thread(self._handle, kind, payload)

    def _handle(self, kind: str, payload) -> None:
        if kind == "thought":
            self._status_detail = ""
            self._phrase = fun.pick(fun.THINKING)
            self._settle_active()   # previous step → done (green dot)
            text = payload["thought"]
            widget = self._write(Static(f"[#b08cdd]●[/] {text}", classes="thought"))
            self._active_thought = (widget, text)
        elif kind == "command":
            self._cur_command = payload["command"]
            cls = "command danger" if payload["dangerous"] else "command"
            self._write(Static(f"$ {payload['command']}", classes=cls))
        elif kind == "tool":
            self._write(Static(f"⚙ {payload['tool']} {payload['args']}", classes="command"))
        elif kind == "stream":
            # Live output goes to the status line only; the full text is shown in
            # a content-sized panel on exit (a streaming Log balloons the layout).
            self._status_detail = payload["text"].strip()[:70]
        elif kind == "exit":
            self.last_output = payload.get("output", "") or ""
            self._status_detail = ""
            cmd = self._cur_command
            if self._cur_command:
                first = next((l.strip() for l in self.last_output.splitlines() if l.strip()),
                             "(no output)")
                self.actions.append(f"`{self._cur_command}` → {first[:100]}")
                del self.actions[:-10]
                self._cur_command = None
            self._mount_output(self.last_output, payload["returncode"])
            if payload["returncode"] == 0 and cmd in self._challenges:
                self._challenges.discard(cmd)
                self._write(Static(fun.pick(fun.PRAISE), classes="win"))
        elif kind == "tool_result":
            out = payload.get("output", "") or ""
            self.last_output = out
            self._mount_output(out, 0, title="tool output")
        elif kind == "skipped":
            self._write(Static("└ skipped", classes="skipped"))
        elif kind == "explain":
            self._write(Static(f"⚠  {payload['text']}", classes="explain"))
        elif kind == "challenge":
            self._challenges.add(payload["command"])
        elif kind == "note":
            self._write(Static(payload["text"], classes="opened"))
        elif kind == "opened":
            self._write(Static(f"📂 opened {payload['path']}:{payload['line']} in VS Code "
                               "- take a look 👀", classes="opened"))
        elif kind == "error":
            self._write(Static(f"✗ {payload}", classes="command danger"))

    def _mount_output(self, output: str, code: int, title: str = "output") -> None:
        """Show command output in a content-sized, collapsible panel."""
        lines = [l for l in (output or "").splitlines() if l.strip()]
        if not lines:
            cls = "thought" if code == 0 else "command danger"
            self._write(Static(f"└ exit {code}", classes=cls))
            return
        body = "\n".join(lines)
        long = len(lines) > 12
        label = f"{title} · exit {code}" if title == "output" else title
        self._write(Collapsible(Static(body, classes="outbody"),
                                title=label, collapsed=long))

    # -- approval prompt (blocks the worker thread) ------------------------
    def _confirm(self, action: str, dangerous: bool):
        if self.auto:
            if not dangerous or self.force:
                return ("run", None)
            # hands-free but not forced → skip the dangerous one and log it
            self.call_from_thread(self._write, Static(
                f"⚠ auto-skipped a DANGEROUS command (review later): {action}",
                classes="explain"))
            return ("skip", None)
        self._confirm_event = threading.Event()
        self._confirm_result = None
        self.call_from_thread(self._mount_confirm, dangerous)
        self._confirm_event.wait()
        return self._confirm_result

    def _mount_confirm(self, dangerous: bool) -> None:
        title = "⚠  Run this DANGEROUS command?" if dangerous else "Run this command?"
        opts = OptionList(
            f"1  {'Run anyway' if dangerous else 'Run'}",
            "2  Skip",
            "3  Stop task",
            "4  Type an instruction…",
            id="c_opts",
        )
        box = Vertical(Static(title, classes="confirm-title"), opts,
                       classes="confirm-box danger" if dangerous else "confirm-box")
        self._confirm_row = self._write(box)
        opts.highlighted = 1 if dangerous else 0   # safe default
        self.call_after_refresh(opts.focus)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "c_opts":
            return
        idx = event.option_index
        if self._confirm_row is not None:
            self._confirm_row.remove()
            self._confirm_row = None
        if idx == 3:   # Type an instruction
            self._await_say = True
            self.query_one("#status", Static).update("Type an instruction and press Enter…")
            self.query_one("#prompt", Input).focus()
            return
        self._confirm_result = [("run", None), ("skip", None), ("stop", None)][idx]
        if self._confirm_event:
            self._confirm_event.set()

    # -- misc --------------------------------------------------------------
    def _help_text(self) -> str:
        return ("Describe what you want done, e.g. 'list my docker containers'.\n"
                "Commands:  /ai [on|off] (agent vs plain shell)   "
                "/coach [on|off] (nudge vs auto-fix small slips)   "
                "/auto [on|off|force] (hands-free; force = even dangerous)   "
                "/model <name>   /memory   /output   /copy   clear   exit\n"
                "Confirm prompt: ↑/↓ then Enter, press 1-4, or click.\n"
                "Copy/paste in a TUI is terminal-controlled:\n"
                "  • Copy text with the mouse: hold SHIFT and drag (or use /copy).\n"
                "  • Paste: Ctrl+Shift+V or right-click (Windows Terminal/PowerShell).\n"
                "  • If clipboard is fiddly, run 'utrains --classic' for native copy/paste.")


def run_tui(model, manager=None, auto=False, force=False, dry_run=False) -> int:
    """Launch the Textual UI. Returns 0 on a clean exit."""
    UtrainsApp(model, manager, auto, force, dry_run).run()
    return 0
