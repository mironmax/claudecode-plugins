"""kg doctor, setup, update and uninstall: one set of steps, read, fixed or reversed."""

import os
import sys

try:
    from . import kg, steps
except ImportError:  # run as a script from a checkout
    import kg
    import steps

MARK = {steps.OK: "✓", steps.FIX: "✗", steps.MANUAL: "✗", steps.SUGGEST: "·", steps.OFF: "–"}


def survey(ctx) -> list[tuple]:
    rows = []
    for step in steps.STEPS:
        try:
            state, detail = step.check(ctx)
        except Exception as exc:  # a broken check must not hide the others
            state, detail = steps.MANUAL, f"check failed: {exc}"
        rows.append((step, state, detail))
    return rows


def show(rows) -> None:
    groups = {}
    for step, state, detail in rows:
        groups.setdefault(step.group, []).append((state, detail))
    for group, lines in groups.items():
        print(f"\n{group}")
        if all(state == steps.OFF for state, _ in lines) and len({d for _, d in lines}) == 1:
            lines = lines[:1]   # one "not installed", not one per step
        for state, detail in lines:
            print(f"  {MARK[state]} {detail}")


def run() -> int:
    ctx = steps.Context()
    print(f"kg {ctx.want} — {kg.ROOT}")
    rows = survey(ctx)
    show(rows)
    problems = sum(state in (steps.FIX, steps.MANUAL) for _, state, _ in rows)
    offers = sum(state in (steps.FIX, steps.SUGGEST) for _, state, _ in rows)
    print(f"\n{problems} issue(s)." if problems else "\nAll good.", end="")
    print(f" `kg setup` can fix or improve {offers}." if offers else "")
    return 1 if problems else 0


def setup(assume_yes: bool, only: set[str] | None, plan: bool) -> int:
    ctx = steps.Context()
    print(f"kg {ctx.want} — {kg.ROOT}")
    rows = survey(ctx)
    show(rows)
    offered = [(s, st, d) for s, st, d in rows
               if st in (steps.FIX, steps.SUGGEST) and (not only or s.key in only)]
    manual = [(s, d) for s, st, d in rows if st == steps.MANUAL]
    if not offered:
        print("\nNothing to set up." + (" Manual steps are listed above." if manual else ""))
        return 1 if manual else 0
    print("\nSetup would:")
    for step, _, _ in offered:
        print(f"  [{step.key}] {step.group}: {step.title}")
    if plan:
        print("\nRun `kg setup` to choose, or `kg setup --yes [--only KEY,…]`.")
        return 0
    if not assume_yes and not sys.stdin.isatty():
        print("\nNo terminal to ask in: rerun with --yes (and --only KEY,… for a subset).")
        return 1
    failed = 0
    print()
    for step, _, detail in offered:
        if not confirm(f"{step.group}: {step.title}", assume_yes):
            continue
        try:
            print(f"  ✓ {step.apply(ctx)}")
        except Exception as exc:
            failed += 1
            print(f"  ✗ {step.key}: {exc}")
    if ctx.backups.exists():
        print(f"\nBackups of every changed file: {ctx.backups}")
    for step, detail in manual:
        print(f"Still for you: {step.group}: {detail}")
    return 1 if failed else 0


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return input(f"{question}? [Y/n] ").strip().lower() in ("", "y", "yes")


def update(after_upgrade: bool) -> int:
    """Upgrade kg the way it was installed, then bring the server and every
    installed plugin to its version."""
    kind = steps.install_kind()
    if not after_upgrade:
        before = kg.version()
        upgrade = {"uv": ["uv", "tool", "upgrade", "kg-memory"],
                   "pipx": ["pipx", "upgrade", "kg-memory"]}.get(kind)
        if upgrade:
            print(f"Upgrading kg ({kind})...", flush=True)
            try:
                steps.run(upgrade)
            except Exception as exc:
                print(f"✗ {exc}")
                return 1
            command = steps.kg_command()
            now = steps.run([command, "version"]).strip() if command else before
            if now != before:
                print(f"kg {before} → {now}", flush=True)   # execv drops unflushed output
                os.execv(command, [command, "update", "--after-upgrade"])
            print(f"kg {before} is the latest.")
        elif kind == "checkout":
            print("Development checkout: update it with git. Continuing with its current code.")
        else:
            print("Update kg the way you installed it. Continuing with this version.")
    ctx = steps.Context()
    for step in steps.STEPS:
        if step.key not in ("server", "claude-plugin", "codex-plugin", "agy-plugin"):
            continue
        state, detail = step.check(ctx)
        if state != steps.FIX:
            continue
        print(f"{step.group}: {detail}")
        try:
            print(f"  ✓ {step.apply(ctx)}")
        except Exception as exc:
            print(f"  ✗ {exc}")
    print()
    return run()


def uninstall(assume_yes: bool, plan: bool) -> int:
    ctx = steps.Context()
    undo = [(s, p) for s in reversed(steps.STEPS) if (p := s.undo_plan(ctx))]
    kind = steps.install_kind()
    print(f"Your memory stays in {kg.STORAGE_ROOT}: uninstall never touches it.")
    if not undo:
        print("Nothing set up by kg to reverse.")
    else:
        print("\nUninstall would:")
        for step, text in undo:
            print(f"  [{step.key}] {text}")
        if plan:
            return 0
        if not assume_yes and not sys.stdin.isatty():
            print("\nNo terminal to ask in: rerun with --yes.")
            return 1
        print()
        for step, text in undo:
            if not confirm(text[0].upper() + text[1:], assume_yes):
                continue
            try:
                print(f"  ✓ {step.undo(ctx)}")
            except Exception as exc:
                print(f"  ✗ {step.key}: {exc}")
        if ctx.backups.exists():
            print(f"\nBackups of every changed file: {ctx.backups}")
    remove = {"uv": "uv tool uninstall kg-memory", "pipx": "pipx uninstall kg-memory"}.get(kind)
    if remove:
        print(f"Last step, the kg command itself: {remove}")
    return 0
