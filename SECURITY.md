# Security Policy

## Supported Versions

This project is pre-1.0. Security fixes are applied to the latest version only.

| Version | Supported |
| ------- | --------- |
| latest (0.x) | yes |

## Scope

The knowledge-graph MCP server is designed to run locally on the user's own machine,
bound to `127.0.0.1` only. It is not intended to be exposed to the network or the internet.
The attack surface is therefore limited to local user processes.

That said, we take reports seriously — unexpected behavior that could affect users running
the server in non-standard configurations is worth knowing about.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report privately via [GitHub Security Advisories](https://github.com/mironmax/claudecode-plugins/security/advisories/new).

Include:
- Description of the issue and its potential impact
- Steps to reproduce
- Affected version(s)
- Any suggested fix, if you have one

You can expect an acknowledgement within 48 hours and a resolution or status update
within 7 days for confirmed issues.
