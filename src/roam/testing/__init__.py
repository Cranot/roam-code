"""Test-infrastructure helpers shipped inside the package.

Currently holds the CI auto-parallelism pytest plugin
(``roam.testing.ci_xdist``) — loaded via ``-p`` in pyproject's
``addopts`` so it activates wherever the repo's pytest config is read.
"""

from __future__ import annotations
