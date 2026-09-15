# Changelog

All notable changes to this project are documented here.

## [0.9.38] - 2026-09-15

A security release. Nothing in it changes what the server does; all of it changes what the server will accept and what it runs on.

### Security
- **Every path a hook sends is contained before it is used.** Hook payloads arrive as HTTP requests, so the paths they carry — the working directory behind a tool event, the transcript behind a session resume — are untrusted input however they were produced. Two of them reached the filesystem on trust. A tool event's `cwd` named the file the event counters live in through a slug sanitizer that is sound but sits several calls deep; the request handler now resolves it through the same home-directory containment every other entry point uses, and a `cwd` outside home is ignored rather than written. A resume's `transcript_path` was opened as given; it is now accepted only as a `.jsonl` file under the user's home, which is the only place the harness writes one, so a request naming anything else reads nothing. Neither change alters behaviour for a real hook payload.

- **The prompt scrubbers are linear.** Recall strips `[Image: …]` placeholders and quoted paths from the prompt before matching it, and did so with two regular expressions whose unbounded character class precedes a closing delimiter — quadratic whenever that delimiter never arrives, on input that is by nature unbounded. Both are now hand scans that visit each character once; behaviour is unchanged and pinned by tests, and a half-megabyte prompt of unclosed delimiters scans in well under a second.

- **Dependency floors carry the advisories, and an old venv follows them.** Two libraries the server runs on had published fixes it could not pick up: `mcp` below 1.28.1 (CVE-2026-59950) and `starlette` below 1.3.1 (five advisories, two rated high). `starlette` is transitive — `fastapi` and `mcp` both leave it unbounded — so it gains an explicit advisory floor of its own. The larger gap was mechanical: dependencies were installed without `--upgrade`, so a venv kept every transitive at whatever version its first install happened to resolve, and a floor bump moved only the package it named. The install now runs `--upgrade --upgrade-strategy eager` whenever `requirements.txt` changes, so an existing environment converges on what a fresh install would resolve, guarded by the smoke test from 0.9.34. Verified on a clean tree: the resolved set carries no known advisories and passes the full suite.

### Changed
- Dependabot version updates are switched off (`open-pull-requests-limit: 0`); security updates are unaffected. Requirements are floors, so a fresh install already resolves to current releases, and a floor bump only forces older environments to upgrade — which is now a release decision, made when an advisory calls for it, rather than a weekly stream of pull requests.
- The environment setup message distinguishes a first run from a requirements change.

21 test files, 534 assertions, suite green.

## [0.9.37] - 2026-09-15

Maintenance existed in one shape — a 25-call pass fired by a systemd timer — and that shape does not fit a laptop. This release re-cuts it into something that runs while you work, and gives the agent doing it a memory of its own.

### Added
- **Chores: maintenance in a unit that fits a working day.** Left to a timer on a laptop, the pass reaches each graph roughly once every three weeks, against a staleness horizon of 14 days, because the gate is a five-way conjunction (machine awake AND quota gauge fresh AND 5h<60% AND 7d<85% AND inside the last 75 minutes of the moving 5h window) whose terms are anti-correlated by construction. The gauge is written only by an interactive session's status line, so a fresh gauge means someone is working — which is exactly when the usage gate is shut; and when nobody is working the laptop is suspended, so the blind night window aims at hours that mostly do not exist. The schedule was not underperforming, it was starved.

  A **chore** is the same work in a smaller unit: ONE debt category, one or two targets the server names up front, and no orientation at all. The pass spends its first third deciding what to do, and that is precisely the part the server can do for free — it already computes every debt factor. What remains is five or six tool calls. It is dispatched on the one signal that genuinely correlates with opportunity — a prompt arriving, which the server already sees, because the recall hook posts it — and runs as a detached headless agent, so it costs the live session no context and needs no cooperation from the model in it. `core/chores.py` selects; `mcp_http/chore_dispatch.py` gates, spawns and watches.

  Three rules bound which nodes a chore may take, and each is a bug that would otherwise be. It never **renames** a node a recently-active session is holding — the session keeps the old id, so the rename turns its next read into a NOT FOUND. It is only a *preference* for the other kinds, and the distinction matters: a blanket veto would refuse every chore in the project currently being worked on, because a session that has done the loud full read has marked every active node seen, and a blanket veto therefore bars the whole graph. A gist rewrite preserves meaning and an added edge is invisible, so those kinds demote a held node instead of barring it. It never takes a node an earlier pass recorded in `declined` — those lines are decisions, and re-proposing them is the work the trail exists to prevent. And it never runs the same category forever: kinds rank by the weight the debt formula itself gives them, so gists lead, but three chores of one kind yield to the next non-empty category, or thirteen long ids rot while gists get tightened two at a time. Entity consolidation and duplicate merges stay in the full pass, where there is context to weigh them.

  **Off unless switched on**, because it spends quota: `~/.knowledge-graph/chores.json` `{"enabled": true}` or `KG_CHORES=1`. Everyone else pays one stat of a missing file per prompt. Gates are stricter than the scheduled pass on the 5h gauge (55% vs 60%) and refuse a stale reading outright — the timer tolerates staleness because waiting for a fresh gauge overnight means never running, but a chore fires while someone is working, so a stale reading means the status line is not rendering and spending blind would land on the user's own session. Every decision, refusals included, appends one line to `~/.knowledge-graph/chores.jsonl`, so "why did nothing run" is answerable — the question the old dispatcher could not answer for weeks. The scoped MCP-only allowlist the chore runs under **ships with the plugin** (`chores/settings.json`): the previous dispatcher kept its settings in `~/.config`, outside the repo, and drifted — it never gained `kg_rename_node` when v0.9.35 added it, and the one pass that tried id work recorded `ids_renamed: 0`, declining the category as "out of scope per dispatch instructions" while the DEBT line it was sent to pay down was counting seven long ids.

- **The pass runs on the same activity signal, funded by the weekly surplus.** Chores create a trap on their own: the scheduled dispatcher selects graphs scoring ≥ 0.3, chores pay down exactly the terms that produce that score, and the deficit factor bottoms out at the debt formula's own constant 0.25. Measured — a groomed, fully-active graph caps at 0.25 however long it goes untended, so a well-chored graph would never be selected for a full pass again, and the two categories chores refuse (entity consolidation, duplicate merges) would never happen on it at all. Debt reports whether the graph is *correct*; it cannot report that structural work is waiting, which is the same blind spot a health check has against starvation.

  So the pass tier is triggered by **time since the last stamped pass** — `PASS_INTERVAL_DAYS`, default 21 — on a graph in use, and never by debt. Its prompt carries the full six-category runbook inline, versioned beside the skill it implements so it cannot drift the way the out-of-repo script did, and it stamps `task_id="maintain"`, which is what resets staleness.

  **It is paid for out of the weekly allowance, not the daily one.** The 5h gauge is what the day's own work needs, and a pass keeps well clear of it (under 40%). The 7-day allowance is the one routinely left partly unspent, and unspent weekly quota is simply lost at the reset — so the gate is a *pace* test rather than an absolute headroom test: `pace = seven_day_pct / (100 × fraction of the window elapsed)`, and a pass runs only at `pace ≤ 1.0`, meaning the week is on course to finish under 100%. The test is self-adjusting, which is why no day-of-week rule appears anywhere in the code: a real working day early in the week puts usage above the line and the pass waits, while a quiet week drifts further under the line each day, concentrating firings near the reset by arithmetic. A quiet week three days in might read 22% against a 48% line — a pace of 0.46 — and that surplus is exactly what the tier is designed to claim. An absolute 7d ceiling of 70% backstops it, since at 6.5 days elapsed the linear line sits at 93% and would otherwise wave through a nearly spent week. Passes use their own settings file (`chores/pass-settings.json`), which adds the delete tools a merge needs; the small frequent unit stays strictly non-destructive.

- **A third graph level, `maintain` — the maintenance agent's own memory.** What accumulated before was the `kg_progress` trail: per-graph, twenty entries, counts and declines. That is a work log, not craft, and a lesson learned gardening one project could never reach the next. `~/.knowledge-graph/maintain.json` holds what chores learn about *maintaining* — "a rename that drops the term prompts actually use costs recall even when the new id is better" — one store per machine, shared by every chore in every project. It is isolated by construction rather than by filtering: `read_graphs`, `search` and `survey_debt` each name the graphs they touch, so a gardening note cannot surface as prompt recall by accident, and reaching it requires naming the level. The dispatcher renders the lessons INTO each chore's prompt rather than making the chore fetch them — a lesson that costs a tool call is a lesson that gets skipped. Writing one is deliberately rare: only when a future chore would act on it differently, reinforced by writing to an existing id rather than minting a near-duplicate. Read it with `kg_read(session_id, level="maintain")`.

### Fixed
- **The maintenance stamp was invented, and it decided which graph got tended.** `/kg-maintain` and the dispatcher both asked the pass to stamp `{"last_ts": <unix now>}`, and an MCP-only agent has no clock — no Bash, no way to ask. It guessed — a round hour at best, and nothing stops a guess from landing a week ahead or a year behind the pass it stamps. Staleness is the leading term of the debt score and therefore of the dispatcher's target selection, so a graph could stamp itself unpickable for a week or permanently maximally stale, and the whole maintenance economy ran on numbers a model made up. `set_progress` now writes `last_ts` from the server clock, replacing any supplied value in both the stamp and its trail copy; the skill no longer asks for one and says why.

- **A progress stamp made before its graph was loaded vanished twice over.** `set_progress` named the graph key without resolving it, so a stamp for a project graph that lazy loading had not reached yet went into an in-memory dict the subsequent load overwrote from disk — and `_write_through` skipped it for having no persistence entry behind it. Masked in practice because a pass calls `kg_read` first, which loads the graph; surfaced by a test that stamped first. Both `set_progress` and `get_progress` now resolve through `_resolve_graph_key`, which loads.

- **An unloaded graph read as "never passed".** A pass-tier candidate whose progress dict was empty because nothing had loaded it yet computed `days_since_pass` as infinite and qualified on the spot. Every server restart would have looked like a graph overdue for a full pass. Candidates are now loaded before any of them is judged, graphs under `PASS_MIN_ACTIVE_NODES` are too small to repay a pass, and an infinite age is logged as `null` rather than as the `Infinity` token, which is not valid JSON.

- **The usefulness signal could only ever confirm itself.** `kg_useful` is the sole writer of `_useful_ts` — the usefulness term is 0.35 of the archival score and the only one an agent controls — and every rule around it asked for the same one thing: at wrap-up, name the nodes that demonstrably changed the outcome. So the only node that could earn credit was a node the surface had already shown. The scorer therefore heard about every archival decision it got right and about none that it got wrong, which is not a quiet failure but a ratchet: a node archived too early has no way to report the sessions it should have been in, and each of those sessions lowers its recency further. The class is worse for preferences than for facts — a preference can be right on three separate occasions and stay archived through all of them, because a fact is retrieved because its topic is already in context, while a preference must fire *before* intent forms, when nothing in context names it.

  Two things now earn the signal instead of one. A node that HELPED — it was in front of you and the work went differently for it, judged at wrap-up against results, exactly as before. And a node that was MISSING — it existed, the session needed it, and nothing surfaced it, so the work re-derived what the graph already held or the user supplied it themselves. A miss is sent the *moment* it is established rather than held for wrap-up, because the correction in front of you is the evidence and a session that ends abruptly still records it; late feedback counts the same way, since a node whose relevance only becomes clear afterwards was still missing while it mattered. Whenever endorsements are being rationed a miss outranks a hit, on the same asymmetry: only a miss can correct a wrong archival, while a hit confirms a right one. Wording only — the timestamps and the 90-day decay are untouched; the budget itself is loosened just below. The one mechanical companion is a note, not a change: an endorsement does not un-archive anything, so a miss wants `kg_read(ids=[...])` beside it, which promotes the node, and the endorsement is then what stops it sinking again.

- **Two descriptions credited search with a signal it does not send.** The `kg_search` tool description and the kg-core skill both told the agent that finding a node at the moment it is needed feeds its usefulness score. It does not — `kg_useful` is the only writer of `_useful_ts`, and search feeds nothing at all. The instinct behind the claim was right and now has a real mechanism behind it: a node you had to dig for is a node the surface should have shown you, which is a miss, and saying so is what keeps it alive.

- **The endorsement budget stopped being a wall.** `MAX_LIKES_PER_SESSION` was one number doing two jobs: advising the agent to be selective, and refusing the sixth call outright. The refusal is the wrong half. Five is a fine number for "which nodes changed the outcome" and a badly wrong one the moment misses count too — a session corrected three times in a row has three real misses to report before it has praised a single hit, and the run of misses is precisely the evidence the signal was just extended to carry. A cap that silences it destroys the data at exactly the moment the data exists. An agent that hits the limit has no way to record what it found.

  Split into two numbers. `LIKES_GUIDANCE_PER_SESSION = 5` is what the doctrine asks for and what the tool description, the skill and the wrap-up nudge all say — kept, because endorsement that costs nothing means nothing. `MAX_LIKES_PER_SESSION = 10` is a flood stop rather than a budget, set where no honest session reaches it. Past the guidance nothing is refused: `mark_useful` returns `over_guidance`, and the reply says how far over it is and how much is left — back-pressure that keeps the pressure to be selective without ever making a true endorsement impossible. Only the cap itself refuses, and it says "hard cap", not "budget exhausted".

- **A chore wore whatever it inherited.** The agent is spawned as a headless `claude`, and a headless `claude` dresses itself from wherever it starts. Started with the project as its working directory, it takes on that project's `.claude/` — and a project is free to keep an `ANTHROPIC_BASE_URL` there that routes to another provider, along with hooks, permissions, MCP servers and a CLAUDE.md. At best that is a rejected model name; at worst the user's graph maintenance runs on a provider chosen for one project, the graph's content goes there, that key pays, and the log says `rc=0`. The server's own environment is a second door of the same kind: when it is started by the autostart hook from inside an interactive session, it carries that session's identity — id, messaging socket and token, bridge id — and copying the environment wholesale hands every chore a badge it should not hold.

  A chore is a system-wide action and now reads like one. The agent runs with `--setting-sources user`, so the plugin and whatever the user set for every session apply and nothing a project keeps in `.claude/` does; the explicit `--settings` allowlist rides on top regardless. It runs from the store's own directory rather than the project's, since the graph is named by the `kg_read(cwd=…)` argument in the prompt and a project directory would still supply the CLAUDE.md and `.mcp.json` that setting sources do not govern. And its environment is the server's minus the `CLAUDE_CODE_*` family and its siblings — every Claude setting a user actually wants lives in user settings and is re-applied from there, so dropping the launcher's copy loses nothing.

20 test files, 517 assertions, suite green.

## [0.9.36] - 2026-08-31

Both changes are instrumentation. The system was doing work and keeping no record of it, so the questions that decide what to build next could only be answered from sources that erase themselves.

### Added
- **Ambient recall writes down what it decided.** `build_prompt_recall` computed the injection and then kept nothing. Every number in five weeks of audits — fire rate, payload, per-node frequency — had to be reconstructed after the fact from Claude Code transcripts, which expire in 30 days. The measurement had a shorter memory than the graph it measured. Each decision now appends one JSON line to `~/.knowledge-graph/recall.jsonl`: the matched terms, the threshold it was judged against, and per candidate its id, level, score, `title_match`, `matched_terms` and `max_term_idf`.

  **Silences are logged too, and that is the point.** A log of injections alone gives no denominator, and — worse — no near-misses. The prompts that scored just under the bar are precisely the evidence any threshold change has to be argued from, and nothing has ever seen them: `no_hits` records carry the top three candidates the gate rejected and the score each one reached. The other outcomes (`full_read_nudge`, `not_a_prompt`, `no_terms`, `all_seen`, `trimmed_to_seen`) are recorded the same way. Terms are stored rather than the prompt — enough to replay a ranking after the transcript that held it is gone, without keeping a second copy of everything typed. A prompt with no registered session writes nothing, deliberately: there is nothing to attribute it to. The file rotates to `.prev` at 8 MB, and every failure path is swallowed to a debug line, because a hook must never break a session.

### Changed
- **`kg_progress` stops destroying the previous stamp.** `set_progress` assigned the state dict, so each write erased the one before it. The maintenance pass is asked to carry "found-but-deferred" forward as the next pass's cursor and the storage could not hold it across two passes; the dispatcher fires every 20 minutes, so each pass reconsidered from scratch and a merge already weighed and refused was re-litigated on the next tick. Git records what changed. Nothing recorded what was considered and rejected — which is the record that stops work repeating.

  Each write now appends a size-bounded copy of the stamp to a 20-entry `_trail` ring carried inside the stored dict. Top-level keys are written through unchanged, so `core.debt` reading `state["last_ts"]` is unaffected; a caller supplying its own `_trail` has it ignored, since the ring is server-owned; long strings and lists are clipped rather than dropped, so a trail stays readable at a glance and cannot grow the graph file without bound.

- **`/kg-maintain` stamps what it declined.** Step 1 now reads `_trail` before working the list, and step 3's stamp gains a `declined` list — one short line per rejection with its reason. That is the half that compounds: a pass that examined a merge and decided against it has done real work, and without recording it the next pass repeats the examination to reach the same answer.

19 test files, 461 assertions, suite green.

## [0.9.35] - 2026-08-28

### Added
- **`kg_rename_node` — ids can finally be changed without losing the graph around them.** There was no rename. The only way to change an id was `kg_put_node` under a new name plus `kg_delete_node` of the old one, which silently drops `_created_ts`, `_useful_ts`, `_last_read_ts` and the whole version history, and strips the node of every edge. The damage that hurts most was delayed and invisible: cross-level edges live in PROJECT graphs and point up to user nodes (`_clean_orphaned_edges` is explicit that this is the sanctioned direction), those graphs are not loaded when the write happens, so nothing rewrites them — and the next time each one loads, its now-dangling edge is garbage-collected with a `logger.warning` nobody reads. Measured on the live graph: **28 user nodes were referenced that way from 10 different project graphs**, several of them the very ids most in need of renaming.

  The new primitive moves the node with every underscore field intact, re-keys its edges in both directions (edges are keyed by `(from, to, rel)`, so a rename is a re-key, not a field write), follows cross-level references into project graphs **on disk** whether or not they are loaded, carries the version history onto the new key, and updates live sessions' seen/preload state so search dedup keeps working mid-session. It refuses a target that already exists rather than merging by accident, skips any project graph that owns its own node by that name (those edges are local references, not cross-level ones), refuses to rewrite into a graph that already has a node named like the target, and reports what it skipped. Available as an MCP tool, as `POST /api/nodes/rename` for bulk passes, and broadcast to the visual editor as `node_renamed`.

### Changed
- **Node ids have a length rule, because they had been growing for four months.** Measured across 1737 nodes in 27 graphs, mean id length by creation month ran 3.4 → 4.5 → 5.1 → 5.1 → 6.4 words; 65% of August ids were over five words and the worst was eleven (`cd-chained-into-git-is-hardcoded-no-allow-rule-beats-it`). The cause was a doctrine gap rather than carelessness: the only guidance anywhere was `"Node ID (kebab-case)"`, while the gist doctrine ("compressed headline") bled into the id, so ids drifted into being claims. The user graph reached 43% over-length and the project graph only 7% — because project ids name *things* and user ids had started naming *conclusions*.

  It is not cosmetic. `store.search` field-weights the id ×3 and `in_title()` fires on a match in id **or** gist, so every extra id word is another token that can set `title_match` and let a weak hit clear the prompt-recall noise gate; long ids also inflate `df` for common technical terms, flattening IDF for every other query. Shortening costs retrieval almost nothing precisely because the gist keeps the words and still counts as a title match.

  The rule — **the id names the subject, the gist makes the claim**, three to five words — is enforced where behaviour actually rides: in the `kg_put_node` schema description, since a hidden skill's body is never loaded. Seven words or more is refused at the write boundary with a steering error; six earns a nudge in the tool result. The refusal applies **only to a create or a rename target**, never to an update: a node named before the rule existed must stay writable, or the graph's own history becomes read-only.

- **Dates in ids are nudged too.** A date records when something was written down, never what it is, so it ages into noise in the one field that has to stay recognisable years later. A dated id is nearly always a node minted per *event* where the graph wanted one enduring node for the subject, updated in place, with `touches` pointing at the current document — the document's own filename is where a date is actually useful.

- **DEBT counts long ids.** The maintenance signal now reports `N long id(s)` (active ids over five words or carrying a date) alongside oversized gists, and weights them into the deficit the same way unconnected nodes are weighted. Without this the drift stayed invisible: unlike a bloated gist, a bad id costs nothing to look at and everything to search.

- **`/kg-maintain` gained an id category — and it is not only repair.** Naming a growing graph is like categorising a growing archive: at the start you cannot know the right categories, and only once a body of work accumulates does the vocabulary the graph *actually* uses become visible. The pass now reads a cluster of related nodes together, asks what they are collectively about, and renames toward that shared vocabulary so siblings read as siblings — capped at five per pass, always through `kg_rename_node`.

- **Documents point into memory, never the reverse** (`/kg-core`, `/kg-ops`). A letter, handover or README naming a node id makes a stationary artifact depend on a moving one: memory keeps evolving and the document rots unnoticed. Write what the document means in its own words; let the node carry the document's path in `touches`.

## [0.9.34] - 2026-08-25

### Fixed
- **Prompt recall was finding the right nodes and then throwing them away in its own re-sort.** Hits were ordered by `(title_match, max_term_idf, score)`, but IDF is computed *per graph* (`store.search_graph_rrf` sets `n_total` from the graph it is searching). A heterogeneous user graph makes nearly any specific term unique in it (idf ~1.0), while a topic-dense project graph makes the very vocabulary its nodes are *about* dull. Ranking on the rarest single term therefore preferred a one-word coincidence in the user graph over a project node matching ten prompt terms — topical density was being punished. Live case: the prompt "rewrite for version 2.0 of MCP ... do you have notes" scored `v0934-venv-selfheal-plan`, `mcp-dep-unbounded-2x-risk` and `mcp2-migration-shape` 1-2-3, and the sort replaced all three with unrelated single-term hits.

  The sort key is now `(title_match, score)`. `max_term_idf` is not deleted — it still gates admission in `_evidence` via `PROMPT_RECALL_MIN_SOLO_IDF`, which is the job it is good at; it was demoted from ranking, which is the job it was bad at. `title_match` stays primary: it is a per-node fact carrying no cross-graph calibration, so it ranks honestly. Six replayed live prompts, hand-labelled: 2/5 carried anything useful before, 5/5 after. `tests/test_recall_rank.py` locks the ordering, the per-graph IDF asymmetry that causes it, that `max_term_idf` still gates, and the cap — 10 assertions, verified to fail against the old sort.

- **A dependency fix could not reach an existing install without a release.** `.deps_ok` recorded only that pip had exited 0 and then latched forever, so a corrected pin in `requirements.txt` was invisible to any venv already marked good — the v0.9.33 mcp cap could only arrive by riding a version bump into a fresh cache dir. The marker now holds the sha256 of `requirements.txt`, and a mismatch re-runs pip.

- **Installed was being treated as working.** After pip succeeds the bootstrap now imports the server module and builds the tool surface before latching the marker — precisely the step that fails under mcp 2.x while every plain import still resolves. The server also preflights the lowlevel decorators before wiring and logs one `KG PREFLIGHT` line naming the installed mcp version against the declared range, instead of surfacing an `AttributeError` raised inside a decorator call that names neither the package nor its version. Verified against a real mcp 2.0.0 venv.

- **The startup path claimed the opposite of the truth on failure.** A failed start now writes `server/.last_start_error` with the classified cause, and `kg-autostart.sh` reports that instead of announcing that the server is warming up.

### Changed
- **kg-core asks for endorsements in so many words.** `kg_useful` calls fell to zero at the v0.9.32 kg-core rewrite, which softened the line to "a node found when needed earns credit" — true, but it names no call and no moment. The description now states all three: the call, the <=5 cap, and wrap-up as the time to make it. This lives in the skill *description* rather than its body deliberately: a hidden skill's body is never loaded, so behaviour depended on must ride in the description.

- **`PROMPT_RECALL_MAX_HITS` is 4** (was 5). An intermediate cut to 3 was measured as the worst setting on the board and reversed: the noisy tail was the ranking inversion above, so trimming only removed slots the right node could have occupied.

## [0.9.33] - 2026-07-31

### Fixed
- **Fresh installs since 28 July built a server that could not start.** `requirements.txt` asked for `mcp>=1.27.1` with no upper bound, and mcp 2.0.0 landed on PyPI at 13:45 UTC on 2026-07-28 — under five hours after v0.9.31 shipped. Because every plugin update installs into a new version-stamped cache dir and rebuilds the venv from scratch, anyone who installed or updated after that moment resolved 2.0.0 and got a server that dies on import-time wiring: 2.x keeps `Server`, `StreamableHTTPSessionManager`, `Tool` and `TextContent` importable but drops the `@server.list_tools()` / `@server.call_tool()` decorators the tool surface is built on, so startup raises `AttributeError: 'Server' object has no attribute 'list_tools'`. The pin is now `mcp>=1.27.1,<2.0.0` (resolving 1.29.0), and updating the plugin rebuilds the venv against it. Installs predating 28 July were never exposed — their venvs resolved 1.x and the `.deps_ok` latch left them there.

  Migrating to the 2.x surface (`MCPServer` with `@tool()` / `add_tool` and `run_streamable_http_async`; `mcp.server.fastmcp` is gone) is deliberate work, deferred rather than rushed under an outage.
- **`server/version.py` was left at 0.9.31 through the 0.9.32 release**, so `/health` under-reported the running version. Both version strings move together here.

## [0.9.32] - 2026-07-31

### Changed
- **kg-core speaks in layers.** The always-loaded description now says what the memory system actually does rather than issuing instructions about it: memory is granular and served in layers, the preload and full read each carry only the most important tier, and *recall* is therefore a depth-fetch — when a gist points at detail those layers didn't carry, pull that node in full. Search keeps its place as the instrument that reaches every tier, with retrieval framed by what it earns a node (prominence) rather than what it saves it from.
- **kg-ops covers the quota gauge.** New recipe in the operations runbook — diagnose, install, verify, undo — for the status line that persists your rolling 5h/7d subscription usage, plus the reading rules an agent needs: which fields are account-global, which belong only to the last renderer, and which timestamp actually gates freshness.

### Added
- **A status line Claude can read its own limits from** (`recommended-setup/statusline.sh`, joining the benchmarked CLAUDE.md and output style as the third companion artifact). Claude Code pipes `rate_limits` — rolling 5-hour and 7-day usage with reset epochs — to the status-line command's stdin and nowhere else: the model never receives it and the harness never persists it, so without this an agent cannot know its own remaining budget and has to ask you to read the number off your screen. The script renders session health (model · quota · cache hit rate · context) and atomically writes each reading to `~/.claude/last-limits.json`.

  The two rate-limit windows can arrive independently, so a frame carrying only one of them still gets written and the other's value is carried forward **with its original observation stamp** (`five_hour_seen_at` / `seven_day_seen_at`) — dropping a live reading because its neighbour was absent is the worse failure, and a carried value that inherited a fresh timestamp would be worse still. `updated_at` answers "is this file being maintained"; the per-window stamp answers "can I trust this number".

  `recommended-setup/README.md` carries the practice this enables, written from the agent's side: the three gauges as three horizons (context governs this conversation, 5h governs today's session shape, 7d governs the week), scoping work into waves that end on a checkpoint, reserving headroom because the wrap-up itself — handover letter, memory writes, final commit — costs quota, calibrating on your own workload rather than trusting general rules, anchoring schedules to `five_hour_resets_at` because the window drifts with first use, and reading a sub-30% cache rate as prompt-prefix churn. It also names the adoption gap plainly: the file changes nothing until the agent is told it exists.

## [0.9.31] - 2026-07-28

### Changed
- **Skills collapsed to one always-loaded voice.** kg-capture and kg-recall folded into kg-core, per the Claude-5 context-engineering shifts (judgment over constraint, no cross-layer repetition, progressive disclosure): the collapsed description is 1,032 chars where the three totaled ~4.4K, mechanics live only in the kg_* tool descriptions, the session protocol lives only in the server preload header, and the description's longest instinct is deliberately *search below the surface* — a rich graph buries needed facts under fresher work; finding one when it matters also feeds the usefulness signal that keeps it alive.
- **Search core sees more and weighs better** (shared by kg_search and recall): tokens contribute their `./_-` subtokens ("CLAUDE.md-cleanup" → claude, md, cleanup), terms match through a light stem (schedule ≈ scheduling), adjacent subtokens form bigram terms with their own co-occurrence IDF, occurrences are field-weighted (id ×3, gist ×2, notes ×1 — the week-2 misrank class came entirely from incidental notes matches), and IDF is sharpened (^1.5) so one term naming the right node isn't outvoted by five dull ones. Minimum recall term length drops 4→3 (the audited misses' core vocabulary — css, woo, smtp — never reached search), with function words that length stopworded instead.
- **Recall speaks only on evidence.** A hit must be corroborated by a second term, near-unique in the graph, or named by the node's id/gist — the week-2 audit's noise mechanism (one moderately common word brushing somebody's notes) stays silent. Injection order is evidence quality, not raw rank-fusion, so a sharp single-term hit no longer falls to the 5-hit cap.

### Added
- **Smear detection + entity consolidation.** Why search kept missing "project-central" vocabulary: chronicle-style capture re-describes entities in prose (a product feature named in dozens of node texts while the hub node that owns it holds three or four edges) — IDF sees a saga, not a signal. Three-part fix at the root instead of an embedding layer: (a) kg-core's capture craft gains *name things once* — event nodes record the delta and edge to the owner; (b) `put_node` gains a hub-mention nudge — a new node whose gist re-describes an entity the graph already names (≥3 holders, undated hub id) gets "an edge to it beats re-describing"; (c) the DEBT line gains a **smeared** factor (`term×count→hub`, slug tokens excluded as namespace) and /kg-maintain gains category 1: consolidate exactly ONE smeared entity per pass. Doctrine changes now have a standing home: express them as debt factors and the scheduled dispatcher propagates them through old graphs automatically.
- **Near-duplicate nudge on node create.** Sessions measurably never search before writing (two audited weeks: 229 writes, 6 searches), so duplicate control moved to the interface: creating a node probes its id + gist against its own graph and the tool result names the closest existing node when the self-normalized similarity ratio clears 0.50 (calibrated leave-one-out on a 380-node graph: ~5% base rate, and the pairs above 0.6 were actual duplicates the graph already carried). A nudge, never a block.

### Fixed
- **`source=fork` re-preloaded on top of inherited context.** Claude Code emits source values v0.9.29 never met; recovery now runs for ANY source except `clear` — the KG markers in the transcript are the evidence of inherited context, so unknown future sources degrade gracefully (a genuinely fresh transcript has no markers). Reused non-compact sessions get the continuity note; compact keeps its full re-render.
- Tests: `tests/test_v0931.py` (26 assertions — term pipeline, ranking fixtures from the week-2 misses, evidence gate, source-agnostic lineage, near-dup nudge and its never-blocks guarantee). Full suite green. Offline replay harness over both audit weeks' real prompts validated the retuning (session scratchpad `eval_search.py`).

## [0.9.30] - 2026-07-24

### Changed
- **Re-render only where the context actually lost the preload.** Refines v0.9.29's session reuse by one rule — re-rendering is fine provided it is not duplication. After *compact* the preload was squeezed into a summary, so the bootstrap re-renders the full core (restoration). After *resume* the forked transcript still contains the original preload verbatim, so re-rendering would duplicate ~10K chars — the bootstrap now injects a one-paragraph continuity note instead (session_id, dedup-state reassurance, `kg_sync` pointer for changes written by other sessions meanwhile). Tests: two new asserts in `tests/test_v0929.py` (16 there); full suite 349 green.

## [0.9.29] - 2026-07-24

### Fixed
- **Session identity survives resume/compact and concurrent sessions — recall dedup no longer resets.** Two live failure modes shared one root: KG sessions were keyed only by project path, and every SessionStart minted a fresh one. (a) Resuming a session re-preloaded memory, re-nagged for the full read, and treated every previously-injected gist as unseen. (b) With two sessions in one project, the older session's recall resolved to the *newest* KG session — the mid-session `session_id` drift the week-1 audit caught — reading and polluting the wrong seen-set. Now the KG session binds to the Claude Code session id every hook payload carries: recall and capture nudges resolve by that binding first, and the bootstrap reuses the existing session on `resume`/`compact` (`clear` still starts fresh). Resume forks mint a *new* Claude sid and rewrite transcript metadata, so recovery uses the one durable anchor — the KG session id our own preload/`kg_read` renders left in the copied transcript. A reused session keeps its seen-set and full-read state: the preload re-render is re-orientation, not amnesia.
- Tests: `tests/test_v0929.py` (14 assertions — binding/rebind, transcript recovery, per-session seen isolation, compact/resume/clear bootstrap paths, full-read state preserved). Full suite 347 green.

## [0.9.28] - 2026-07-24

### Changed
- **Prompt recall only answers a human asking something.** The week-1 live audit (46 injections, 33 sessions) found 20% of recall firings triggered by harness records with no user intent — task notifications and image-paste placeholders — where matching necessarily ran on file paths and boilerplate. Recall now gates on the humanly-typed part of the prompt: notifications stay silent outright, image placeholders are dropped, path tokens reduce to their basename (which can still legitimately match a node's touches), and what remains must clear a small floor of real text (8 chars — short directive prompts like "Yes commit all" landed meaningful hits in the audit and still speak).
- **Recall header trimmed.** The `depth: kg_read(session_id, ids=[...])` invitation was followed 0/46 times in the audit — inline gists suffice. The header is now just `KG recall — memory matching this prompt:` so unused instruction text doesn't water down the payload.
### Fixed
- **Search now loads the session's project graph before scanning.** Graphs load lazily; after a server restart, `kg_search` (and prompt recall, which rides the same core) silently scanned the user graph only until something else happened to load the project graph. Found live during the v0.9.28 smoke test.
- Tests: `tests/test_v0928.py` (12 assertions — notification/image/dragged-path silence, basename floor mechanics, real prompts still speak, lean header, search lazy-load); one v0.9.24 assert updated to the new header contract. Full suite 333 green.

## [0.9.27] - 2026-07-20

### Changed
- **Search terms are IDF-weighted.** RRF ranks are relative, so a ubiquitous term ("user", "works", "project") produced a confident-looking ranking while carrying no signal — the failure showed up live on the first day of prompt recall, injecting tangential nodes on ordinary conversational vocabulary. Each term now contributes `idf/(60+rank)` with `idf = log(N/df)/log(N)`: a term unique to one node keeps full weight, a term in half the graph drops to ~0.15, a term in every node contributes nothing. This lands in the shared ranking core, so **`kg_search` inherits it identically** — rare terms dominate results, generic terms stop polluting them. Prompt-recall thresholds recalibrated to the weighted arithmetic (0.010 single / 0.020 multi): a stack of generic terms sums below the gate and stays silent, one genuinely rare term still speaks.
- Tests: `tests/test_v0927.py` (7 assertions — rare-term dominance and separation, generic-prompt silence, all-node terms contribute zero). Full suite 321 green.

## [0.9.26] - 2026-07-20

### Changed
- **Prompt recall now injects the neighbourhood, not a list.** The search behind `POST /api/prompt_context` already computes connection paths between its hits; the injection now carries them: unseen nodes with full gists, already-seen nodes as bare `id (in context)` anchors — an attention re-focus at near-zero budget — and the path edges between them, so the recall reads as related knowledge rather than isolated lines. Two gates unchanged in spirit: the score threshold decides whether to speak, and at least one **unseen** node must be present — an all-seen match set injects nothing. Edge lines are deduplicated cite-once *per injection* and deliberately have no cross-session tracking: a repeated edge is a small trace refreshing focus the earlier render may have lost, while node gists never inject twice. Every edge endpoint resolves to a rendered node line (sub-threshold and lower-ranked matches get pulled in connector-style; an unresolvable edge is dropped). Budget 1.2K → 2.5K chars; trim ladder drops edges first, then connectors, then seen anchors — a fresh gist survives everything.
- Tests: `tests/test_v0926.py` (13 assertions — tree render, anchor rendering, novelty gate, per-blob edge dedup, endpoint resolution). Full suite 314 green.

## [0.9.25] - 2026-07-20

### Added
- **Maintenance debt — the graph now says when it needs tending.** Every `kg_read` and session preload renders a `DEBT:` line per level after `HEALTH:`, computed server-side (`core/debt.py`) as staleness × activity × deficit: days since the last stamped maintenance pass (saturating at 14; "never" counts full), distinct active days in the last week (node-read stamps plus the v0.9.24 tool-event traffic), and countable wear — oversized gists (the documented compactor-stall root cause) and unconnected active nodes. The factors print raw next to the verdict, so the score can be sanity-checked at a glance; `HIGH` carries the call to action. A pass records itself via `kg_progress` task `"maintain"` — the stamp is what resets staleness, so only real passes count.
- **`GET /api/maintenance_debt`** — a debt survey of every graph on disk (user + all projects), neediest first, with each project's path attached. Built for maintenance dispatchers: pick the top row, run a pass there, the stamp re-sorts the list. Reads graph files directly so surveying doesn't pull every project into server memory.
- **`/kg-maintain` rewritten as a bounded, resumable pass.** The garden philosophy became an executable runbook: orient on the DEBT lines → resume from the `kg_progress` cursor → work capped categories in value order (oversized gists ≤8, unconnected nodes ≤5, duplicate merges ≤3, notes hygiene ≤3) → verify → stamp → report. Hard rules ride along: never invent facts, archived stays untouched, ~25 calls per pass. The skill ships the exact dispatch prompt for running the pass as a subagent — subagents get no preload, so the prompt carries everything.
- The deep-session reminder pool nudges spawning a maintenance subagent when a DEBT line shows HIGH.
- Tests: `tests/test_v0925.py` — 29 assertions (debt math and thresholds, line rendering, stamp-resets-staleness, DEBT placement in read/bootstrap within budgets, disk survey ordering and tool-event activity, endpoint shape). Full suite 301 green.

## [0.9.24] - 2026-07-20

### Added
- **Prompt-matched recall — memory surfaces itself at the moment of relevance.** The `kg-remind.sh` hook now posts each prompt's payload to a new `POST /api/prompt_context` endpoint and prints whatever ready-made hook output comes back — the bash side parses nothing. Server-side, the deterministic full-read nudge (v0.9.21) answers first while the loud `kg_read` is outstanding; after it, the prompt's terms (stopword-filtered, length-floored) run through the existing search machinery with the session's seen-set, and up to 3 *unseen* matching gists ride the hook's `additionalContext` — capped at 1.2K chars, marked seen so the same node never injects twice. A score threshold keyed to RRF arithmetic makes multi-term prompts corroborate before anything injects: precision guards against habituation, the failure mode of every ambient reminder. No model round-trip, no output tokens spent on retrieval — the memory arrives with the prompt. `{}` from the server (or no server) falls back to the staged random pools unchanged.
- **Capture nudges on proven re-derivation.** A new PostToolUse hook (`kg-tool-event.sh`, matcher `Read|WebFetch|WebSearch`) reports tool targets to `POST /api/tool_event`; the server counts them per project in `projects/<slug>/tool_events.json` and speaks only when repetition proves a gap: a file read in a **second distinct session**, or the same URL fetched / query searched twice, with **no node referencing the target** (touches, gists, and notes are checked). The nudge names the target, the evidence, and a ready `kg_put_node` call. First-time reads never nudge; noise paths (node_modules, venv, /tmp, .git, …) never count; throttles keep it rare (10-min session gap, 3 per session, one per target per day). This closes the mid-turn gap: prompt-time reminders can't fire during long autonomous stretches — which is exactly when most reads happen and when the distilled bottom line is still in working attention, capturable for the cost of one node write. (PostToolUse `additionalContext` inline delivery was measured on a live throwaway session before building on it.)

### Fixed
- `server/version.py` had drifted (0.9.21) — `/api/health` under-reported the running version. Synced; the bump script remains the single write path.

### Added (tests)
- `tests/test_v0924.py` — 35 assertions: term extraction, recall precedence/seen-dedup/budget, tool-event thresholds, coverage suppression, all three throttles, REST wrapper shapes. Full suite green (272 asserts) + live smoke through the actual bash hooks against a scratch server.

## [0.9.23] - 2026-07-05

### Added
- **`/kg-ops` — one operations runbook for agents.** The plugin's operational knowledge was scattered across the README, two management scripts, and tribal memory; an agent told "fix the memory server" had to reverse-engineer it. Now it's one skill of recipes (diagnose → act → verify → undo): install/first run, plugin updates (shim refresh, server restart, `/mcp` reconnect), server lifecycle, systemd autostart, Claude Desktop/Cowork connection, configuration, backup/restore, and troubleshooting — including the previously-undocumented ones (`-32000` means the server process died, read the traceback; venv broken by an OS Python upgrade rebuilds on start; `reload=true` after direct disk edits; which log lines are fine). Skill descriptions load into every session, so consolidating ops into one skill also keeps that overhead flat.

### Changed
- `/kg-desktop` (one day old) folded into `/kg-ops` — same setup script, one entry point fewer.

## [0.9.22] - 2026-07-05

### Added
- **Claude Desktop support: `/kg-desktop`.** Desktop's "Add custom connector" dialog rejects local URLs by design — custom connectors are contacted from Anthropic's cloud and can never reach a local server. The correct route is Desktop's own config file, and the new skill automates it: `setup_desktop.py` resolves absolute paths for the machine (Desktop spawns commands without a shell — no `~`, unreliable PATH), backs up `claude_desktop_config.json`, and idempotently registers a stdio bridge. The bridge (`desktop_bridge.sh` → `mcp-remote`, Node.js ≥ 18) health-checks the shared HTTP server and auto-starts it when Desktop launches first, so Desktop — and Cowork, which receives config-file servers through Desktop's sandbox bridge — becomes just another client of the same graph. `--remove` undoes the entry. Desktop sessions get no hook preload; orientation arrives with the first `kg_read`.

## [0.9.21] - 2026-07-04

### Changed
- **The loud `kg_read` is now enforced, not hoped for.** Field evidence from a real session: the preload fired, the model announced recall, and the full-graph read never happened — the session ran on 14 of 45 project gists and 0 of 48 user gists (the bootstrap ladder routinely drops the whole user level to higher-scored project nodes). Three mechanisms close the gap. (1) The preload header is now directive: it names itself a PARTIAL view and requires one `kg_read(session_id)` before substantive work. (2) The "I have recalled KG Memories" announce moved to where it belongs — the first full read appends the instruction to its own output, so the ritual can no longer complete on the preload alone. (3) The reminder hook is deterministic while it matters: the server tracks `full_read_ts` per session (new `GET /api/session_state?project_path=` endpoint), and `kg-remind.sh` emits the full-read nudge on *every* prompt until the flag flips — previously that nudge was one random pick out of seven, so a short session had good odds of never seeing it. Any hook-side failure (server down, no session) falls back to the staged random pools.
- **Compactor grace-period stall logs once, not twice a minute.** A sprint-week graph can sit over its char budget with every active node inside the 5-day grace period — nothing is eligible, and the compactor used to log "Compacting graph" at INFO on every 30s tick before silently giving up. It now logs the stall once, with the reason and the graph's namespace ("over budget but all N active nodes within grace — compaction deferred"), then stays at debug until eligibility can change. Compaction logs now name the graph they act on. The render-time ladder still guarantees `kg_read` fits inline regardless.

### Added
- Tests: `tests/test_v0921.py` (bootstrap header directive, full-read session tracking, session_state lookup, compactor stall single-log and recovery).

## [0.9.20] - 2026-07-03

### Changed
Two readability tweaks from first-person reading experience of the node-centric format:
- **The archived list renders alphabetically.** It is id-only, so score ordering was invisible to the reader anyway — alphabetical clusters related name prefixes (`kg-*`, `night-ops-*`) and makes a long list scannable. Score still governs what the degradation ladder *drops*; only the display order changed.
- **Write-time nudge for oversized gists.** Gists past 300 chars read as walls in the full-graph render and break the scan rhythm. `kg_put_node` now appends a note to its response when a gist exceeds the limit — the write is never rejected (long gists are sometimes right), but the writer is nudged at the moment the fix is cheapest. The maintain skill's oversized-gist pass handles existing stock; this stems the inflow.

## [0.9.19] - 2026-07-03

### Changed
- **The session-start preload is a compact core (≤10K chars) — and the loud `kg_read` never repeats it.** Hook `additionalContext` rides a much smaller inline window than tool results: measured on Claude Code 2.1.199, hook output stays inline up to ~10,100 chars and spills to a persisted file (2KB preview) at ~10,150 — so v0.9.17's full-render preload silently landed in a file on any real graph. The bootstrap now renders under a hard `BOOTSTRAP_CHAR_BUDGET` (10,000, instruction header included — render == charge covers every character the hook emits) with an extended degradation ladder: archived anchors first, then edge citations, then whole active gists, lowest-scored first — the hubs stay. The two channels then split the work: the silent preload gives the model its top-scored orientation before the first tool call; `kg_read(session_id)` renders the full graph with preloaded gists collapsed to id-only `(preloaded)` anchors, spending its 40K budget on everything the compact core had to drop. Explicit `ids=[...]` reads are never deduped.
- **Memory loading is no longer invisible.** The SessionStart hook emits a `systemMessage` one-liner (active node counts, gists inline, session id) so the user sees memory load instead of inferring it from a missing tool call.
- **The prompt-time reminder is stage-aware.** `kg-remind.sh` now weights its nudge pool by session depth (transcript size): early prompts point at recall, mid-session at capture, deep sessions at maintenance and wrap-up (`kg_useful`). New reminder: subagents never receive the preload — the dispatching session puts the relevant gists or `kg_*` instructions in their prompts (measured: SessionStart does not fire for Agent-tool subagents).

### Added
- Tests: `tests/test_v0919.py` (30 assertions: bootstrap budget cap and hub-first selection, read dedup and freed budget, preloaded-set session tracking with save/load round-trip).

## [0.9.18] - 2026-07-03

### Added
- **`kg_useful` — explicit usefulness endorsement.** At session wrap-up, the agent marks up to 5 nodes that *actually helped*, judged against real results rather than mid-flight promise. One vote per node per session; the ledger lives on the session, decaying timestamps (90-day half-life) on the node. Reads deliberately do **not** feed this signal: a well-formed gist is self-sufficient, so counting reads would reward the weakest gists. A like is not a content write — versions, recency, and sync state are untouched.
- **Usefulness in archival scoring.** The score blend is now 0.25 recency / 0.40 connectedness / 0.35 usefulness (percentile ranks). Percentile assignment became tie-aware (equal raw values share the average rank), which the usefulness column requires — with most nodes at zero likes, index-order percentiles would have spread identical values across the whole range arbitrarily; an all-zero column now collapses to a uniform 0.5 and distorts nothing.
- Tests: `tests/test_v0918.py` (22 assertions: like budget/ledger/decay, scorer blend and tie-awareness, namespace helpers, namespace meta).

### Changed
- **Namespace seam (internal, no behavior change).** Graph keys are constructed and inspected only through `core.constants` helpers (`project_namespace`, `is_project_namespace`, `namespace_kind`) instead of scattered string literals, and every graph file now carries `_meta.namespace = {kind, owner}`. This is the storage-level seam for future namespace kinds (role/org graphs, multi-user owners) — they slot in without a migration.

## [0.9.17] - 2026-07-03

### Added
- **Memory preloaded at turn 1.** The SessionStart hook now injects the rendered knowledge graph as `additionalContext` when the server is healthy (`/api/session_bootstrap`: registers the session, returns the same text kg_read would produce — one renderer, two delivery channels). The session starts with memory already in context: zero tool calls, a full model round-trip saved. Silent miss while the server bootstraps; classic kg_read remains the fallback.
- **Search v2.** `kg_search` returns a focused, capped answer instead of an unbounded JSON dump: top-5 hits with full treatment, connections *between* the hits (union of pairwise shortest paths — connector nodes as id+gist plus the path edges), and remaining matches as one-liners, all under a 10K-char ceiling with a value-ordered trim ladder. **Session-aware dedup:** the server tracks which gists each session has already been shown (preload, reads, prior searches); a seen hit renders as a one-line gist reminder — notes are never re-dumped, they stay one explicit node read away. Gists + edges are the working currency; notes are on-demand depth.

### Changed
- **kg_read full-graph format is node-centric.** Nodes render in cluster order (connected communities contiguous, highest-degree hub first) with their relationships indented beneath them — a cluster reads as one coherent knowledge paragraph instead of three flat sections joined by id. Each edge is cited exactly once, under its first-rendered endpoint (`→`/`←` show direction); doubling citations would tax the character budget that gists need. A node's complete neighbourhood is always visible in single-node reads. The estimator measures the actual planned render (headers, node lines, citations, anchors), so render == charge stays exact by construction.

## [0.9.16] - 2026-07-03

### Changed
- **Budgets are now exact rendered characters — kg_read is guaranteed to land inline.** The old budget was estimated tokens with flat per-item charges (`BASE_NODE_TOKENS`, `TOKENS_PER_EDGE`, `ARCHIVED_ID_TOKENS`, …) that drifted from real rendered sizes — long kebab ids and long relationship names rendered far past their charge, so on some projects the combined kg_read output overflowed the MCP client's inline tool-result limit and landed in a persisted file the model only sees a 2KB preview of (the root cause behind "read the overflow file before working"). The estimator now measures the *exact strings* kg_read renders (`core/render.py` is the single source of truth for line rendering; the estimator charges `len(line)+1`), the per-level budget is `MAX_CHARS_PER_LEVEL` (17,500), and a render-time **degradation ladder** enforces a hard `READ_CHAR_BUDGET` (40,000) on the combined output for graphs the compactor hasn't maintained yet: lowest-scored archived anchors are hidden first (with a count and a kg_search pointer), then lowest-value live edges — active gists are never dropped. The budget is deliberately **not configurable**: the `KG_MAX_TOKENS` env override is gone; the inline guarantee is an invariant, not a tuning exercise.
- **Node reads are compact text, not raw JSON.** `kg_read(id)` used to dump the node as indented JSON including internal fields (`_last_read_ts`, …). It now renders gist / notes / touches plus — new — the node's own edges, giving crumb-following its next hops for free.

### Added
- **Batch node reads:** `kg_read` accepts `ids: [...]` to read several nodes in one call — sequential crumb-following round-trips collapse into one.
- **Session reuse:** `kg_read` accepts `session_id`; a valid one is reused instead of registering a fresh session. Previously *every* read carrying `cwd` (which the schema required) minted a new session and fsynced `sessions.json` — four crumb reads = four sessions. `cwd` is now only required on the true first call.
- Tests: `tests/test_v0916.py` (33 assertions: exact-char render==charge, ladder ordering/floor/guarantee, compact node format, session reuse, edge-cleanup preservation).

### Fixed
- **Cross-level and artifact edges were silently deleted on every server restart.** `_clean_orphaned_edges` removed any edge whose endpoint wasn't a node in the same graph — which is exactly what a project→user cross-level edge or a file-path (artifact) edge looks like locally. Cleanup now keeps endpoints that are artifact paths (`/` or `~`) or resolvable in another loaded level; only true dangling references (deleted nodes) are removed. Doctrine settled alongside: cross-level edges belong in the *project* graph pointing up to user-level nodes.

## [0.9.15] - 2026-07-02

### Fixed
- **Storage git auto-commit never fired in normal operation.** Commits of `~/.knowledge-graph` only happened in `manage_server.sh` on managed `stop`/`restart` — but in real life the server is launched by the SessionStart hook and dies with machine shutdown, so a managed stop (and thus a commit) never ran; on one machine the last `Auto-save` commit was three weeks stale despite daily use, and a crash during that window would have lost the entire uncommitted history. The server now commits **periodically from within the Python process** (`core/autocommit.py`, daemon thread on the same Event-wait pattern as the store's saver thread), so history accumulates no matter how the server is started or killed. Every `KG_AUTOCOMMIT_INTERVAL` seconds (default 900 = 15 min; `0` disables) it commits pending changes with the existing `Auto-save YYYY-MM-DD HH:MM` message convention — only when the tree is actually dirty (no empty commits), silent no-op when the storage root has no `.git` (a later `git init` is picked up without a restart), and git failures are logged but never fatal. A final best-effort commit also runs on graceful shutdown (SIGTERM/SIGINT), ordered after the store's disk flush so it captures the final state. `manage_server.sh commit_storage()` stays as-is for `kg-memory commit` and the managed stop paths.

### Added
- `KG_AUTOCOMMIT_INTERVAL` environment variable (documented in README configuration table and as a commented example in `server/memory-mcp.service`).
- Tests: `tests/test_autocommit.py` (8 tests on throwaway git repos — dirty/untracked commit, clean-tree no-op, no-`.git` no-op, interval parsing, disabled mode, idempotent shutdown commit, periodic loop firing). Runs standalone like the other suites or via pytest.

### Changed
- README "External backups" git section rewritten: `git init` in `~/.knowledge-graph` is now a one-time setup — the server handles the periodic commits itself. ARCHITECTURE.md storage layer documents the design.

## [0.9.14] - 2026-06-11

### Fixed
- **First run actually works now.** There was no venv bootstrap anywhere: a fresh install had no Python environment and no documented step to create one, so `kg-memory start` failed with `venv/bin/python: No such file` and the plugin could not complete first run on any machine where the venv hadn't been built by hand. Worse, plugin updates install into a fresh version-stamped directory, so even a hand-built venv vanished on every update. `manage_server.sh` and `manage_visual.sh` now build the venv automatically on `start` when it's missing or incomplete (one-time ~1 min, with a marker file so a half-finished `pip install` is retried rather than trusted).

### Added
- **Server auto-start.** A bundled SessionStart hook health-checks the memory server on every session and launches it in the background when down — combined with the venv bootstrap, "install → restart → done" is now literally true. The hook only ever *starts* the server; it never stops or restarts a running one. When a session connected while the server was down, the hook tells Claude to verify health and ask the user for the one step only they can do: `/mcp` → `plugin:knowledge-graph:kg` → **Reconnect**.

### Changed
- `kg-core` skill guidance updated: connection-refused is now "warming up — retry, then /mcp Reconnect", not "ask the user to start the server".
- README rewritten around the real first-run experience: honest "Done." claim, Python 3.10+ requirement stated up front, and a "Your first five minutes" section (what Claude says, how to seed the graph with `/kg-extract` and `/kg-scout`, what to ask next session). Wiki Installation/Server-Management/Home updated to match.

## [0.9.13] - 2026-06-11

### Fixed
- **Visual editor writes to project graphs.** Creating a node, editing gist/notes/touches inline, and creating an edge on a *project* graph all 500'd: the editor's session has no project path registered, and unlike the read/recall/delete paths (fixed in 0.9.12), the write paths never accepted `project_path`. Now `POST /api/nodes` and `POST /api/edges` take `project_path`, the editor sends it, and all node/edge operations share one graph-addressing helper in the store (`_resolve_graph_key`).
- **REST `DELETE /api/edges/...` always failed** — the endpoint passed its arguments to the store positionally in the wrong order (`level` landed in `from_ref`, `rel` in `level`), so every call errored. Latent because the editor UI has no delete-edge action yet; fixed and covered by tests.
- **Refill dead band: graphs settled permanently with most knowledge stranded archived.** Refill only triggered below 0.6×budget but filled to 0.8×, so any graph sitting between 0.6 and 0.8 (where compacted graphs naturally land) never refilled — observed live: a user graph at 33 active / 128 archived with ~560 tokens of unused headroom and refill never firing. Refill now acts whenever the graph is below the 0.8 fill ceiling (single threshold; no-thrash is preserved by the ceiling sitting under the 1.0 archive threshold, plus skipping refill on any tick that just archived).
- **Refill blocker: one oversized gist stranded everything behind it.** A top-scored candidate too large for the remaining headroom used to stop the whole pass ("reconsidered next time" — but the estimate only grows, so it never fit later either). Non-fitting candidates are now skipped and smaller ones behind them promote. The fit check uses an exact O(degree) promotion delta from the adjacency index, so skipping is cheap even on dense graphs.
- **Compaction token bookkeeping.** Archiving re-measures the graph instead of subtracting the node cost (which ignored the remaining anchor cost and edges going dead); the resurrection pass now re-measures too and reverts a swap that would push the graph back over budget.
- Server shutdown ran the store flush twice, each waiting up to 5s for the sleeping maintenance thread (~10s exit latency). `shutdown()` is now idempotent and wakes the thread via an event — exit is immediate.
- `kg_search` iterated graph dicts without the store lock — racing the background maintenance thread (archival, pruning) could blow up mid-scan. Search moved into the store behind the lock; `read_graphs` now returns snapshot copies instead of live dict references for the same reason.

### Security
- **Healer ReDoS, complete fix (CodeQL alert 12).** The 0.9.12 boundary-lookahead fix killed one witness family (`<notes` glued to junk) but a gist full of *viable* opener starts (`<notes <notes …`, no `>` anywhere) still made every start scan the unbounded `[^>]*` tail to end-of-string — measured quadratic (7.7s at 140KB; healing runs on every write and load). The attribute tail is now bounded (`[^>]{0,256}`), making the scan linear (100ms on the same witness). Regression-tested with both witness families.
- **WebSocket Origin validation.** Browsers do not apply CORS to WebSocket upgrades, so any web page could previously open `ws://127.0.0.1:8765/ws` (or the editor's `:3000/ws` proxy) and silently receive every graph broadcast — node contents included. Upgrades with a non-local `Origin` are now rejected; absent `Origin` (non-browser clients) still works.
- **Host-header validation (anti DNS-rebinding)** on every HTTP/WebSocket request to both servers: requests not addressed to `localhost`/`127.0.0.1`/`::1` (or the explicitly configured bind host) are rejected with `421`, closing the rebinding route around CORS for the REST *and* MCP endpoints.
- **Server-side identifier validation.** Node IDs, edge endpoints, and rel types are validated to a safe character set at the write boundary (REST and MCP alike) — markup can no longer enter the store and reach surfaces that render it. Existing graphs are unaffected (scanned: all existing IDs already conform).
- **Visual editor XSS hardening:** `escapeHtml` now escapes quotes (attribute contexts); the edit-modal title escapes the node ID; node IDs are no longer interpolated into inline `onclick` JS (entity-escaping cannot make that context safe — replaced with data attributes + listeners).
- SECURITY.md now states the trust boundary explicitly (local processes; session IDs are namespacing, not auth) and documents the new guards.

### Changed
- REST API construction extracted from the server entrypoint into `mcp_http/rest.py`; RRF search logic moved from the MCP tool handler into `MultiProjectGraphStore.search()`. Both moves make the wiring layer testable in-process.
- REST write/read endpoints return `400` with a real message on validation errors (was a generic `500`); the editor proxy forwards upstream status + detail, and editor toasts show it.
- `GraphPersistence` takes `project_path` as a constructor parameter (was injected post-hoc as a private attribute).
- Tests: `tests/test_v0912.py` renamed to `tests/test_core.py` (57 assertions, including new refill skip-not-break and validation coverage); new `tests/test_http.py` exercises every REST endpoint in-process with the editor's exact addressing shape, plus the WebSocket Origin policy (28 assertions). Both run with the project venv, no pytest:
  `./venv/bin/python tests/test_core.py && ./venv/bin/python tests/test_http.py`

## [0.9.12] - 2026-06-01

### Added
- **Reverse refill (self-healing active set).** Compaction previously only moved nodes *down* (active → archived) when over budget; nothing moved them back up except a manual `kg_read(id)`, so a graph could sit far below budget with valuable knowledge needlessly collapsed — especially now that the edge-accounting change frees real headroom. A new pass (`Compactor.refill_if_room`) promotes the highest-scored archived nodes back to active to use spare budget. A hysteresis band prevents thrashing: refill only **triggers** below `REFILL_TRIGGER_RATIO` (0.6×max) and only **fills up to** `COMPACTION_TARGET_RATIO` (0.8×max), leaving a stable 0.6–1.0 dead zone where neither refill nor archiving acts. Runs as part of the existing compaction step (on writes and the periodic maintenance tick).
  - Refill **re-scores after each promotion** and connectedness now weights an edge to an archived neighbour at `ARCHIVED_EDGE_WEIGHT` (0.2) instead of 0. Together these fix a ratchet where a well-connected cluster that archived *together* could never be refilled — every member looked disconnected because all its neighbours were archived too. Now a dense archived hub floats up the refill order and, once promoted, makes its neighbours' edges live so the cluster is resurfaced as a unit within the same pass. Scoring builds an edge-adjacency index once per pass, so iterative re-scoring stays fast (sub-second even on large dense graphs).
- **Self-healing for malformed nodes.** A node is meant to arrive as separate fields — a short `gist` headline plus a `notes` list. Occasionally a client serialized the *whole* node (gist + notes + surrounding tool-call markup) into the single `gist` string and left `notes` empty. Because every full-graph `kg_read` renders `id + gist`, those oversized gists (often 20–30× their intended size) dominated the token budget. The server now repairs this automatically in two places, both driven by one function (`core.healer.heal_node_fields`): on **write** (`put_node` sanitizes before storing, so corruption never lands) and on **load** (each graph is healed when first read from disk, and the repair is written back). Healing splits the real headline out and recovers the embedded `notes`/`touches` into their proper fields; it is idempotent and never overwrites caller-supplied data, so already-clean graphs are untouched.
  - **What you'll see on first upgrade:** the server logs a `WARNING` per repaired node plus an `INFO` summary `Healed N corrupt node(s) on load` the first time it opens an affected graph, then rewrites the file. This is expected and one-time — subsequent loads find clean data and do nothing. Affected nodes shrink and their previously-lost notes reappear in `kg_read(id)` and the visual editor.
  - **Token impact:** on graphs that had accumulated these malformed nodes, active-graph cost dropped substantially (observed −30% to −70% per graph). If `kg_*` MCP calls were consuming an outsized share of your context, this is the likely cause and fix.
  - As with any data-touching change, **take a backup before upgrading** — see [Data and Backup](https://github.com/mironmax/claudecode-plugins/wiki/Data-and-Backup). The healed write keeps the usual `.prev` rolling backup, but a point-in-time snapshot is cheap insurance.

### Changed
- **Edges are "resurfacing strings": render == charge.** `kg_read` now shows — and the compaction budget now charges — an edge only when at least one endpoint is active (or is a file/artifact reference, which is always present). An edge between two archived nodes is a dangling thread you can't pull: it is suppressed from `kg_read` output and no longer counted against the active token budget. It reappears automatically the moment either endpoint is promoted, so nothing is lost. A single predicate, `core.utils.edge_is_live`, drives both the renderer (`format_graph_compact`) and the estimator (`TokenEstimator`), so visible output and budget can never drift apart.
  - Impact: on large graphs where most nodes are archived, the archived–archived edges were 85–97% of the edge count and dominated the token budget — starving the active set (e.g. a 117-node project showed only 1 active node) and bloating `kg_read` output toward the tool-result limit. With the fix, those graphs keep far more nodes active and produce much shorter output. Graphs with no archiving are byte-identical — no change.
  - No data migration: existing graph files are untouched; archived nodes and all their edges remain on disk, in the visual editor, and in `kg_search`. Compaction never *re-archives* retroactively — the cheaper edge accounting only means *fewer* nodes archive on future compactions, never more.
- `kg_read` `HEALTH:` line now reports active nodes and **live** edges (matching the visible sections), instead of raw on-disk totals — so `avg edges/node` is no longer skewed by hidden archived–archived edges. "Orphans" in the health line now means active nodes with no live edge (a genuinely useful reachability signal).

### Fixed
- Token estimator charged **all** edges (including orphan-endpoint edges that `kg_read` already suppressed) and counted archived nodes as free — two inconsistencies between what was rendered and what was budgeted. The estimator now charges active nodes (id+gist), archived nodes (a 5-token ID anchor), and live edges only — exactly what `kg_read` renders.
- Reconciled two conflicting active-token-budget defaults: `GraphConfig.max_tokens` was `5000` while the server env fallback was `4000`. Both now derive from a single `MAX_TOKENS` constant in `core/constants.py` (5000), still overridable via `KG_MAX_TOKENS`.
- The orphan-pass `ARCHIVED_ID_TOKENS` was a local literal with no shared source; it is now a single constant in `core/constants.py` shared with the estimator.

## [0.9.11] - 2026-05-22

### Fixed
- Visual editor "Recall" action: switched broken `POST /api/nodes/{level}/{id}/recall` proxy to the existing `GET /api/nodes/{level}/{id}` REST read (which auto-promotes archived/orphaned nodes). Previously the action silently 500'd.
- Visual editor WebSocket URL: derived from `window.location` instead of hardcoded `:3000`, so the page works on any `EDITOR_PORT`.
- Systemd unit (`server/memory-mcp.service`) rewritten to invoke `~/.local/bin/kg-memory` (oneshot + RemainAfterExit). Previous unit pointed at `~/.claude/plugins/cache/maxim-plugins/memory/latest/server` — the wrong plugin name and a path that does not exist.
- `server/version.py` synced to plugin.json (was lagging at 0.9.9).
- `manage_server.sh`: removed `migrate` subcommand and `auto_migrate()` — they referenced `tools/migrate_storage.py` which was deleted in 0.9.1.

### Changed
- Docs: `ARCHITECTURE.md` scoring formula updated to `0.33×recency + 0.66×connectedness` (richness was dropped in 0.9.9); compaction budget shown as ~4000 tokens; stale version header removed.
- Docs: `wiki/Skills-Reference.md` skill table refreshed (six skills, hidden vs user-invocable split); `kg-extract` section rewritten to match the current Tier 1 / Tier 2 model and subsystem/component vocab; stale char-count table replaced with a one-line note.
- Docs: `wiki/Knowledge-Graph-API.md` `kg_search` entry now documents RRF ranking and actual return shape (gist + notes + score, not full node body).
- Docs: `wiki/Installation.md` corrected — six skills, not four; `kg-maintain` is auto-loaded *and* user-invocable, not "hidden."
- Docs: `wiki/Design-Decisions.md`, `Data-and-Backup.md` token references updated 3000 → 4000.
- Docs: `knowledge-graph/README.md` inline mini-changelog removed; replaced with a pointer to this file.
- Docs: `visual-editor/README.md` rewritten — it was stuck at the Read-Only MVP era. Now a dev-oriented overview pointing at `VISUAL_EDITOR_GUIDE.md` and the wiki for user-facing content.
- Skills: `kg-scout` and `kg-extract` frontmatter explicitly marked `user-invocable: true` for consistency with `kg-maintain`.
- Settings: `.claude/settings.local.json` cleaned of legacy MCP tool names (`kg_ping`, `kg_register_session`, `kg_progress_get`/`_set`, `kg_recall`) and shell-parsing artifacts (`Bash(rtk *)`, `Bash(done)`, `__NEW_LINE__` entries).

### Removed
- `knowledge-graph/mcp` — orphan thin wrapper around `manage_server.sh`, not referenced anywhere.
- `knowledge-graph/visual-editor/start.sh` — redundant with `manage_visual.sh` and had a port-3001 default that contradicted everything else.

## [0.9.10] - 2026-05-20

### Added
- Docs guidance on enabling Claude Code plugin auto-updates for the `maxim-plugins` marketplace (off by default for third-party sources). Covers `/plugin` UI flow, manual `/plugin marketplace update maxim-plugins`, and the `/reload-plugins` prompt that follows an automatic version bump.

### Changed
- Install flow simplified to three user-visible steps: marketplace add → plugin install → restart Claude Code.
- UserPromptSubmit memory hook moved into bundled `hooks/hooks.json` — auto-registers on plugin enable; no `~/.claude/settings.json` edits required.
- `install_command.sh` demoted to optional (only needed for the `kg-memory` / `kg-visual` shell command symlinks). Also performs idempotent cleanup of the legacy hook entry in `settings.json` for users upgrading from earlier installs.

### Fixed
- Docs no longer reference the non-existent `~/.claude/plugins/knowledge-graph/` flat path. Bundled assets are addressed via `${CLAUDE_PLUGIN_ROOT}` inside the plugin and `find ... | sort -V | tail -1` in the single user-facing shell command that still needs it.
- Server Management docs corrected: the HTTP MCP server requires manual start; it is not auto-started by Claude Code (previously implied otherwise).
- Configuration docs corrected: tunable env vars are read from the shell where the server is started, not from the plugin's bundled `.mcp.json` (which is overwritten on update).
- Systemd auto-start instructions use `cp` (not `ln -s`) so the unit file survives plugin cache churn on updates.
- Knowledge-graph plugin README version field synced to plugin.json (was lagging at 0.9.8).

## [0.9.9] - 2026-05-19

### Security
- Add `safe_project_path()` validator — user-supplied project paths are now constrained
  to within the user's home directory, preventing path traversal (CWE-022)
- Remove exception details (`str(e)`) from all HTTP 500 responses in the visual editor
  backend; errors are logged server-side only (CWE-209)
- Add `SECURITY.md` with responsible disclosure instructions and GitHub Advisory reporting
- Add `.github/dependabot.yml` for automated weekly pip dependency updates

### Changed
- Scorer redesign: drop `richness` dimension, refine `connectedness` to count only edges
  to/from active nodes (in×0.66 + out×0.33), add `resurrection` pass after archiving
- Grace period now based on `_created_ts` only — updates and reads no longer reset it,
  preventing active nodes from becoming permanently immune to compaction
- After archiving pass, a resurrection pass promotes any archived node that outscores a
  freshly-archived one by ≥ 0.05 margin
- `score_all()` accepts `include_archived` flag to support resurrection scoring
- Add `_created_ts` and `_last_read_ts` fields to `Node` TypedDict
- Update SKILL.md scoring formula description to match implementation
- Export `safe_project_path` from `core.__init__`

## [0.9.8] - 2026-05-14

### Changed
- `kg-maintain` hygiene passes (water/prune/fertilize) always run regardless of graph health score

## [0.9.7] - 2026-05-13

### Added
- Visual editor three-panel layout: projects list, graph canvas, details/connections panel
- Inline field editing in details panel
- Connections panel showing node edges

### Fixed
- WebSocket handshake: route `/ws` through ASGI dispatcher so visual editor stays Online

## [0.9.6]

### Fixed
- Removed tiered backup table (hourly/daily/weekly) and git auto-commit section from README and wiki — neither was ever implemented.
- Documented actual built-in protection: atomic writes + single `.prev` rolling copy per save.
- Added user-managed external backup guide: git (simple snapshots) and Borg (dedup-friendly, better for high-frequency data).
- Same corrections applied to wiki (`Data-and-Backup.md`, `Configuration.md`, `Home.md`).

## [0.9.5]

### Added
- `kg_search` upgraded to Reciprocal Rank Fusion: multi-term queries tokenize, rank per term by occurrence, then merge into a single unified ranking — user and project results sorted together by score.
- Without `session_id`, `kg_search` falls back to searching all loaded project graphs (best-effort); response includes a note explaining the limitation.
- `kg-maintain` made user-invocable (`/kg-maintain`): focused pass — health check, prune if large, fertilize, water — and reports what changed.
- `kg-visual` shell command added to `install_command.sh` (was previously a manual symlink).

### Changed
- Edge notes removed from full-graph `kg_read` output — edges show as `from --rel--> to` only; notes appear in single-node reads (same pattern as node notes).
- Size notification threshold raised 40K → 45K chars; tone shifted from warning to informational note suggesting `/kg-maintain`.
- All skill language rewritten for calm, professional tone — imperative/enforcement framing replaced with collaborative guidance throughout `kg-core`, `kg-recall`, `kg-capture`, `kg-maintain`.
- `kg-core` skill body: new Server Operations section documenting both `kg-memory` and `kg-visual` with subcommands, ports, install path, troubleshooting.

## [0.9.4]

### Added
- README Prerequisites section: Python 3 + pip install instructions for macOS, Linux, Windows.

### Changed
- Skill guidance rewritten across all four hidden skills for sharper, more actionable capture / recall / maintain / extract rules. `kg-extract` introduces the two-tier index (subsystem + component) with skip-decision gist patterns.
- Quick Install: marketplace URL changed to `https://github.com/mironmax/claudecode-plugins`; setup script path uses `find` to auto-locate the version-stamped cache dir.
- `kg-remind` hook rotates through 18 targeted prompts (was a single generic reminder).

### Removed
- Scheduler plugin — superseded by Claude Code's native `/schedule` skill.

## [0.9.3]

### Added
- Three-tier compaction: active → archived → orphaned. Pass 1 archives lowest-scored active nodes; pass 2 orphans lowest-connectivity archived nodes when archived section exceeds 30% of token budget. Orphans are invisible in `kg_read`/`kg_sync`, searchable via `kg_search`, chain-rescued when adjacent archived nodes are read, permanently deleted after 365 days without recall.
- Four hidden skills: `kg-core`, `kg-capture`, `kg-recall`, `kg-maintain`. Descriptions rewritten to fit the 1,536-char per-skill hard limit (previously silently truncated at 38–65%).
- `UserPromptSubmit` hook for ambient memory reminders — injects a short prompt via `additionalContext`. `install_command.sh` wires it into `~/.claude/settings.json` idempotently.

### Changed
- Encoding doctrine added to `kg-capture` (telegraphic gist style, gist vs notes boundary, edge-first principle).
- Garden rhythm added to `kg-maintain` (water/prune/fertilize as proactive tending alongside reactive triggers).

## [0.9.1]

### Changed
- Compaction tuning: `COMPACTION_TARGET_RATIO` 0.9 → 0.8 (wider buffer), `GRACE_PERIOD_DAYS` 3 → 5, `ORPHAN_GRACE_DAYS` 30 → 365.
- `constants.py` is now the single source of truth — env var fallbacks import from constants; service file no longer overrides.
- Storage safety: atomic writes + `.prev` rolling backup on every save.
- Docs: values in skills and docs reference env vars and `constants.py` instead of hardcoded numbers.
- Added comparison with MemPalace and Claude Code Auto-Memory in wiki.

### Removed
- `migrate_storage.py` and `replay_sessions.py` (superseded by centralized storage).

## [0.9.0]

### Changed
- Consolidated MCP tools from 13 → 8. Removed `kg_ping`, `kg_session_stats`, `kg_register_session`, `kg_recall`, `kg_progress_get`, `kg_progress_set`.
- `kg_read(cwd)` initializes session and returns `session_id`.
- `kg_read(cwd, id)` reads a single node and promotes archived nodes.
- `kg_progress` merges get/set — omit `state` to read, include to write.
- `kg_delete_node` and `kg_delete_edge` auto-resolve graph level (no `level` param needed).
- Default `KG_MAX_TOKENS` raised to 4000.

## [0.8.0]

### Added
- Zero-setup behavioral guidance via four hidden skills — no CLAUDE.md required.
- Self-awareness mechanism: Claude checks graph is loaded before any task.

### Changed
- Restructured into six focused skills (four hidden + two user-invocable).

## [0.7.2]

### Added
- User profile as top-priority capture target — calibrate explanations to the user's domain knowledge.
- `CAPTURE.md` "Preserving the Why" section: notes as the home for rationale, recalled on demand.
- `RECALL.md`: recall active nodes for their notes when rationale matters.

### Changed
- Recommend disabling Claude Code's built-in auto-memory (conflicts with KG).
- Recommend a single global `CLAUDE.md` only; project-level files cause instruction conflicts.

## [0.7.1]

### Changed
- Renamed skills to `kg-` prefix to avoid name collisions across plugins.

## [0.7.0]

### Added
- Server tools (`migrate_storage`, `replay_sessions`) and `manage_visual.sh`.
- Scheduler plugin (new, second plugin in the marketplace): MCP stdio server for task scheduling, usage-monitor hook, launcher, installer, skills, templates.
- `kg_search` for full-text search across active and archived nodes.

### Changed
- Plugin renamed from `memory-plugin` to `knowledge-graph` to better reflect the underlying model.
- Centralized storage moved to `~/.knowledge-graph/`.
- Write-through persistence: every mutation saved to disk immediately.
- Multi-session-safe server restart with `setsid` + PID validation.
- Visual editor UI/CSS overhaul; streamable server, store, session manager enhancements.
- Architecture docs rewritten.

## [0.6.1]

### Fixed
- Reversed `kg_read` / `kg_register_session` order so the project graph loads on startup.
- `kg_register_session` accepts a `cwd` parameter for automatic `graph.json` resolution.
- Sync timestamp tracking (`mark_synced`) prevents duplicate sync diffs; `kg_sync` handler advances the watermark after each call.
- `project_discovery` hardened: scans multiple session files / lines for `cwd`.
- Tighter visual editor graph simulation forces.

## [0.6.0]

### Added
- `kg_progress_get` / `kg_progress_set` tools for persistent task progress (stored in `_meta.progress` in graph JSON).
- `kg_session_stats` tool (duration, op count, graph sizes); per-session operation counting.
- Session persistence — `~/.claude/knowledge/sessions.json` survives server restarts; auto-recover unknown sessions gracefully.
- `/skill scout` — tension-driven mining of conversation history for pattern extraction.
- `/skill extract` — map codebase architecture into the knowledge graph.
- Visual editor write support: create / edit / delete nodes and edges via REST proxy.
- WebSocket transport for visual editor real-time updates (replaces polling); context menu, modals, toast notifications.
- `VISUAL_EDITOR_GUIDE.md`.

### Changed
- Memory skill restructured into `SKILL.md` (100-line overview) + reference files (`CAPTURE.md`, `RECALL.md`, `MAINTAIN.md`).
- `persistence.py` returns a 3-tuple (graph, versions, progress); REST endpoints for progress and session stats.
- `CLAUDE.md` template adds session-lifecycle guidance and available-skills routing.

## [0.5.14]

### Changed
- Project graph path consolidated to `.claude/knowledge/graph.json` (hardcoded).
- Added global `kg-memory` command for server management from anywhere.
- Auto-generated `.gitignore` for project knowledge folders.

### Removed
- Legacy path support (`.knowledge/`, `.claude/graph.json`).

## [0.5.13]

### Fixed
- Streamable HTTP transport for Claude Code: `json_response=False → True` in `StreamableHTTPSessionManager` (was using SSE format instead of JSON-RPC over HTTP). Resolves "Failed to reconnect to plugin:memory:kg" errors.
- Orphaned-edge cleanup on graph load.

### Added
- `project_path` parameter to `read_graphs()` REST API.

### Removed
- Dead code: unused `mcp_http/app.py`.

## Earlier versions

Versions before 0.5.13 predate this changelog. The earliest commit in the current history is the initial Streamable HTTP transport work; older code is no longer in git history (an early force-push removed sensitive data that had leaked into commits). See [`ARCHITECTURE.md`](knowledge-graph/ARCHITECTURE.md) "Origin & Evolution" for the pre-history of the design (ByteRover Cipher → TypeScript MCP with Steiner trees → current compression-first architecture).
