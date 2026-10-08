"""``python -m reqman`` runs the ``reqman`` command line (see ``cli.py``)."""

import sys

from .cli import main

sys.exit(main())
