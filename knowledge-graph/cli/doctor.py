"""kg doctor and kg setup: the same checks, read-only or with fixes."""

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
        if not assume_yes:
            answer = input(f"{step.group}: {step.title}? [Y/n] ").strip().lower()
            if answer not in ("", "y", "yes"):
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
