"""Allow running as `python -m researchclaw`."""

import os
import sys

from researchclaw.cli import main

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.exit(main())
