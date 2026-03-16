#!/usr/bin/env python3
"""
Migrate knowledge graph storage from legacy locations to centralized ~/.knowledge-graph/.

Legacy layout:
  ~/.claude/knowledge/user.json
  ~/.claude/knowledge/sessions.json
  <project>/.claude/knowledge/graph.json

New layout:
  ~/.knowledge-graph/
    user.json
    sessions.json
    projects/
      <slug>/graph.json
    .gitignore

Usage:
  python migrate_storage.py           # Dry run
  python migrate_storage.py --apply   # Apply migration
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path


STORAGE_ROOT = Path.home() / ".knowledge-graph"
LEGACY_USER = Path.home() / ".claude/knowledge/user.json"
LEGACY_SESSIONS = Path.home() / ".claude/knowledge/sessions.json"


def find_legacy_project_graphs() -> list[tuple[Path, str, str]]:
    """Find all legacy project graph.json files.

    Returns list of (graph_path, project_slug, project_root) tuples, deduplicated.
    """
    seen_roots = set()
    results = []

    # Scan common locations (more specific first to avoid duplicates)
    for search_root in [Path.home() / "DevProj", Path.home() / "Projects", Path.home()]:
        if not search_root.exists():
            continue

        for graph_path in search_root.rglob(".claude/knowledge/graph.json"):
            # Skip venv, node_modules, .git
            parts = graph_path.parts
            if any(p in parts for p in ("venv", "node_modules", ".git", "__pycache__")):
                continue

            # Derive project root: strip .claude/knowledge/graph.json
            project_root = graph_path.parent.parent.parent
            root_str = str(project_root.resolve())

            # Deduplicate
            if root_str in seen_roots:
                continue
            seen_roots.add(root_str)

            slug = project_root.name
            results.append((graph_path, slug, root_str))

    return results


def migrate(apply: bool = False):
    """Run migration."""
    print(f"{'APPLYING' if apply else 'DRY RUN'}: Migrating to {STORAGE_ROOT}/\n")

    # Create storage root
    if apply:
        STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
        (STORAGE_ROOT / "projects").mkdir(exist_ok=True)

    # 1. User graph
    if LEGACY_USER.exists():
        dest = STORAGE_ROOT / "user.json"
        if dest.exists():
            print(f"  SKIP user.json (already exists at {dest})")
        else:
            print(f"  COPY {LEGACY_USER} -> {dest}")
            if apply:
                shutil.copy2(LEGACY_USER, dest)
    else:
        print(f"  SKIP user.json (no legacy file)")

    # 2. Sessions
    if LEGACY_SESSIONS.exists():
        dest = STORAGE_ROOT / "sessions.json"
        if dest.exists():
            print(f"  SKIP sessions.json (already exists at {dest})")
        else:
            print(f"  COPY {LEGACY_SESSIONS} -> {dest}")
            if apply:
                shutil.copy2(LEGACY_SESSIONS, dest)
    else:
        print(f"  SKIP sessions.json (no legacy file)")

    # 3. Project graphs
    project_graphs = find_legacy_project_graphs()
    print(f"\n  Found {len(project_graphs)} legacy project graph(s):\n")

    for graph_path, slug, project_root in project_graphs:
        dest_dir = STORAGE_ROOT / "projects" / slug
        dest = dest_dir / "graph.json"

        if dest.exists():
            # Compare sizes/timestamps
            src_size = graph_path.stat().st_size
            dst_size = dest.stat().st_size
            if src_size == dst_size:
                print(f"  SKIP {slug} (identical)")
            else:
                print(f"  CONFLICT {slug}: legacy={src_size}B, centralized={dst_size}B (keeping larger)")
                if apply and src_size > dst_size:
                    shutil.copy2(graph_path, dest)
        else:
            try:
                data = json.loads(graph_path.read_text())
                nodes = len(data.get("nodes", {}))
                edges = len(data.get("edges", {}))
                print(f"  COPY {slug} ({nodes} nodes, {edges} edges) <- {graph_path}")
            except Exception:
                print(f"  COPY {slug} (unreadable) <- {graph_path}")

            if apply:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(graph_path, dest)

    # 4. Create .gitignore
    gitignore = STORAGE_ROOT / ".gitignore"
    if not gitignore.exists():
        print(f"\n  CREATE .gitignore")
        if apply:
            gitignore.write_text("*.tmp\n*.bak\n")
    else:
        print(f"\n  SKIP .gitignore (exists)")

    # 5. Git init
    git_dir = STORAGE_ROOT / ".git"
    if not git_dir.exists():
        print(f"  INIT git repository")
        if apply:
            subprocess.run(["git", "init"], cwd=STORAGE_ROOT, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=STORAGE_ROOT, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial migration from legacy storage"],
                cwd=STORAGE_ROOT, capture_output=True
            )
    else:
        print(f"  SKIP git init (already initialized)")

    print(f"\n{'DONE' if apply else 'DRY RUN COMPLETE — run with --apply to execute'}")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    migrate(apply)
