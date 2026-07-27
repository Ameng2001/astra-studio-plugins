# Astra Studio Plugins

This is a marketplace of Claude Code plugins for plugin development workflows. Each subdirectory is a standalone plugin.

## Repository Structure

Toolchain plugins (`studio-*`) — the studio itself:
```
├── studio-core/         # Workspace management (init, promote, status, create-expert) — 4 skills
├── studio-insight/      # Business analysis toolkit (personas, journeys, processes, domains) — 6 skills
├── studio-planner/      # Planning pipeline (event-storm, domain-model, skill-design, spec-generate, build-skills) — 5 skills
├── studio-quality/      # Quality assurance (plugin validation, MCP wiring) — 2 skills
├── studio-docs/         # Document delivery suite (blueprints, writing experts, parallel generation, export) — 6 skills
├── studio-platform/     # Platform documentation generator (brainmap, agent mapping, tech designs, visualization) — 6 skills
├── studio-design/       # Design-to-code (screenshot → Pencil → OpenSpec → code) — 3 skills
├── studio-ontology/     # FDE pipeline — compile business analysis into a clife-onto-engine ontology — 3 skills
```

Vertical plugins — built *with* the toolchain, shipped from the same marketplace:
```
├── fund-review/         # 政府信息化采购报价优化 + 财评预审 — 4 skills, 40 Python scripts
```

## Plugin Dependencies

```
studio-core      (zero deps)
studio-insight   (zero deps)
studio-planner   (depends on studio-core + studio-insight)
studio-quality   (zero deps)
studio-docs      (depends on studio-core; consumes artifacts from studio-planner + studio-insight)
studio-platform  (depends on studio-core; consumes artifacts from studio-planner + studio-insight)
studio-design    (zero deps; needs the Pencil MCP server for .pen files)
studio-ontology  (depends on studio-core; targets the clife-onto-engine runtime)
fund-review      (zero plugin deps; needs Python with openpyxl + python-docx)
```

`fund-review` is not part of the toolchain — it is a domain plugin the toolchain produced, kept here so it ships through the same marketplace. It is `stateful`: sessions live under `.fund-review/{session-id}/` in the user's project, not under `studio/`. Its design documents live in the separate `fund-review-plugins` repo.

## Plugin Structure

Each plugin follows this layout:
```
plugin-name/
├── .claude-plugin/plugin.json   # Plugin manifest (name, description, version)
├── commands/                    # Slash commands (.md files)
├── skills/                      # SKILL.md files for specific tasks
│   └── skill-name/SKILL.md
├── agents/                      # Subagent role definitions (.md files)
├── hooks/                       # Event-driven automation (hooks.json)
├── scripts/                     # Python helper scripts
├── references/                  # Reference docs loaded on demand
└── templates/                   # File templates for scaffolding
```

## Key Files

- `.claude-plugin/marketplace.json`: Marketplace manifest — registers all 9 plugins with source paths
- `.claude-plugin/plugin.json`: Root-level marketplace metadata
- `*/plugin.json`: Per-plugin metadata — name, description, version, dependencies
- `commands/*.md`: Slash commands invoked as `/plugin:command-name`
- `skills/*/SKILL.md`: Detailed knowledge and workflows with YAML frontmatter (name, description, allowed-tools)
- `agents/*.md`: Subagent role definitions — personas adopted by Claude during multi-role brainstorming
- `templates/`: Scaffolding templates used by init skill to create `studio/` in user projects

## Agent System

studio-insight ships 11 built-in agent roles + 1 template:
- **General roles**: product-manager, architect, ux-researcher, data-analyst, compliance-officer, operations-manager
- **Health domain**: child-nutrition-expert, child-exercise-expert, elderly-nutrition-expert, elderly-rehab-exercise-expert
- **Beauty domain**: skincare-expert
- **Template**: `_domain-expert-template.md` for creating new experts

Agent lookup order: `studio/agents/` (project-level) > built-in `agents/` (plugin-level). Same-name overrides.

All 6 insight skills use **dynamic expert discovery** — they scan for relevant domain experts at runtime and invoke them for review via the Agent tool.

**Expert scope**: Planning-phase experts (PM, architect) stay in studio-insight. Runtime experts (domain specialists needed by generated plugins) are shipped with the plugin in `{target_dir}/agents/`. The `expert-scoped` trait in skill-design distinguishes the two.

## Design Principles

1. **Spec-implementation separation**: `studio/changes/` holds only design documents (brief, skill-map, status, plugin.json.draft). Implementation files (SKILL.md, commands, scripts) live directly in the target plugin directory — single source of truth, no duplication.
2. **Plugin-first**: The workspace (`studio/changes/`) is organized by plugin, not by skill
3. **Outer loop orchestration**: Astra Studio orchestrates planning, build, validation, and shipping, while individual skill authoring is executed through the built-in `anthropic-skills:skill-creator`
4. **Git-tracked workspace**: `studio/` directory in user projects is version-controlled (briefs, status, design decisions)
5. **Standard Claude Code plugin spec**: No proprietary extensions — works with any Claude Code installation
6. **Platform-neutral outputs**: SKILL.md skeletons produced by spec-generate contain no Claude-specific references — they run on any compatible runtime
7. **Customizable experts**: Users create domain experts via `/studio-core:create-expert`, saved to `studio/agents/` (git-tracked, team-shared)
8. **Plugin traits**: skill-design detects cross-cutting characteristics (stateful, hil-gated, kb-dependent, multi-pipeline, expert-scoped) that drive conditional scaffolding in spec-generate
9. **Runtime workspace convention**: Stateful plugins get `.{plugin-name}/` runtime workspace scaffolding — separate from `studio/` which serves plugin development
10. **Initial fill, not final build**: build-skills produces working first drafts; users iterate with skill-creator before validation
11. **Versioned promotion**: promote creates versioned milestones (v0.1 -> v0.2); design docs are snapshotted to archive but stay active for continued iteration

## Planning Pipeline

`/studio-planner:plan` chains 5 pipeline skills:

```
event-storm → persona-insight + journey-map + process-flow + KB deps + expert scope
     ↓
domain-model → [optional: domain-canvas + behavior-matrix + opportunity-brief]
     ↓         (full analysis mode — or fast mode skips these)
skill-design → plugin traits detection + skill breakdown
     ↓
spec-generate → SKILL.md skeletons + trait-conditional scaffolding
     ↓           (runtime workspace, HIL gates, pipeline commands)
build-skills → initial drafts via skill-creator (test & iterate before validation)
```

## Development Workflow

1. Edit SKILL.md files directly — changes take effect immediately when loaded via `--plugin-dir`
2. Test commands with `/plugin:command-name` syntax (e.g., `/studio-core:init`)
3. Skills trigger automatically when their description matches user intent
4. Test locally: `claude --plugin-dir ./studio-core --plugin-dir ./studio-insight --plugin-dir ./studio-planner --plugin-dir ./studio-quality` (add `--plugin-dir` for whichever others you are touching)
