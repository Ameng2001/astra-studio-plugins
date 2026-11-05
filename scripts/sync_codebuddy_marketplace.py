#!/usr/bin/env python3
"""Generate the CodeBuddy / WorkBuddy marketplace manifest from the Claude Code one.

`.claude-plugin/marketplace.json` stays the single source of truth. CodeBuddy
(the runtime under WorkBuddy) reads each plugin's `.claude-plugin/plugin.json`
as-is, but only documents `.codebuddy-plugin/marketplace.json` for the
marketplace itself — so that one file is mirrored here.

Usage:
    python3 scripts/sync_codebuddy_marketplace.py           # write the mirror
    python3 scripts/sync_codebuddy_marketplace.py --check   # exit 1 if stale or invalid
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / ".claude-plugin" / "marketplace.json"
TARGET = ROOT / ".codebuddy-plugin" / "marketplace.json"
MANIFEST_DIRS = (".codebuddy-plugin", ".workbuddy-plugin", ".claude-plugin")


def plugin_manifest(plugin_root: Path) -> dict | None:
    for d in MANIFEST_DIRS:
        path = plugin_root / d / "plugin.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def build() -> tuple[dict, list[str]]:
    market = json.loads(SOURCE.read_text(encoding="utf-8"))
    problems = []
    seen = set()
    for entry in market["plugins"]:
        name, source = entry["name"], entry["source"]
        if name in seen:
            problems.append(f"{name}: registered twice")
        seen.add(name)
        if not isinstance(source, str):
            continue  # remote source — nothing local to verify
        if not source.startswith("./"):
            problems.append(f"{name}: source must start with './' (got {source!r})")
        manifest = plugin_manifest(ROOT / source)
        if manifest is None:
            # strict mode (the default) requires a plugin.json in the plugin
            problems.append(f"{name}: no plugin.json under {source}")
            continue
        if manifest.get("name") != name:
            problems.append(
                f"{name}: plugin.json name is {manifest.get('name')!r}"
            )
        # CodeBuddy caches by version: surface it in the marketplace listing.
        if manifest.get("version"):
            entry["version"] = manifest["version"]
    return market, problems


def main() -> int:
    check = "--check" in sys.argv[1:]
    market, problems = build()
    for p in problems:
        print(f"ERROR {p}", file=sys.stderr)
    if problems:
        return 1

    rendered = json.dumps(market, indent=2, ensure_ascii=False) + "\n"
    if check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else None
        if current != rendered:
            print(
                f"{TARGET.relative_to(ROOT)} is stale — run "
                "python3 scripts/sync_codebuddy_marketplace.py",
                file=sys.stderr,
            )
            return 1
        print(f"{TARGET.relative_to(ROOT)} is up to date")
        return 0

    TARGET.parent.mkdir(exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(market['plugins'])} plugins)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
