"""PyInstaller entry point — a plain script (absolute import) so the frozen
binary can launch utrains without package/relative-import context."""
from utrains.cli import main

if __name__ == "__main__":
    main()
