#!/usr/bin/env python3
"""Check plugin dependency integrity.

Usage:
    python check_dependencies.py <plugin-dir>

Checks:
    - .mcp.json is valid JSON if present
    - Each MCP server entry has 'command' field
    - Environment variables in MCP config are documented
    - hooks.json is valid if declared
    - Hook event names are recognized
    - Dependencies listed in manifest exist or are documented
"""

import json
import sys
from pathlib import Path


RECOGNIZED_HOOK_EVENTS = {
    "SessionStart",
    "SessionEnd",
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
}



MANIFEST_DIRS = (".codebuddy-plugin", ".workbuddy-plugin", ".claude-plugin")


def find_manifest(root: Path) -> Path:
    """Locate plugin.json. CodeBuddy / WorkBuddy prefer their own directory
    and fall back to .claude-plugin/, so check in that order."""
    for d in MANIFEST_DIRS:
        path = root / d / "plugin.json"
        if path.exists():
            return path
    return root / ".claude-plugin" / "plugin.json"

def check_mcp(root: Path) -> dict:
    """Check .mcp.json validity."""
    results = {"passed": [], "failed": [], "warnings": []}

    mcp_path = root / ".mcp.json"
    if not mcp_path.exists():
        results["passed"].append("No .mcp.json (not required)")
        return results

    try:
        config = json.loads(mcp_path.read_text())
        results["passed"].append(".mcp.json is valid JSON")
    except json.JSONDecodeError as e:
        results["failed"].append(f".mcp.json is invalid JSON: {e}")
        return results

    servers = config.get("mcpServers", {})
    if not servers:
        results["warnings"].append(".mcp.json exists but has no mcpServers entries")
        return results

    seen_names = set()
    for name, server in servers.items():
        if name in seen_names:
            results["failed"].append(f"Duplicate MCP server name: '{name}'")
        seen_names.add(name)

        if "command" in server:
            results["passed"].append(f"MCP server '{name}' has command field")
        else:
            results["failed"].append(f"MCP server '{name}' missing 'command' field")

        # Check env vars
        env = server.get("env", {})
        for var_name, var_value in env.items():
            if "${" in str(var_value):
                results["warnings"].append(
                    f"MCP server '{name}' uses env var ${{{var_name}}} — ensure it is documented"
                )

    return results


def check_hooks(root: Path, manifest: dict) -> dict:
    """Check hooks.json validity."""
    results = {"passed": [], "failed": [], "warnings": []}

    hooks_path_str = manifest.get("hooks")
    if not isinstance(hooks_path_str, str):
        return results  # absent, or an inline config / list — no single file to check

    hooks_path = root / hooks_path_str
    if not hooks_path.exists():
        results["failed"].append(f"Declared hooks file '{hooks_path_str}' not found")
        return results

    try:
        hooks_data = json.loads(hooks_path.read_text())
        results["passed"].append(f"{hooks_path_str} is valid JSON")
    except json.JSONDecodeError as e:
        results["failed"].append(f"{hooks_path_str} is invalid JSON: {e}")
        return results

    hooks = hooks_data.get("hooks", {})
    for event_name in hooks:
        if event_name in RECOGNIZED_HOOK_EVENTS:
            results["passed"].append(f"Hook event '{event_name}' is recognized")
        else:
            results["warnings"].append(
                f"Hook event '{event_name}' is not a recognized event name"
            )

    return results


def check_plugin_dependencies(root: Path, manifest: dict) -> dict:
    """Check declared plugin dependencies."""
    results = {"passed": [], "failed": [], "warnings": []}

    deps = manifest.get("dependencies", [])
    if not deps:
        results["passed"].append("No plugin dependencies declared")
        return results

    if isinstance(deps, dict):
        # WorkBuddy expert packages declare {"mcpServers": ..., "connectors": [...]}
        # here instead of plugin names — not sibling plugins, nothing to look up.
        results["passed"].append("Dependencies declare MCP servers / connectors, not plugins")
        return results

    for dep in deps:
        if isinstance(dep, dict):  # {"name": ..., "version": ..., "marketplace": ...}
            dep = dep.get("name", "")
        # Check if the dependency exists as a sibling directory (in a collection)
        sibling = root.parent / dep
        if sibling.is_dir() and find_manifest(sibling).exists():
            results["passed"].append(f"Dependency '{dep}' found as sibling plugin")
        else:
            results["warnings"].append(
                f"Dependency '{dep}' not found locally — ensure it's available at install time"
            )

    return results


def check_dependencies(plugin_dir: str) -> dict:
    """Run all dependency checks."""
    root = Path(plugin_dir).resolve()
    all_results = {"passed": [], "failed": [], "warnings": []}

    # Load manifest
    manifest = {}
    manifest_path = find_manifest(root)
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            pass
    else:
        draft_path = root / "plugin.json.draft"
        if draft_path.exists():
            try:
                manifest = json.loads(draft_path.read_text())
            except json.JSONDecodeError:
                pass

    for check_fn in (
        lambda: check_mcp(root),
        lambda: check_hooks(root, manifest),
        lambda: check_plugin_dependencies(root, manifest),
    ):
        result = check_fn()
        all_results["passed"].extend(result["passed"])
        all_results["failed"].extend(result["failed"])
        all_results["warnings"].extend(result["warnings"])

    return all_results


def main():
    if len(sys.argv) < 2:
        print("Usage: python check_dependencies.py <plugin-dir>", file=sys.stderr)
        sys.exit(1)

    plugin_dir = sys.argv[1]
    if not Path(plugin_dir).is_dir():
        print(f"Error: '{plugin_dir}' is not a directory", file=sys.stderr)
        sys.exit(1)

    results = check_dependencies(plugin_dir)
    print(json.dumps(results, indent=2))

    if results["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
