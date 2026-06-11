# Security Policy

## Supported Versions

This project is pre-1.0. Security fixes are applied to the latest version only.

| Version | Supported |
| ------- | --------- |
| latest (0.x) | yes |

## Scope and Trust Boundary

The knowledge-graph MCP server is designed to run locally on the user's own machine,
bound to `127.0.0.1` only. It is not intended to be exposed to the network or the internet.

**The trust boundary is "processes on this machine."** There is no authentication:
any local process can read and modify the graphs through the MCP or REST endpoints.
Session IDs are namespacing, not authorization — presenting an unknown session ID
simply creates it.

Web content is *outside* the boundary, and the server defends against the two
browser-side paths that could otherwise cross it:

- **DNS rebinding** — all HTTP/WebSocket requests must carry a local `Host` header
  (`localhost`, `127.0.0.1`, `::1`, or the explicitly configured bind host);
  others are rejected with `421`.
- **Cross-origin WebSockets** (browsers do not apply CORS to WebSocket upgrades) —
  upgrades with a non-local `Origin` are rejected. Absent `Origin` (non-browser
  clients) is allowed.

Node IDs, edge endpoints, and relationship types are validated to a safe character
set at the write boundary, so graph data cannot carry markup into surfaces that
render it (the visual editor, `kg_read` output).

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
