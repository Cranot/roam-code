# W1090: ``__all__`` convention in this directory is heterogeneous by design.
#
# - Sectioned with ``#`` comment dividers: ``smells.py``, ``python_idioms.py``,
#   ``detectors.py``. Large modules where grouping (registry / helpers /
#   detector-fns) aids navigation more than a flat alphabetical list would.
# - Detector-cluster ordering (registry constants, version stamp, detector
#   function): ``type_switch.py``, ``clones_cross_layer.py``,
#   ``parallel_hierarchy.py``. The W855 / W856 / W858 clone-detector family
#   chose this shape; the per-file ``__all__`` mirrors the public surface
#   each detector exposes to ``catalog/detectors.py``.
# - Declaration-order: ``_shared.py``, ``tasks.py``. Small modules where
#   helpers-first ordering is semantically meaningful.
# - Trivial single-name (``fixes.py``): convention is N/A at len-1.
#
# Do not force directory-wide uniformity — each cluster's shape carries the
# author's framing. New files should pick the convention whose cluster they
# belong to, not invent a fourth.
