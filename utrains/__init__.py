"""
utrains - a local, LLM-powered operations agent for your terminal.

You type what you want in plain English; utrains decides which shell commands
to run (Windows, Linux, Docker, AWS, Azure, GitHub, Python, git, kubectl …),
asks for your OK, runs them, reads the output, and keeps going until the job
is done. The "brain" is a local model served by Ollama, so nothing leaves your
machine.
"""

__version__ = "0.1.4"


def enable_utf8_output() -> None:
    """
    Prepare the console for pretty output: UTF-8 everywhere (so symbols/emoji and
    box-drawing render instead of mojibake, and typed input is read correctly) plus
    ANSI colour support (VT processing on Windows 10+). Safe to call repeatedly.
    """
    import sys

    # 1. Put the WINDOWS CONSOLE itself into UTF-8 (codepage 65001). Without this a
    #    fresh double-clicked console uses the legacy OEM codepage and shows our
    #    UTF-8 output as garbage — and misreads typed input. This is the real fix
    #    for the frozen-binary "funny characters / can't choose" problem.
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)   # what we PRINT
            kernel32.SetConsoleCP(65001)         # what we READ (input)
        except Exception:  # pragma: no cover
            pass

    # 2. Make Python's own streams agree (UTF-8), including stdin for input().
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    # 3. Turn on ANSI escape handling so colours and the menu cursor moves work.
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            for handle_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
                handle = kernel32.GetStdHandle(handle_id)
                mode = ctypes.c_uint32()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                    kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:  # pragma: no cover - never fail just for colours
            pass
