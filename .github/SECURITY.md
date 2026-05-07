# Security Policy

## Supported versions

| Version  | Supported |
|----------|-----------|
| Latest minor on PyPI (`roam-code`) | Yes |
| Anything older | No — please upgrade |

We patch the latest minor release. There is no LTS branch.

## Reporting a vulnerability

**Email security@roam-code.com.** Encrypted reports preferred — fetch the PGP
key from `https://api.protonmail.ch/pks/lookup?op=get&search=security@roam-code.com`.

Full disclosure policy, scope, and safe-harbor commitments live at
**https://roam-code.com/security**.

We aim to acknowledge reports within 72 hours and ship a fix or mitigation
within 30 days for high-severity issues. We will credit you in the published
advisory unless you ask us not to.

## Out of scope

- Self-XSS that requires the user to paste content into devtools
- Findings in dependencies that already have a public CVE and a known fix
- Theoretical issues with no demonstrable security impact
- Rate-limiting, DoS, or volumetric attacks against the marketing site
