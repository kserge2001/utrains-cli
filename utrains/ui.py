"""
The look of utrains - a calm dark theme and a live "thinking" animation.

Plain ANSI escape codes, no third-party libraries. Colours auto-disable when the
output isn't a real terminal or when NO_COLOR is set, so piping to a file stays
clean. Windows VT support is switched on in utrains/__init__.py.

The palette is tuned for dark terminals: soft, slightly muted tones (teal,
violet, mint, amber) rather than harsh primary colours - easy on the eyes and
never pure black-on-black.
"""

import itertools
import os
import re
import shutil
import sys
import textwrap
import threading
import time

# True-colour palette (R;G;B). Muted/desaturated for easy-on-the-eyes dark terminals.
_PALETTE = {
    "accent":  "38;2;95;209;191",    # teal    - prompts, brand
    "purple":  "38;2;176;140;221",   # purple  - spinner / step marker
    "heading": "38;2;170;150;225",   # violet  - titles
    "ok":      "38;2;127;207;154",   # green   - success / output
    "warn":    "38;2;225;180;95",    # amber   - caution
    "danger":  "38;2;216;138;138",   # red     - destructive
    "dim":     "38;2;154;163;179",   # slate   - secondary text
    "bold":    "1",
    "blink":   "5",                  # slow blink (for the live "Thinking…")
    "invert":  "7",                  # reverse video - highlights the default choice
}
_RESET = "\033[0m"


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _enabled() -> bool:
    """Colour only when writing to a real terminal and NO_COLOR isn't set."""
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _visible_len(text: str) -> int:
    """Length of text as shown on screen, ignoring ANSI colour codes."""
    return len(_ANSI_RE.sub("", text))


def style(text: str, *names: str) -> str:
    """Wrap text in one or more palette styles, e.g. style('hi', 'accent', 'bold')."""
    if not _enabled() or not names:
        return text
    codes = ";".join(_PALETTE[n] for n in names if n in _PALETTE)
    return f"\033[{codes}m{text}{_RESET}" if codes else text


def link(text: str, url: str) -> str:
    """Make `text` a clickable hyperlink (OSC 8) in terminals that support it."""
    if not _enabled():
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def box(lines, title: str = "", color: str = "accent") -> str:
    """
    Draw a rounded box around the given lines (Copilot/Cursor-style card).

    Lines may already contain colour codes - width is measured on the visible
    text, so padding stays correct.
    """
    content_w = max([_visible_len(l) for l in lines] + [_visible_len(title) + 2])
    content_w = min(content_w, term_width() - 4)
    interior = content_w + 2

    if title:
        # "╭─ Title ───…╮"  - 3 chars for "╭─ ", 1 space after the title.
        tail = "─" * max(0, interior - _visible_len(title) - 3) + "╮"
        top = style("╭─ ", color) + style(title, color, "bold") + style(" " + tail, color)
    else:
        top = style("╭" + "─" * interior + "╮", color)
    bottom = style("╰" + "─" * interior + "╯", color)

    rows = [top]
    for line in lines:
        pad = " " * max(0, content_w - _visible_len(line))
        rows.append(style("│", color) + " " + line + pad + " " + style("│", color))
    rows.append(bottom)
    return "\n".join(rows)


# --- Utrains brand -------------------------------------------------------
# Terminal version of the animated grad-cap + globe logo from aws-service-agent.
BRAND_SLOGAN = "Utrains => Become a Job-Ready AI / DevOps Engineer"
BRAND_WEB = "https://utrains.org"           
BRAND_PHONE = " +1 (302) 689 3440"       
BRAND_LOGO = "🎓🌐"                       


def welcome() -> str:
    """A branded intro: a little Utrains commercial, then the product header."""
    bar = rule()
    lines = [
        "",
        bar,
        style("🎓  " + BRAND_SLOGAN, "heading", "bold"),
        (style("🌐 ", "accent") + link(style(BRAND_WEB, "accent", "bold"), BRAND_WEB)
         + style("    ☎  ", "accent")
         + link(style(BRAND_PHONE, "accent"),
                "tel:" + "".join(c for c in BRAND_PHONE if c.isdigit() or c == "+"))),
        bar,
        style(BRAND_LOGO + " Utrains", "purple", "bold") + style("  ·  local terminal agent", "dim"),
        style("Tell me what you want => I'll run the commands to do it.", "dim"),
        (style("/help", "accent") + style(" · ", "dim") + style("/ai", "accent")
         + style(" · ", "dim") + style("/model", "accent") + style(" · ", "dim")
         + style("clear", "accent") + style(" · ", "dim") + style("exit", "accent")),
        bar,
    ]
    return "\n".join("  " + line for line in lines)


def rule(label: str = "") -> str:
    """A horizontal divider, optionally with a centred label."""
    if not label:
        return style("─" * 52, "dim")
    pad = max(0, (52 - len(label) - 2) // 2)
    return style("─" * pad + f" {label} " + "─" * pad, "dim")


class Spinner:
    """
    A braille spinner that animates on its own thread while something blocks.

    Use as a context manager:
        with Spinner("thinking"):
            slow_call()
    It clears its line on exit, so whatever you print next starts clean.
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, text: str = "Thinking", color: str = "purple"):
        # The default "Thinking" gets a random playful label; explicit text
        # (e.g. "Running command") is kept as-is.
        if text == "Thinking":
            from . import fun
            text = fun.pick(fun.THINKING)
        self.text = text
        self.color = color
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "Spinner":
        if _enabled():
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
            sys.stdout.write("\r\033[K")  # erase the spinner line
            sys.stdout.flush()

    def _spin(self) -> None:
        for frame in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            dot = style(frame, self.color, "bold")          # purple, bold square
            label = style(f"{self.text}…", self.color, "blink")  # blinking purple text
            sys.stdout.write(f"\r{dot} {label}")
            sys.stdout.flush()
            time.sleep(0.08)


def spinner(text: str = "Thinking", color: str = "purple") -> Spinner:
    """Convenience factory so callers can write `with ui.spinner('Thinking'): …`."""
    return Spinner(text, color)


# --------------------------------------------------------------------------
# Text wrapping & light markdown - keeps long output readable in a terminal.
# --------------------------------------------------------------------------

def term_width() -> int:
    return max(40, min(shutil.get_terminal_size((80, 20)).columns, 100))


def clear_screen() -> None:
    """Clear the terminal (and scrollback), like the shell's `clear`/`cls`."""
    if _enabled():
        sys.stdout.write("\033[2J\033[3J\033[H")   # clear screen + scrollback, home cursor
        sys.stdout.flush()
    else:
        os.system("cls" if sys.platform == "win32" else "clear")


def wrap(text: str, indent: str = "   ") -> str:
    """Wrap each paragraph to the terminal width with a hanging indent."""
    width = term_width()
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
        else:
            out.append(textwrap.fill(para, width=width,
                                     initial_indent=indent, subsequent_indent=indent))
    return "\n".join(out)


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")


def _plain(text: str) -> str:
    """The text as it appears on screen - markup and ANSI stripped."""
    text = _ANSI_RE.sub("", text)
    text = _BOLD_RE.sub(r"\1", text)
    return _CODE_RE.sub(r"\1", text)


def _disp_width(text: str) -> int:
    """On-screen width, counting emoji / wide glyphs as 2 cells (for table padding)."""
    import unicodedata
    width = 0
    for ch in _plain(text):
        o = ord(ch)
        if (unicodedata.east_asian_width(ch) in ("W", "F")
                or o >= 0x1F300                       # emoji
                or 0x2600 <= o <= 0x27BF              # misc symbols / dingbats (✅ �add)
                or 0x2300 <= o <= 0x23FF              # ⌛ ⏳ technical
                or 0x2B00 <= o <= 0x2BFF):            # ⭐ arrows / stars
            width += 2
        else:
            width += 1
    return width


def _md_inline(text: str) -> str:
    """Inline markup: **bold** and `code`."""
    if not _enabled():
        return _plain(text)
    text = _BOLD_RE.sub(lambda m: style(m.group(1), "bold"), text)
    return _CODE_RE.sub(lambda m: style(m.group(1), "accent"), text)


def _style_cell(cell: str) -> str:
    """Colour a table cell by its status word/badge (done=green, pending=amber…)."""
    p = _plain(cell)
    low = p.lower()
    if any(k in p for k in ("✅", "✔", "☑")) or "done" in low or "complete" in low:
        return style(p, "ok")
    if any(k in p for k in ("❌", "✖", "✗")) or "fail" in low or "error" in low:
        return style(p, "danger")
    if (any(k in p for k in ("⏳", "▶", "◷", "🔴")) or "pending" in low
            or "next" in low or "progress" in low or "todo" in low):
        return style(p, "warn")
    return _md_inline(cell)


def _split_cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|") and line.count("|") >= 2


def _is_table_sep(line: str) -> bool:
    s = line.strip()
    return bool(s) and "-" in s and bool(re.match(r"^\|?[\s:|-]+\|?$", s))


def _md_table(header: list[str], body: list[list[str]]) -> str:
    """Draw a markdown table as a rounded, colour-coded ANSI box."""
    ncol = max([len(header)] + [len(r) for r in body])
    norm = lambda r: (r + [""] * ncol)[:ncol]
    header, body = norm(header), [norm(r) for r in body]
    widths = [0] * ncol
    for row in [header, *body]:
        for j, cell in enumerate(row):
            widths[j] = min(max(widths[j], _disp_width(cell)), 52)

    def border(left, mid, right):
        return style(left + mid.join("─" * (w + 2) for w in widths) + right, "dim")

    def row(cells, is_header=False):
        parts = []
        for j, cell in enumerate(cells):
            disp = style(_plain(cell), "heading", "bold") if is_header else _style_cell(cell)
            parts.append(" " + disp + " " * max(0, widths[j] - _disp_width(cell)) + " ")
        pipe = style("│", "dim")
        return pipe + pipe.join(parts) + pipe

    out = [border("╭", "┬", "╮"), row(header, True), border("├", "┼", "┤")]
    out += [row(r) for r in body]
    out.append(border("╰", "┴", "╯"))
    return "\n".join(out)


def _md_code_block(lines: list[str]) -> str:
    """A fenced code block as a boxed, highlighted command card."""
    return box([style(l, "accent") for l in lines] or [""], color="accent")


def md(text: str) -> str:
    """Render light markdown - headings, tables, code blocks, bullets, **bold**,
    `code` - into colourful terminal output (Copilot/Cursor-style)."""
    lines = (text or "").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):                       # fenced code block
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            out.append(_md_code_block(block))
        elif (_is_table_row(line) and i + 1 < len(lines)      # markdown table
              and _is_table_sep(lines[i + 1])):
            header = _split_cells(line)
            i += 2
            body = []
            while i < len(lines) and _is_table_row(lines[i]):
                body.append(_split_cells(lines[i]))
                i += 1
            out.append(_md_table(header, body))
        elif stripped.startswith("#"):                        # heading
            out.append(style(stripped.lstrip("#").strip(), "heading", "bold"))
            i += 1
        elif re.match(r"^\s*[-*]\s+", line):                  # bullet
            indent, rest = re.match(r"^(\s*)[-*]\s+(.*)", line).groups()
            out.append(f"{indent}  " + style("•", "accent") + " " + _md_inline(rest))
            i += 1
        else:
            out.append(_md_inline(line))
            i += 1
    return "\n".join(out)


# --------------------------------------------------------------------------
# Interactive selection menu - arrow keys (↑/↓), number keys, or Enter.
# --------------------------------------------------------------------------

def _interactive() -> bool:
    return _enabled() and sys.stdin.isatty()


def _read_key() -> str:
    """Read one keypress, returning 'up'/'down'/'enter'/<char>/'other'."""
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):                       # arrow / function key prefix
            return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "other")
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":                                 # ESC sequence → arrow
            return {"[A": "up", "[B": "down"}.get(sys.stdin.read(2), "other")
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _render_choices(options, idx) -> None:
    for i, (label, color) in enumerate(options):
        if i == idx:
            line = (f"  {style('▸', 'accent', 'bold')} "
                    f"{style(f' {i + 1} ', 'invert', 'bold')} {style(label, color, 'bold')}")
        else:
            line = f"    {style(f'{i + 1})', 'dim')} {style(label, color)}"
        sys.stdout.write("\r\033[K" + line + "\n")
    sys.stdout.flush()


def select(options, default: int = 0) -> int:
    """
    Show a vertical menu and return the chosen index.

    options: list of (label, color). Use arrow keys or number keys; Enter takes
    the highlighted (default) row. Falls back to a typed prompt when there's no
    real terminal (e.g. piped input).
    """
    n = len(options)
    if not _interactive():
        for i, (label, _c) in enumerate(options):
            print(f"    {i + 1}) {label}")
        raw = input(f"  Choice [Enter = {default + 1}]: ").strip()
        return int(raw) - 1 if raw.isdigit() and 1 <= int(raw) <= n else default

    idx = default
    _render_choices(options, idx)
    while True:
        key = _read_key()
        if key == "up":
            idx = (idx - 1) % n
        elif key == "down":
            idx = (idx + 1) % n
        elif key == "enter":
            break
        elif key.isdigit() and 1 <= int(key) <= n:
            idx = int(key) - 1
            sys.stdout.write(f"\033[{n}A")
            _render_choices(options, idx)
            break
        else:
            continue
        sys.stdout.write(f"\033[{n}A")     # jump back to the top of the list
        _render_choices(options, idx)
    return idx
