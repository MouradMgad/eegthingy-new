"""Run the SSVEP workbench or open the collector directly."""

import sys

from .app import run as run_collector
from .workbench import menu


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() in {"collect", "collector"}:
        run_collector()
    else:
        menu()
