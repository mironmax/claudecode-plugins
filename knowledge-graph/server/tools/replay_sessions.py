#!/usr/bin/env python3
"""
Replay session history to recover lost knowledge graph data.

Scans ALL projects under ~/.claude/projects/ for kg_put_node, kg_put_edge,
kg_delete_node, kg_delete_edge tool calls in session JSONL files.
Reconstructs graph state using last-write-wins per node/edge.

Usage:
  python replay_sessions.py                    # Scan all projects, report only
  python replay_sessions.py --project <name>   # Scan specific project
  python replay_sessions.py --apply            # Write recovered data to centralized storage
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional


CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
STORAGE_ROOT = Path.home() / ".knowledge-graph"


def decode_project_path_from_cwd(project_dir: Path) -> Optional[Path]:
    """Extract project path from session file .cwd field."""
    session_files = [f for f in project_dir.glob("*.jsonl") if not f.name.startswith("agent-")]
    for sf in session_files[:3]:
        try:
            with open(sf) as f:
                for i, line in enumerate(f):
                    if i >= 10:
                        break
                    try:
                        data = json.loads(line)
                        if cwd := data.get("cwd"):
                            return Path(cwd)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue
    return None


def extract_kg_operations(session_file: Path) -> list[dict]:
    """Extract kg_put_node, kg_put_edge, kg_delete_node, kg_delete_edge from a session file."""
    ops = []
    try:
        with open(session_file) as f:
            for line_num, line in enumerate(f):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Look for tool_use blocks in assistant messages
                message = data.get("message", data)
                content = message.get("content", [])
                if isinstance(content, str):
                    continue

                ts = data.get("timestamp") or message.get("timestamp")

                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_use":
                        continue

                    raw_name = block.get("name", "")
                    # Handle both direct names and MCP-prefixed names
                    # e.g. "mcp__plugin_memory_kg__kg_put_node" -> "kg_put_node"
                    name = raw_name
                    for suffix in ("kg_put_node", "kg_put_edge", "kg_delete_node", "kg_delete_edge"):
                        if raw_name.endswith(suffix):
                            name = suffix
                            break

                    if name not in ("kg_put_node", "kg_put_edge", "kg_delete_node", "kg_delete_edge"):
                        continue

                    args = block.get("input", {})
                    ops.append({
                        "op": name,
                        "args": args,
                        "ts": ts,
                        "file": str(session_file),
                        "line": line_num,
                    })
    except Exception as e:
        print(f"  Error reading {session_file.name}: {e}")

    return ops


def reconstruct_graph(ops: list[dict]) -> dict:
    """Reconstruct graph state from operations using last-write-wins.

    Returns dict with "nodes" and "edges" for both user and project levels.
    """
    # Sort by timestamp
    ops.sort(key=lambda o: o.get("ts") or "")

    graphs = {
        "user": {"nodes": {}, "edges": {}},
        "project": {"nodes": {}, "edges": {}},
    }

    for op in ops:
        name = op["op"]
        args = op["args"]
        level = args.get("level", "project")

        if level not in graphs:
            continue

        if name == "kg_put_node":
            node_id = args.get("id")
            if not node_id:
                continue
            node = {"id": node_id, "gist": args.get("gist", "")}
            if notes := args.get("notes"):
                node["notes"] = notes
            if touches := args.get("touches"):
                node["touches"] = touches
            graphs[level]["nodes"][node_id] = node

        elif name == "kg_put_edge":
            from_ref = args.get("from")
            to_ref = args.get("to")
            rel = args.get("rel")
            if not (from_ref and to_ref and rel):
                continue
            edge_key = f"{from_ref}->{to_ref}:{rel}"
            edge = {"from": from_ref, "to": to_ref, "rel": rel}
            if notes := args.get("notes"):
                edge["notes"] = notes
            graphs[level]["edges"][edge_key] = edge

        elif name == "kg_delete_node":
            node_id = args.get("id")
            if node_id and node_id in graphs[level]["nodes"]:
                del graphs[level]["nodes"][node_id]
                # Remove connected edges
                to_remove = [k for k, e in graphs[level]["edges"].items()
                             if e["from"] == node_id or e["to"] == node_id]
                for k in to_remove:
                    del graphs[level]["edges"][k]

        elif name == "kg_delete_edge":
            from_ref = args.get("from")
            to_ref = args.get("to")
            rel = args.get("rel")
            if from_ref and to_ref and rel:
                edge_key = f"{from_ref}->{to_ref}:{rel}"
                graphs[level]["edges"].pop(edge_key, None)

    return graphs


def load_existing_graph(graph_path: Path) -> dict:
    """Load existing graph from centralized storage."""
    if not graph_path.exists():
        return {"nodes": {}, "edges": {}}
    try:
        data = json.loads(graph_path.read_text())
        return {
            "nodes": data.get("nodes", {}),
            "edges": data.get("edges", {}),
        }
    except Exception:
        return {"nodes": {}, "edges": {}}


def compare_graphs(recovered: dict, existing: dict) -> dict:
    """Compare recovered vs existing graph and report differences."""
    missing_nodes = set(recovered["nodes"]) - set(existing["nodes"])
    extra_nodes = set(existing["nodes"]) - set(recovered["nodes"])
    common_nodes = set(recovered["nodes"]) & set(existing["nodes"])

    missing_edges = set(recovered["edges"]) - set(existing["edges"])
    extra_edges = set(existing["edges"]) - set(recovered["edges"])

    return {
        "missing_nodes": missing_nodes,
        "extra_nodes": extra_nodes,
        "common_nodes": common_nodes,
        "missing_edges": missing_edges,
        "extra_edges": extra_edges,
    }


def scan_all_projects(filter_project: str = None) -> dict:
    """Scan all projects and return recovery report."""
    if not CLAUDE_PROJECTS.exists():
        print("No ~/.claude/projects/ found")
        return {}

    results = {}

    for project_dir in sorted(CLAUDE_PROJECTS.iterdir()):
        if not project_dir.is_dir():
            continue

        project_path = decode_project_path_from_cwd(project_dir)
        if project_path is None:
            continue

        project_name = project_path.name

        if filter_project and filter_project != project_name:
            continue

        # Collect all operations from all session files
        session_files = sorted(project_dir.glob("*.jsonl"))
        all_ops = []

        for sf in session_files:
            ops = extract_kg_operations(sf)
            all_ops.extend(ops)

        if not all_ops:
            continue

        # Reconstruct
        recovered = reconstruct_graph(all_ops)

        # Count operations by type
        op_counts = defaultdict(int)
        for op in all_ops:
            op_counts[op["op"]] += 1

        # Load existing graph from centralized storage
        slug = project_path.name
        existing_path = STORAGE_ROOT / "projects" / slug / "graph.json"
        existing = load_existing_graph(existing_path)

        # Compare project level
        diff = compare_graphs(recovered["project"], existing)

        results[project_name] = {
            "project_path": str(project_path),
            "sessions_scanned": len(session_files),
            "total_ops": len(all_ops),
            "op_counts": dict(op_counts),
            "recovered_nodes": len(recovered["project"]["nodes"]),
            "recovered_edges": len(recovered["project"]["edges"]),
            "existing_nodes": len(existing["nodes"]),
            "existing_edges": len(existing["edges"]),
            "missing_nodes": len(diff["missing_nodes"]),
            "missing_edges": len(diff["missing_edges"]),
            "missing_node_ids": sorted(diff["missing_nodes"]),
            "recovered": recovered,
        }

    return results


def main():
    filter_project = None
    apply = "--apply" in sys.argv

    for i, arg in enumerate(sys.argv):
        if arg == "--project" and i + 1 < len(sys.argv):
            filter_project = sys.argv[i + 1]

    print(f"Scanning session history for KG operations...\n")

    results = scan_all_projects(filter_project)

    if not results:
        print("No KG operations found.")
        return

    # Print report
    total_missing = 0
    for name, data in sorted(results.items()):
        status = ""
        if data["missing_nodes"] > 0:
            status = " *** RECOVERY NEEDED ***"
            total_missing += data["missing_nodes"]
        elif data["existing_nodes"] == 0 and data["recovered_nodes"] > 0:
            status = " *** FULL RECOVERY ***"
            total_missing += data["recovered_nodes"]

        print(f"  {name}{status}")
        print(f"    Sessions: {data['sessions_scanned']}, Operations: {data['total_ops']}")
        print(f"    Op breakdown: {data['op_counts']}")
        print(f"    Recovered: {data['recovered_nodes']} nodes, {data['recovered_edges']} edges")
        print(f"    Existing:  {data['existing_nodes']} nodes, {data['existing_edges']} edges")
        if data["missing_nodes"] > 0:
            print(f"    Missing:   {data['missing_nodes']} nodes, {data['missing_edges']} edges")
            if len(data["missing_node_ids"]) <= 10:
                print(f"    Missing IDs: {data['missing_node_ids']}")
        print()

    print(f"Total projects with data: {len(results)}")
    print(f"Total missing nodes across all projects: {total_missing}")

    if apply and total_missing > 0:
        print(f"\nApplying recovery...")
        for name, data in results.items():
            if data["missing_nodes"] == 0 and data["existing_nodes"] > 0:
                continue

            slug = Path(data["project_path"]).name
            graph_path = STORAGE_ROOT / "projects" / slug / "graph.json"
            graph_path.parent.mkdir(parents=True, exist_ok=True)

            # Load existing or create new
            existing = load_existing_graph(graph_path)
            recovered = data["recovered"]["project"]

            # Merge: add missing nodes and edges (don't overwrite existing)
            for node_id, node in recovered["nodes"].items():
                if node_id not in existing["nodes"]:
                    existing["nodes"][node_id] = node

            for edge_key, edge in recovered["edges"].items():
                # Convert edge_key back to tuple format for storage
                edge_tuple_key = (edge["from"], edge["to"], edge["rel"])
                if edge_tuple_key not in existing["edges"] and edge_key not in existing.get("_raw_edges", {}):
                    existing["edges"][edge_key] = edge

            # Save
            graph_data = {
                "nodes": existing["nodes"],
                "edges": existing["edges"],
                "_meta": {"_recovered": True}
            }
            graph_path.write_text(json.dumps(graph_data, indent=2))
            print(f"  Recovered {name}: {graph_path}")

        print("Recovery complete.")
    elif total_missing > 0 and not apply:
        print(f"\nRun with --apply to write recovered data to centralized storage.")


if __name__ == "__main__":
    main()
