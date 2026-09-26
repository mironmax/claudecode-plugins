# 06 — Continuous integration

## Goal

Run the test suite on every push and pull request, so that a change,
including a dependency security update, shows whether it breaks anything
before anyone folds it. The repository has no CI today. `.github/` holds
only `dependabot.yml`, which opens security updates only.

## What the suite is

- Self-contained scripts under `knowledge-graph/server/tests/test_*.py`,
  with no pytest. Each one runs directly with the server's Python, for
  example `cd knowledge-graph/server && python tests/test_core.py`. A
  non-zero exit code means failure; confirm this for every file rather
  than assuming it.
- Dependencies come from `knowledge-graph/server/requirements.txt`.
- Some tests use git (autocommit, anchor repair, the eval harness), and
  some start the HTTP app against a temporary `KG_STORAGE_ROOT`. Find
  anything else a test assumes about its environment (paths under `$HOME`,
  ports, installed binaries) and make it hold on the runner. Do not skip
  the test to get around it.
- The server supports Python 3.10 and newer
  (`server/manage_server.sh`).

## What to build

1. `.github/workflows/tests.yml`, triggered on push to `main` and on pull
   requests.
2. A matrix of the oldest supported Python (3.10) and the newest stable
   Python the runner offers. If a test fails on 3.10, report it and fix the
   code where the fix is small and clearly correct. Otherwise raise the
   floor in `manage_server.sh` and the README, and say why in the pull
   request.
3. One step that runs every `tests/test_*.py` file, not a hand-kept list,
   and fails if any file fails. It prints each file's name and result so a
   red run shows at once which file failed.
4. A fresh virtual environment from `requirements.txt` on each run, with
   pip caching keyed on that file.
5. A README badge only if the README already has a place where one fits
   naturally; otherwise skip it.

## Constraints

- If a test fails only on the runner, fix the test's assumption, not the
  assertion. Report any such case in the pull request description.
- Keep workflow permissions minimal (`contents: read`), and pin actions to
  a major version.
- No changes to server behaviour. Changes to test files are allowed only
  to make hidden environment assumptions explicit.

## Done when

The workflow runs green on its own pull request for both Python versions,
and the pull request description lists every test file with its run time
and any assumption that had to be made explicit.
