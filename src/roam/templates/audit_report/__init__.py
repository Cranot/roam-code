"""Audit-report assets shipped with the wheel (W554).

Houses the canonical ``control-mapping.yaml`` that the OSCAL emitter
(`roam evidence-oscal`) and the persistent-OSCAL-artifact path
(`roam ci-setup --with-oscal`) consume at runtime.

Pre-W554 the YAML lived at the project root under
``templates/audit-report/`` and was excluded from the wheel — pip-install
users could not run the OSCAL surfaces because the helper file was not
shipped. The W554 move lifts the YAML into the ``roam.templates.audit_report``
package so ``pyproject.toml`` package-data picks it up, and uses
``importlib.resources`` for wheel-safe lookup at runtime.

Non-runtime audit-report material (README.md, samples, the
pr-replay-template.md prose reference) intentionally stays at the
project-root ``templates/audit-report/`` location — it is dev-tree
documentation, not runtime data.
"""
