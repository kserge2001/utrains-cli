"""
utrains - a local, LLM-powered operations agent for your terminal.

You type what you want in plain English; utrains decides which shell commands
to run (Windows, Linux, Docker, AWS, Azure, GitHub, Python, git, kubectl …),
asks for your OK, runs them, reads the output, and keeps going until the job
is done. The "brain" is a local model served by Ollama, so nothing leaves your
machine.
"""

__version__ = "0.1.0"


def enable_utf8_output() -> None:
    """
    Prepare the console for pretty output: UTF-8 encoding (so symbols/emoji don't
    crash Windows' cp1252) and ANSI colour support (VT processing on Windows 10+).
    Safe to call more than once.
    """
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    # Turn on ANSI escape handling in the Windows console.
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
