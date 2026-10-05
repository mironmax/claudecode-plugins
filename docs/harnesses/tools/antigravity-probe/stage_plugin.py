#!/usr/bin/env python3
"""Stage a native plugin copy for a development port; no user config edits."""

import argparse
import json
from pathlib import Path
import shutil
import tempfile


def stage_plugin(repo, target, port):
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    shutil.copytree(repo / "knowledge-graph", target, ignore=shutil.ignore_patterns(
        "venv", "__pycache__", ".pytest_cache", "devdocs", "dev", ".mcp_server.pid", ".last_start_error"))
    config_file = target / "mcp_config.json"
    config = json.loads(config_file.read_text())
    config["mcpServers"]["kg"]["serverUrl"] = f"http://127.0.0.1:{port}/"
    config_file.write_text(json.dumps(config, indent=2) + "\n")


def main():
    repo = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--output-root", type=Path, default=repo / "devdocs/antigravity-plugins")
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="iteration-", dir=args.output_root.resolve()))
    target = folder / "knowledge-graph"
    stage_plugin(repo, target, args.port)
    print(target)


if __name__ == "__main__":
    main()
