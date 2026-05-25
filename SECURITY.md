# Security Policy

## Supported versions

Only the currently-released minor line receives security fixes. As of writing
that is `0.3.x`. Older lines (`0.2.x` and earlier) are unsupported.

| Version | Supported          |
| ------- | ------------------ |
| 0.3.x   | Yes                |
| < 0.3   | No                 |

## Reporting a vulnerability

Please report vulnerabilities **privately**, not via a public issue.

Preferred channel: **GitHub private security advisories**
<https://github.com/Workharu/eth-validator-stats/security/advisories/new>

For especially sensitive issues that you do not want recorded on GitHub at all,
email `<security@...>` (maintainer to fill in).

Please include:
- The version (`eth-validator-stats --version`) and install method (`.deb` /
  `.rpm` / `pipx` / source).
- A minimal reproduction or proof-of-concept.
- The impact you believe it has.

## Scope

`eth-validator-stats` is a **read-only** HTTP client against a beacon node. It
does **not** submit attestations, does **not** sign blocks, and does **not**
hold validator signing keys. The threat model is therefore narrow:

- Misuse or leakage of `beacon_auth_token` (read from the config file).
- Tampering with push-notification payloads sent to ntfy.
- Local-only privilege issues on system installs (file modes under `/etc/` and
  `/opt/`, the dedicated service user).

Out of scope: anything beacon-node-side, anything ntfy-server-side, anything
that requires an attacker who already has write access to the config file.

## Disclosure timeline

Standard **90 days** from initial private report to public disclosure. We will
acknowledge receipt within a few days, agree on a fix window, and coordinate
the public advisory and release.
