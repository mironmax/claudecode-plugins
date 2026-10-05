# Security Policy

## Supported Versions

This project is pre-1.0. Security fixes are applied to the latest version only.

| Version | Supported |
| ------- | --------- |
| latest (0.x) | yes |

## Scope and Trust Boundary

The knowledge-graph MCP server is designed to run locally on the user's own machine,
bound to `127.0.0.1` by default (`KG_HTTP_HOST` can change this). It is not intended to be exposed to the network or the internet.

**The trust boundary is "processes on this machine."** There is no authentication:
any local process can read and modify the graphs through the MCP or REST endpoints.
Session IDs track context and project scope, not authorization. A first
`kg_read` registers a session; later tools require a known session where their
schema calls for one. These guards prevent accidental scope/state mistakes,
not access by another local process.

Web content is *outside* the boundary, and the server defends against the two
browser-side paths that could otherwise cross it:

- **DNS rebinding** — all HTTP/WebSocket requests must carry a local `Host` header
  (`localhost`, `127.0.0.1`, `::1`, or the explicitly configured bind host);
  others are rejected with `421`.
- **Cross-site requests** (a page cannot read the response, but a GET or simple
  POST still has side effects) — HTTP requests with `Sec-Fetch-Site: cross-site`
  or a non-local `Origin` are rejected with `403`.
- **Cross-origin WebSockets** (browsers do not apply CORS to WebSocket upgrades) —
  upgrades with a non-local `Origin` are rejected.

Non-browser clients send neither `Origin` nor `Sec-Fetch-Site` and are allowed.

Node IDs, edge endpoints, and relationship types are validated at the write
boundary. Gists, notes and touches remain arbitrary text; the visual editor
escapes text when rendering it. Memory delivered to an agent is still model
input and should be treated according to its source.

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
