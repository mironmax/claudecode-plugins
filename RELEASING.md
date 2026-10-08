# Releasing kg-memory

kg-memory reaches users through three channels, and the first two must stay in
step: the plugin's skills and hooks talk to the server, so a plugin from one
release against a server from another can break sessions.

| Channel | What triggers it | What it ships |
|---|---|---|
| Plugin marketplace (Claude Code, Codex) | `version` in `knowledge-graph/.claude-plugin/plugin.json` changing on `main` | skills, hooks, MCP config |
| PyPI (`kg-memory`) | a `v*` tag, then approval in the `pypi` environment | the server and the `kg` command |
| GitHub release and wiki | by hand, at release time | notes and documentation |

## Rules

1. **Version numbers change only in a release.** `plugin.json` and
   `knowledge-graph/server/version.py` are bumped together in one release
   commit, which is tagged on a CI-green commit and published to PyPI the same
   day. A bump without a tag ships the plugin half alone.
2. **`main` always works with the released server.** Fresh installs take the
   plugin from `main` as it stands. A hook, skill or MCP-config change that
   needs new server behaviour stays on its branch until the release that
   carries the server change.
3. **Patch release (0.x.y)** carries only: bug fixes that restore documented
   behaviour, documentation, tests and CI, internal refactors with no
   behaviour change, dependency updates. No new tools or parameters, no change
   to scoring, budgets, recall or delivery, no change to skill or tool text
   that shapes agent behaviour, no new install step.
4. **Minor release (0.x.0)** carries everything else: behaviour changes
   (scoring, budgets, recall, delivery, skill and tool wording), new tools or
   parameters, stored-data changes with their migration. One minor per theme.
   A change with no backward path (data, a removed tool, a new required install
   step) opens its CHANGELOG entry with **Breaking:**.
5. **Every change reaches `main` through a pull request** with CI green on
   Linux and macOS and, when users would notice it, a line under
   `## [Unreleased]` in `CHANGELOG.md`.
6. **A published tag never moves.** Before the PyPI approval a tag can be
   withdrawn: cancel the publish run, delete the GitHub release and the tag.
   After publishing, fix forward with a patch release.
7. **Public text stays public-safe.** No personal names in code, no private
   paths, no unpublished research in the CHANGELOG, release notes, commit
   messages, pull requests, comments, tests or wiki.
8. **The maintainer approves the PyPI publish and decides when a running
   server is restarted.**

## Checklist

1. `[Unreleased]` becomes `## [x.y.z] - YYYY-MM-DD` with a one-paragraph lead;
   every number in the entry is checked against the code.
2. Bump both version files in one commit, to the same number.
3. Grep the diff since the last tag for private text (rule 7).
4. Update the wiki pages the entry touches.
5. Wait for CI on the release commit, then tag it:
   `git tag -a vX.Y.Z -m "…" <commit> && git push origin vX.Y.Z`.
6. Create the GitHub release with the CHANGELOG section as its notes.
7. Approve the publish run; confirm with
   `curl -s https://pypi.org/pypi/kg-memory/json | jq -r .info.version`.
</content>
</invoke>

## On the maintainer's machine

1. The memory server you rely on runs a published release (`uv tool install
   kg-memory`), never a checkout. A restart means upgrading to a release with
   `kg update`, the same path users take.
2. The main checkout stays on `main`, clean. Each task works in its own
   worktree (`git worktree add ../kg-<topic> -b <branch>`), removed after merge.
3. Experiments and development servers run on another port with their own
   storage and state (`KG_HTTP_PORT`, `KG_STORAGE_ROOT`); a server on any port
   but 8765 keeps its state in `port-<N>/`. Port 8765 is only ever a release.
4. The maintainer restarts the release server, and only onto a release.
