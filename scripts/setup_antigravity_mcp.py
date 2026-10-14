"""Generate workspace MCP config without reading any existing credential config.

Official docs: https://antigravity.google/docs/mcp
Existing config files are deliberately never read or overwritten. For those,
use --print-entry and insert the entry through Antigravity's settings yourself.
"""
import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def server_entry(root: Path) -> dict:
    python = root / ".venv" / "Scripts" / "python.exe"
    script = root / "scripts" / "spike_mcp.py"
    if not python.is_file() or not script.is_file():
        raise FileNotFoundError("Create the repository venv and spike_mcp.py first")
    return {"command": str(python), "args": [str(script)]}


def register(root: Path) -> Path:
    target = root / ".agents" / "mcp_config.json"
    data = {"mcpServers": {"devmesh-bridge": server_entry(root)}}
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create: cannot overwrite even if another process creates it first.
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-entry", action="store_true")
    args = parser.parse_args()
    if args.print_entry:
        print(json.dumps({"devmesh-bridge": server_entry(ROOT)}, indent=2))
        return
    try:
        print(f"Created {register(ROOT)}")
    except FileExistsError:
        parser.exit(2, "Config already exists; it was not read or changed. Use --print-entry.\n")


if __name__ == "__main__":
    main()
