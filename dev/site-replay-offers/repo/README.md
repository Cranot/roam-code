<!-- Stored PR Replay copy for README.md. The tier states in dev/site-product-policy.json select each variant; run python scripts/build_site_product_facts.py --write after changing either. -->
<!-- variant:readme-list:open -->

- **PR Replay — paid report by request.** A written assessment of an agreed
  change history, reviewed findings, and a founder walk-through. The existing
  [Team and Deep scopes](https://roam-code.com/audit#tiers) define the PR window;
  agree how commits map to those PRs before kickoff. Try the free local sample
  with `roam pr-replay --tier sample`.
  Its default range is `HEAD~5..HEAD`, not necessarily five PRs; your checkout
  needs that history. A replay does not prove that an incident would have been prevented.
- **Roam Review — planned, not available to subscribe to.** A proposed hosted
  pull-request check using Roam's local analysis. The hosted app, installation
  flow, and billing are not built yet.
- **Roam Cloud — planned, not available to subscribe to.** A proposed shared
  history of codebase measurements. The local `roam metrics-push` command exists;
  that does not establish a hosted dashboard. Inspect its payload with `--dry-run`.

<!-- /variant -->
<!-- variant:readme-list:paused -->

- **PR Replay — free local sample.** Run `roam pr-replay --tier sample` to see
  which structural findings in recent commits deserve a closer look. Paid Team
  and Deep reports are paused for new requests while we fix defects in the
  reports; existing orders keep their agreed terms.
  Its default range is `HEAD~5..HEAD`, not necessarily five PRs; your checkout
  needs that history. A replay does not prove that an incident would have been prevented.
- **Roam Review — planned, not available to subscribe to.** A proposed hosted
  pull-request check using Roam's local analysis. The hosted app, installation
  flow, and billing are not built yet.
- **Roam Cloud — planned, not available to subscribe to.** A proposed shared
  history of codebase measurements. The local `roam metrics-push` command exists;
  that does not establish a hosted dashboard. Inspect its payload with `--dry-run`.

<!-- /variant -->
<!-- variant:readme-faq:open -->PR Replay offers a
free local sample and paid reports with founder review, by agreed scope.<!-- /variant -->
<!-- variant:readme-faq:paused -->PR Replay offers a
free local sample; see [PR Replay](https://roam-code.com/audit) for report availability.<!-- /variant -->
