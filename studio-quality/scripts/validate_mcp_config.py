#!/usr/bin/env python3
"""Validate an existing .mcp.json file.

Usage:
    python validate_mcp_config.py <plugin-dir>

Checks:
    - .mcp.json exists and is valid JSON
    - Each server entry has 'command' field
    - No duplicate server names (JSON handles this, but check args)
    - Environment variables are documented
    - Plugin manifest references .mcp.json if it exists
"""

import json
import sys
from pathlib import Path



MANIFEST_DIRS = (".codebuddy-plugin", ".workbuddy-plugin", ".claude-plugin")


def find_manifest(root: Path) -> Path:
    """Locate plugin.json. CodeBuddy / WorkBuddy prefer their own directory
    and fall back to .claude-plugin/, so check in that order."""
    for d in MANIFEST_DIRS:
        path = root / d / "plugin.json"
        if path.exists():
            return path
    return root / ".claude-plugin" / "plugin.json"

def validate_mcp_config(plugin_dir: str) -> dict:
    """Validate .mcp.json in the given plugin directory."""
    root = Path(plugin_dir).resolve()
    results = {"passed": [], "failed": [], "warnings": []}

    mcp_path = root / ".mcp.json"
    if not mcp_path.exists():
        results["passed"].append("No .mcp.json file (not required)")
        return results

    # Parse JSON
    try:
        config = json.loads(mcp_path.read_text())
        results["passed"].append(".mcp.json is valid JSON")
    except json.JSONDecodeError as e:
        results["failed"].append(f".mcp.json is invalid JSON: {e}")
        return results

    # Check structure
    if "mcpServers" not in config:
        results["failed"].append(".mcp.json missing 'mcpServers' key")
        return results
    results["passed"].append("'mcpServers' key present")

    servers = config["mcpServers"]
    if not isinstance(servers, dict):
        results["failed"].append("'mcpServers' should be an object")
        return results

    if not servers:
        results["warnings"].append(".mcp.json has empty mcpServers")
        return results

    # Validate each server
    env_vars_needed = []
    for name, server in servers.items():
        if not isinstance(server, dict):
            results["failed"].append(f"Server '{name}' should be an object")
            continue

        # Command field
        if "command" in server:
            results["passed"].append(f"Server '{name}' has 'command' field")
        else:
            results["failed"].append(f"Server '{name}' missing 'command' field")

        # Args field
        if "args" in server and not isinstance(server["args"], list):
            results["failed"].append(f"Server '{name}' 'args' should be an array")

        # Environment variables
        env = server.get("env", {})
        for var_name, var_value in env.items():
            if "${" in str(var_value):
                env_vars_needed.append(f"{name}: {var_name}")

    if env_vars_needed:
        results["warnings"].append(
            f"Environment variables to configure: {', '.join(env_vars_needed)}"
        )

    # Check manifest references .mcp.json
    manifest_path = find_manifest(root)
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            if "mcpServers" in manifest:
                results["passed"].append("Plugin manifest declares mcpServers")
            else:
                results["warnings"].append(
                    "Plugin manifest does not reference .mcp.json — add '\"mcpServers\": \"./.mcp.json\"'"
                )
        except json.JSONDecodeError:
            pass  # manifest validation is handled by validate_plugin.py

    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_mcp_config.py <plugin-dir>", file=sys.stderr)
        sys.exit(1)

    plugin_dir = sys.argv[1]
    if not Path(plugin_dir).is_dir():
        print(f"Error: '{plugin_dir}' is not a directory", file=sys.stderr)
        sys.exit(1)

    results = validate_mcp_config(plugin_dir)
    print(json.dumps(results, indent=2))

    if results["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
