"""Hardcoded-credentials fixture. The Stripe key is injected at test time
(see _build_repo) so that no COMMITTED file carries a real-shaped Stripe
secret that GitHub push-protection would block; roam scans the rendered
temp-repo copy, which has the full token. The AWS lines use the canonical
AWS docs EXAMPLE values (literal 'EXAMPLE'), which the scanner suppresses
via _is_placeholder_line, so they are correctly NOT reported.
"""

AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
GITHUB_TOKEN = "ghp_abc1234567890abc1234567890abc12345678"
