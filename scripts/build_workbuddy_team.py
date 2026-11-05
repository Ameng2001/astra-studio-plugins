#!/usr/bin/env python3
"""Assemble the WorkBuddy open-platform expert-team package for Astra Studio.

The team is a repackaging, not a fork: skills, references, templates and
scripts are copied from the plugin directories at build time, and only what
is specific to the expert-team format lives in workbuddy/astra-studio-team/.

Usage:
    python3 scripts/build_workbuddy_team.py            # build dir + zip under dist/workbuddy/
    python3 scripts/build_workbuddy_team.py --check    # validate only, write nothing
    python3 scripts/build_workbuddy_team.py --flat     # zip without the wrapping folder

Where the official docs and the official template (trading-team.zip) disagree,
the package satisfies both when it can: the lead is declared in settings.json
(docs) and setting.json (template), and the zip wraps everything in one
top-level folder as the template does.

Exit code 1 on any rule violation. Warnings (placeholders to replace before
submitting) do not fail the build.
"""

import json
import re
import shutil
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / "workbuddy" / "astra-studio-team"
DIST = ROOT / "dist" / "workbuddy"

# Plugin-root directories that skills reach via ${CLAUDE_SKILL_DIR}/../../<dir>/.
SUPPORT_DIRS = ("templates", "references", "scripts")
# In a plugin, ../../agents/ holds persona files. In a team package agents/ is
# the registered team roster, so personas move to their own directory.
PERSONA_REF = "${CLAUDE_SKILL_DIR}/../../agents/"
PERSONA_REF_TEAM = "${CLAUDE_SKILL_DIR}/../../personas/"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")

CATEGORY_IDS = {
    "00-ExpertTeam",  # used by the official team template, absent from the docs' list
    "01-ProductDesign", "02-Engineering", "03-GameSpatial", "04-DataAI",
    "05-MarketingGrowth", "06-ContentCreative", "07-SalesCommerce",
    "08-FinanceInvestment", "09-OperationsHR", "10-ProjectQuality",
    "11-SecurityCompliance", "12-IndustryConsultant", "13-TencentZone",
    "14-WorldWise", "15-Education",
}
AVATAR_MAX_BYTES = 500 * 1024


def split_frontmatter(text: str) -> tuple[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        raise ValueError("no YAML frontmatter")
    return m.group(1), m.group(2)


def frontmatter_value(front: str, key: str) -> str | None:
    m = re.search(rf"^{key}:\s*(.+)$", front, re.M)
    return m.group(1).strip().strip("'\"") if m else None


def member_frontmatter(member: dict, description: str) -> str:
    lines = [
        "---",
        f"name: {member['id']}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
        "displayName:",
        f"  en: {json.dumps(member['name']['en'], ensure_ascii=False)}",
        f"  zh: {json.dumps(member['name']['zh'], ensure_ascii=False)}",
        "profession:",
        f"  en: {json.dumps(member['profession']['en'], ensure_ascii=False)}",
        f"  zh: {json.dumps(member['profession']['zh'], ensure_ascii=False)}",
        f"maxTurns: {member.get('maxTurns', 50)}",
    ]
    if member.get("skills"):
        lines.append("skills:")
        lines += [f"  - {s}" for s in member["skills"]]
    lines.append("---")
    return "\n".join(lines) + "\n"


def png_size(path: Path) -> tuple[int, int] | None:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def collect(team: dict) -> tuple[dict[str, Path], dict[str, Path], list[str]]:
    """Map bundled skill name -> source dir and support file -> source path."""
    errors: list[str] = []
    skills: dict[str, Path] = {}
    support: dict[str, Path] = {}
    for plugin in team["plugins"]:
        for skill_dir in sorted((ROOT / plugin / "skills").iterdir()):
            if not (skill_dir / "SKILL.md").exists():
                continue
            if skill_dir.name in skills:
                errors.append(f"skill '{skill_dir.name}' exists in two bundled plugins")
            skills[skill_dir.name] = skill_dir
        for d in SUPPORT_DIRS:
            base = ROOT / plugin / d
            if not base.is_dir():
                continue
            for f in sorted(p for p in base.rglob("*") if p.is_file()):
                if "__pycache__" in f.parts or f.suffix == ".pyc":
                    continue
                rel = f"{d}/{f.relative_to(base).as_posix()}"
                if rel in support:
                    errors.append(f"'{rel}' exists in two bundled plugins")
                support[rel] = f
    return skills, support, errors


def build_manifest(manifest: dict, team: dict, skills: dict[str, Path]) -> dict:
    members = team["members"]
    lead = next(m for m in members if m["role"] == "lead")
    out = dict(manifest)
    out["agents"] = [f"./agents/{m['id']}.md" for m in members]
    out["skills"] = [f"./skills/{name}" for name in sorted(skills)]
    out["agentName"] = lead["id"]
    out["plugin"] = manifest["name"]
    out["teamInfo"] = {
        "leadAgent": lead["id"],
        "memberAgents": [m["id"] for m in members if m["role"] == "member"],
    }
    out["members"] = [
        {
            "id": m["id"],
            "name": m["name"],
            "profession": m["profession"],
            "avatar": f"avatars/{m['id']}.png",
            "role": m["role"],
        }
        for m in members
    ]
    return out


def validate(manifest: dict, team: dict, skills: dict[str, Path]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    name = manifest.get("name", "")

    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        errors.append(f"name must be lowercase with hyphens (got {name!r})")
    if manifest.get("expertType") != "team":
        errors.append("expertType must be 'team'")
    if not re.fullmatch(r"\d+\.\d+\.\d+", manifest.get("version", "")):
        errors.append("version must be semver")
    if manifest.get("categoryId") not in CATEGORY_IDS:
        errors.append(f"unknown categoryId {manifest.get('categoryId')!r}")
    for key in ("displayName", "profession", "displayDescription", "defaultInitPrompt"):
        value = manifest.get(key) or {}
        if not (value.get("en") and value.get("zh")):
            errors.append(f"{key} needs both en and zh")
    if re.search(r"[一-鿿]", manifest.get("description", "")):
        errors.append("description must be English")

    zh_len = len((manifest.get("displayDescription") or {}).get("zh", ""))
    if not 40 <= zh_len <= 50:
        errors.append(f"displayDescription.zh must be 40-50 characters (got {zh_len})")
    if len(manifest.get("tags", [])) != 3:
        errors.append("tags must have exactly 3 entries")
    prompts = manifest.get("quickPrompts", [])
    if len(prompts) != 3:
        errors.append("quickPrompts must have exactly 3 entries")
    elif manifest.get("defaultInitPrompt") != prompts[0]:
        errors.append("defaultInitPrompt must equal quickPrompts[0]")

    author = manifest.get("author") or {}
    if not (author.get("name") and author.get("email")):
        errors.append("author needs name and email")
    elif author["email"].upper().startswith("TODO"):
        warnings.append("author.email is a placeholder — set it before submitting")

    members = team["members"]
    leads = [m for m in members if m["role"] == "lead"]
    if len(leads) != 1:
        errors.append("team needs exactly one lead")
    else:
        lead_id = leads[0]["id"]
        if lead_id == "team-lead" or not lead_id.endswith("-team-lead"):
            errors.append(f"lead file must be '<team>-team-lead' (got {lead_id!r})")
        settings = json.loads((OVERLAY / "settings.json").read_text(encoding="utf-8"))
        if settings.get("agent") != lead_id:
            errors.append("settings.json 'agent' must name the lead")

    ids = [m["id"] for m in members]
    if len(set(ids)) != len(ids):
        errors.append("duplicate member id")
    for m in members:
        if m["role"] not in ("lead", "member"):
            errors.append(f"{m['id']}: role must be lead or member")
        for s in m.get("skills", []):
            if s not in skills:
                errors.append(f"{m['id']}: preloaded skill '{s}' is not bundled")
        source = m.get("source")
        path = ROOT / source if source else OVERLAY / "agents" / f"{m['id']}.md"
        if not path.exists():
            errors.append(f"{m['id']}: agent file not found ({path.relative_to(ROOT)})")
            continue
        front, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        if not source:
            # Authored agents carry their own frontmatter; keep it in step with team.json.
            if frontmatter_value(front, "name") != m["id"]:
                errors.append(f"{m['id']}: frontmatter name must equal the file name")
            for lang in ("en", "zh"):
                for key, block in (("name", "displayName"), ("profession", "profession")):
                    want = m[key][lang]
                    got = re.search(rf"^{block}:\n(?:  .*\n)*?  {lang}:\s*\"?(.*?)\"?\s*$", front, re.M)
                    if not got or got.group(1) != want:
                        errors.append(f"{m['id']}: frontmatter {block}.{lang} must be {want!r}")
            for s in m.get("skills", []):
                if not re.search(rf"^\s+-\s+{re.escape(s)}\s*$", front, re.M):
                    errors.append(f"{m['id']}: frontmatter must preload skill '{s}'")
        description = frontmatter_value(front, "description") or ""
        if not description or re.search(r"[一-鿿]", description):
            errors.append(f"{m['id']}: agent description must be English")

    for avatar in ["team"] + ids:
        path = OVERLAY / "avatars" / f"{avatar}.png"
        if not path.exists():
            errors.append(f"avatar missing: avatars/{avatar}.png")
            continue
        if png_size(path) != (512, 512):
            errors.append(f"avatars/{avatar}.png must be a 512x512 PNG")
        if path.stat().st_size > AVATAR_MAX_BYTES:
            errors.append(f"avatars/{avatar}.png exceeds 500KB")
    if (OVERLAY / "avatars" / "PLACEHOLDER").exists():
        warnings.append("avatars are generated placeholders — replace with illustrated ones before submitting")
    return errors, warnings


def write_package(out: Path, manifest: dict, team: dict,
                  skills: dict[str, Path], support: dict[str, Path]) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / ".codebuddy-plugin").mkdir(parents=True)
    (out / ".codebuddy-plugin" / "plugin.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for name, src in skills.items():
        dst = out / "skills" / name
        shutil.copytree(src, dst, ignore=IGNORE)
        skill_md = dst / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8").replace(PERSONA_REF, PERSONA_REF_TEAM),
            encoding="utf-8")
    for rel, src in support.items():
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    shutil.copytree(ROOT / team["personas_from"], out / "personas", ignore=IGNORE)

    (out / "agents").mkdir()
    for m in team["members"]:
        dst = out / "agents" / f"{m['id']}.md"
        if m.get("source"):
            front, body = split_frontmatter((ROOT / m["source"]).read_text(encoding="utf-8"))
            dst.write_text(
                member_frontmatter(m, frontmatter_value(front, "description")) + body,
                encoding="utf-8")
        else:
            shutil.copy2(OVERLAY / "agents" / dst.name, dst)

    (out / "avatars").mkdir()
    for png in sorted((OVERLAY / "avatars").glob("*.png")):
        shutil.copy2(png, out / "avatars" / png.name)
    for name in ("settings.json", "README.md"):
        shutil.copy2(OVERLAY / name, out / name)
    # The docs name the file settings.json; the official template ships setting.json.
    shutil.copy2(OVERLAY / "settings.json", out / "setting.json")


def zip_package(out: Path, zip_path: Path, flat: bool) -> None:
    # Default layout matches the official template: one top-level folder.
    prefix = "" if flat else f"{out.name}/"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(p for p in out.rglob("*") if p.is_file()):
            z.write(f, prefix + f.relative_to(out).as_posix())


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    manifest = json.loads((OVERLAY / "plugin.json").read_text(encoding="utf-8"))
    team = json.loads((OVERLAY / "team.json").read_text(encoding="utf-8"))

    skills, support, errors = collect(team)
    rule_errors, warnings = validate(manifest, team, skills)
    errors += rule_errors
    for w in warnings:
        print(f"WARN  {w}", file=sys.stderr)
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)
    if errors:
        return 1
    if check_only:
        print(f"ok: {len(team['members'])} agents, {len(skills)} skills")
        return 0

    full = build_manifest(manifest, team, skills)
    out = DIST / manifest["name"]
    write_package(out, full, team, skills, support)
    zip_path = DIST / f"{manifest['name']}-{manifest['version']}.zip"
    zip_package(out, zip_path, flat="--flat" in sys.argv[1:])
    size_kb = zip_path.stat().st_size // 1024
    print(f"built {out.relative_to(ROOT)}/ — {len(team['members'])} agents, {len(skills)} skills")
    print(f"wrote {zip_path.relative_to(ROOT)} ({size_kb} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
