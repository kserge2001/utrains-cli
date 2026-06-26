"""Lets you run the package directly: `python -m utrains ...`"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())