#!/usr/bin/env python3
"""Validate plugin manifest and directory structure.

Usage:
    python validate_plugin.py <plugin-dir>

Checks:
    - plugin.json exists (.claude-plugin/, or .codebuddy-plugin/ / .workbuddy-plugin/) and is valid JSON
    - Required fields: name, version, description
    - name matches directory name and kebab-case pattern
    - version follows semver
    - Declared paths (skills, commands, hooks, mcpServers) resolve
"""

import json
import re
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

def validate_plugin(plugin_dir: str) -> dict:
    """Validate plugin structure. Returns {passed: [...], failed: [...], warnings: [...]}."""
    root = Path(plugin_dir).resolve()
    results = {"passed": [], "failed": [], "warnings": []}

    # Check plugin.json exists
    manifest_path = find_manifest(root)
    if not manifest_path.exists():
        # Also check for plugin.json.draft (studio workspace)
        draft_path = root / "plugin.json.draft"
        if draft_path.exists():
            manifest_path = draft_path
            results["warnings"].append(
                "Using plugin.json.draft — this is a development workspace, not a final plugin"
            )
        else:
            results["failed"].append(
                "plugin.json not found in .claude-plugin/, .codebuddy-plugin/ or .workbuddy-plugin/ (and no plugin.json.draft)"
            )
            return results

    # Parse JSON
    try:
        manifest = json.loads(manifest_path.read_text())
        results["passed"].append(f"{manifest_path.name} is valid JSON")
    except json.JSONDecodeError as e:
        results["failed"].append(f"{manifest_path.name} is invalid JSON: {e}")
        return results

    # Required fields
    for field in ("name", "version", "description"):
        if field in manifest and manifest[field]:
            results["passed"].append(f"Required field '{field}' present")
        else:
            results["failed"].append(f"Required field '{field}' missing or empty")

    name = manifest.get("name", "")

    # Name matches kebab-case
    if re.match(r"^[a-z][a-z0-9-]*$", name):
        results["passed"].append(f"Name '{name}' follows kebab-case pattern")
    else:
        results["failed"].append(
            f"Name '{name}' does not match ^[a-z][a-z0-9-]*$ pattern"
        )

    # Name matches directory name
    dir_name = root.name
    if name == dir_name:
        results["passed"].append(f"Plugin name matches directory name '{dir_name}'")
    else:
        results["warnings"].append(
            f"Plugin name '{name}' does not match directory name '{dir_name}'"
        )

    # Version follows semver
    version = manifest.get("version", "")
    if re.match(r"^\d+\.\d+\.\d+", version):
        results["passed"].append(f"Version '{version}' follows semver")
    else:
        results["failed"].append(
            f"Version '{version}' does not follow semver (expected X.Y.Z)"
        )

    # Declared paths resolve
    path_fields = {
        "skills": "skills directory",
        "commands": "commands directory",
        "agents": "agents path",
        "hooks": "hooks file",
        "mcpServers": "MCP config file",
    }

    for field, label in path_fields.items():
        if field not in manifest:
            continue
        # A path, a list of paths, or (hooks / mcpServers) an inline config object.
        value = manifest[field]
        for declared in value if isinstance(value, list) else [value]:
            if not isinstance(declared, str):
                continue  # inline config — nothing on disk to resolve
            resolved = root / declared
            if resolved.exists():
                results["passed"].append(f"Declared {label} '{declared}' exists")
            else:
                results["failed"].append(
                    f"Declared {label} '{declared}' not found at {resolved}"
                )

    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_plugin.py <plugin-dir>", file=sys.stderr)
        sys.exit(1)

    plugin_dir = sys.argv[1]
    if not Path(plugin_dir).is_dir():
        print(f"Error: '{plugin_dir}' is not a directory", file=sys.stderr)
        sys.exit(1)

    results = validate_plugin(plugin_dir)
    print(json.dumps(results, indent=2))

    if results["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
