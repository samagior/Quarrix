"""Entry point of the Quarrix Windows executable."""

import multiprocessing
import sys

from quarrix.gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # required for worker processes in an .exe
    sys.exit(main())
