"""Allow running as python -m roam."""

import sys

from roam.cli import cli

# redactedgraceful Ctrl-C: exit 130 (SIGINT convention) without
# spilling a Python traceback. Long-running commands still leave any
# committed DB rows in place — this just suppresses the noise.
try:
    cli()
except KeyboardInterrupt:
    sys.stderr.write("\nInterrupted (Ctrl-C). Partial work has been preserved; rerun the command to continue.\n")
    sys.exit(130)
