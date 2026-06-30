# Astra Studio

A **Claude Code plugin marketplace** for planning, designing, validating, and shipping plugins.

Astra Studio handles the **outer loop** of plugin development — business analysis, domain modeling, plugin validation, and promotion. Individual skill authoring and testing is executed through the official [`skill-creator`](https://github.com/anthropics/skills/tree/main/skills/skill-creator) as an internal pipeline capability.

## Plugins

| Plugin | Skills | What it does | Install |
|--------|--------|-------------|---------|
| **studio-core** | 4 | Workspace management — init, promote, status, create-expert | `claude plugin install studio-core@astra-studio` |
| **studio-insight** | 6 | Business analysis toolkit — personas, journeys, processes, domains | `claude plugin install studio-insight@astra-studio` |
| **studio-planner** | 5 | Planning pipeline — event storming, domain modeling, skill design, build orchestration | `claude plugin install studio-planner@astra-studio` |
| **studio-quality** | 2 | Quality assurance — plugin validation, MCP wiring | `claude plugin install studio-quality@astra-studio` |
| **studio-docs** | 6 | Document delivery — blueprints, writing experts, parallel generation, export | `claude plugin install studio-docs@astra-studio` |
| **studio-platform** | 6 | Platform docs — brainmap, agent mapping, tech designs, visualization, speech | `claude plugin install studio-platform@astra-studio` |
| **studio-design** | 4 | Design-to-code — screenshot → Pencil prototype → OpenSpec proposal → working code | `claude plugin install studio-design@astra-studio` |
| **studio-ontology** | 3 | **FDE pipeline** — compile business analysis (DDD/event-storm, or a domain-intake spec) into a runnable [clife-onto-engine](https://github.com/Ameng2001/clife-onto-engine) ontology plugin (five-element schema: Object/Link/Function/Rule/Action) | `claude plugin install studio-ontology@astra-studio` |

> **studio-ontology** bridges astra-studio (modeling) to **[clife-onto-engine](https://github.com/Ameng2001/clife-onto-engine)** (OAG runtime). It is the Palantir-style **Foundry → FDE → Ontology** modeling front-end: business analysis → five-element ontology IR → a runnable governed-action plugin. Validated end-to-end on real material (output isomorphic to the hand-built `plugins/grass`).

## Quick Start

```bash
# 1. Register the marketplace
claude plugin marketplace add github:VanLengs/astra-studio-plugins

# 2. Install what you need
claude plugin install studio-core@astra-studio
claude plugin install studio-insight@astra-studio
claude plugin install studio-planner@astra-studio
claude plugin install studio-quality@astra-studio
claude plugin install studio-platform@astra-studio

# 3. Initialize studio in your project
/studio-core:init

# 4. Plan a plugin
/studio-planner:plan "your business domain"

# 5. Confirm the build plan
# → Astra Studio generates specs, then produces initial skill drafts in {target_dir}/

# 6. Test and iterate each skill with real inputs
# → Use skill-creator to refine individual skills

# 7. Validate the plugin
/studio-quality:validate {target_dir}

# 8. Ship a version (design docs stay active for next iteration)
/studio-core:promote {plugin-name}
```

## studio-insight: Standalone Artifact Skills

These 6 skills produce professional deliverables independently — no pipeline required:

```bash
/studio-insight:persona-insight "全职妈妈，两个孩子"    # → persona card + empathy map
/studio-insight:journey-map "日常营养管理流程"           # → journey map + emotional curve
/studio-insight:process-flow "从记录饮食到生成周报"      # → process diagram + decision points
/studio-insight:domain-canvas "儿童健康管理"             # → domain boundary map + classification
/studio-insight:behavior-matrix "儿童健康管理"           # → actor × action × event × data table
/studio-insight:opportunity-brief "儿童健康管理"         # → prioritized opportunity assessment
```

## studio-planner: Planning Pipeline

`/studio-planner:plan` chains 5 pipeline skills:

```
Step 1: event-storm
  Multi-role brainstorming (PM, architect, domain experts)
  → Invokes: persona-insight, journey-map, process-flow
  → Identifies: KB dependencies, expert scope (planning vs runtime)
  → Writes: studio/changes/{domain}/event-storm.md

        ↓ user confirms

Step 2: domain-model
  Clusters events into business domains, draws plugin boundaries
  → Full analysis mode: invokes domain-canvas, behavior-matrix, opportunity-brief
  → Fast mode: skips insight tools, goes directly to plugin proposals
  → Writes: studio/changes/{domain}/domain-map.md
  → Creates: {target_dir}/ scaffold for each plugin candidate

        ↓ user confirms

Step 3: skill-design
  Breaks each plugin into skills with interfaces and data flow
  → Detects plugin traits: stateful, hil-gated, kb-dependent, multi-pipeline, expert-scoped
  → Assesses complexity: prompt-only / scripts / MCP
  → Writes: studio/changes/{plugin}/skill-map.md (includes traits + pipelines)

        ↓ user confirms

Step 4: spec-generate
  Auto-generates specification files + trait-conditional scaffolding
  → Design docs → studio/changes/ (brief.md, plugin.json.draft)
  → Implementation → {target_dir}/ (SKILL.md skeletons, commands)
  → If stateful: init-workspace skill + runtime config/status templates
  → If hil-gated: approval gate sections in relevant skills
  → If multi-pipeline: per-pipeline orchestration commands
  → Advances status: planning → building

Step 5+: build, test, validate, promote
  build-skills produces initial skill drafts via skill-creator (working first drafts)
  → Test each skill with real inputs, iterate with skill-creator
  /studio-quality:validate validates {target_dir}/ and advances to approved
  /studio-core:promote creates a versioned milestone (v0.1 → v0.2)
  → Design docs are snapshotted to archive but stay active for next iteration
```

## studio-platform: Platform Documentation Generator

After completing the planning pipeline, generate a full platform documentation suite:

```bash
# Generate all platform docs
/studio-platform:platform-docs {domain-name}

# Generate specific steps only
/studio-platform:platform-docs {domain-name} --steps brainmap,mapping

# With reference .pen file for visualization
/studio-platform:platform-docs {domain-name} --reference /path/to/ref.pen
```

Pipeline (6 steps):

```
Step 1: generate-brainmap
  → brainmap-index.md + brainmap-module-1~N.md
  Organizes agents by module with skill/data/model dependencies

Step 2: generate-agent-mapping
  → agent-plugin-mapping.md
  Maps agents ↔ plugins ↔ skills with architecture diagrams

Step 3: generate-tech-designs  (parallelized)
  → data-warehouse-design.md     (Kimball dimensional modeling)
  → data-collection-design.md    (IoT/edge-cloud pipelines)
  → knowledge-graph-design.md    (ontology, sub-graphs, reasoning)
  → ml-models-design.md          (model cards, MLOps pipeline)
  → rag-system-design.md         (knowledge bases, hybrid retrieval)

Step 4: generate-project-plan
  → project-plan.md              (Gantt, milestones, team, risks)

Step 5: generate-platform-visual
  → {platform-name}.pen          (4-frame design visualization)

Step 6: generate-speech
  → 迎检话术-{platform-name}.md   (presentation speech script)
```

Domain-agnostic — works for any industry vertical (elderly care, education, healthcare, etc.).

### Subagent Roles

Both studio-insight and studio-planner use multiple perspectives via subagent roles:

| Role | Perspective | Used by |
|------|------------|---------|
| **Product Manager** | User personas, journey mapping, prioritization | persona-insight, journey-map, opportunity-brief, event-storm |
| **Architect** | System boundaries, dependencies, feasibility | process-flow, domain-canvas, behavior-matrix, domain-model |
| **Domain Expert** | Domain-specific knowledge, real-world constraints | All 6 insight skills (dynamically discovered) |

11 built-in domain experts ship with studio-insight — general roles (UX researcher, data analyst, compliance officer, operations manager) plus health and beauty domain experts.

### Customizing Experts

```bash
# Create a new domain expert
/studio-core:create-expert 宠物营养专家

# Customize a built-in expert with your company's terminology
/studio-core:create-expert customize product-manager
```

Custom experts are saved to `studio/agents/` (git-tracked, team-shared) and automatically discovered by all insight skills at runtime. Project-level experts override built-ins with the same filename.

## studio-ontology: Ontology Compilation (FDE Pipeline)

Compile business analysis into a **runnable digital-intelligent ontology** — a [clife-onto-engine](https://github.com/Ameng2001/clife-onto-engine) plugin with the five-element schema (**Object / Link / Function / Rule / Action**). This is the Palantir-style **Foundry → FDE → Ontology** modeling front-end: astra-studio models the business; clife-onto-engine runs it as a governed OAG (do/query, ontology backstop, audit, rollback).

### Two input entries

| Entry | When | Source |
|-------|------|--------|
| **A. studio-planner artifacts** | already ran the planning pipeline | behavior-matrix / domain-canvas / process-flow / event-storm |
| **B. Domain Intake spec** | no planner run — FDE intakes the domain directly | `studio-ontology/templates/domain-intake.md.tmpl` |

### Domain Intake — the standard input spec

`domain-intake.md.tmpl` is the **standard input contract** an FDE hands to the business owner / domain expert / rule engineer. **11 role-annotated sections**, each mapping to a five-element target — so a filled intake compiles straight into an ontology:

| Intake section | Filled by | → Ontology element |
|----------------|-----------|--------------------|
| §3 Entities | domain expert / FDE | **Object** |
| §4 Relationships (with causal nature) | domain expert / FDE | **Link** (root_cause / hypothesis / derivation) |
| §6 Metrics / KPIs | domain expert | **Function** (read-only derived) |
| §7 Rules + §8 Decision points | domain expert + rule engineer | **Rule** (severity · declarative/function · source+citations) |
| §5 Operations + §2 Actors + §11 Governance | business owner + FDE | **Action** (params / guards / post-rules / writes / HIL) |
| §9 Data sources | data/IT | Mapping registry |
| §10 Competency Questions | business owner + FDE | CQ (acceptance) |

It lets you start **without** the full event-storm: hand the template to the business, collect it, feed it to `ontology-map`. Worked examples: **grass** (inline in the template) + **[chili](studio-ontology/examples/domain-intake-chili.md)** — same template, different domain, consistent five-element output.

### Pipeline (3 skills)

```
business analysis (planner artifacts) ─┐
or a filled domain-intake.md          ─┴→ ontology-map   (five-element IR)
                                              ↓ user reviews IR (rule classification + provenance)
                                          ontology-compile → clife-onto-engine plugin
                                              declarative rules auto-generated;
                                              function-backed rules / action write-back = skeletons w/ TODO(FDE)
                                              ↓ FDE fills the skeletons in the engine repo
                                          ontology-validate  (structure · governance audit · behavioral CQ)
```

```bash
/studio-ontology:model {ontology_id}     # map → review → compile
# or step by step:
/studio-ontology:map {ontology_id}
/studio-ontology:compile {ontology_id}
/studio-ontology:validate {ontology_id}
```

Validated end-to-end on real material: the compiled grass plugin is **structurally isomorphic** to the hand-built `plugins/grass` in clife-onto-engine (7 objects, 3 links, 1 function, 6 rules, 2 actions).

## Full Workflow

One upstream analysis feeds **two layers** of the same system — they are **not** mutually exclusive. studio-ontology produces the **governed substrate** (the ontology / OAG runtime); studio-planner produces the **interaction layer** (skills / agents). At runtime the skills **call** the ontology — skills are the caller, the ontology is the callee.

```
/studio-core:init → studio/
        +
business analysis  (studio-planner:plan)
  event-storm → domain-model → behavior-matrix / domain-canvas / process-flow
        │  one analysis, two layers
        ├──────────────────────────────────────────────┬───────────────────────────────────────
        ▼  INTERACTION layer  (how users/agents talk)   ▼  GOVERNED substrate  (what can be done, & the rules)
   studio-planner                                    studio-ontology   (or a filled domain-intake.md as input)
     skill-design → spec-generate                      ontology-map → ontology-map.md (5-element IR)
       → Claude Code plugin (skills/agents)                ↓ user reviews IR
       → build-skills → /studio-quality:validate         ontology-compile
       → /studio-core:promote (v0.1 → v0.2)                 → {target_dir}/__init__.py + mappings + cq + plugin.yaml
        │                                                   (declarative auto; function-backed/write-back = TODO(FDE))
        │                                                    ↓ FDE fills skeletons
        │                                                  ontology-validate (structure · governance · CQ)
        │                                                    ↓
        │   ── at runtime, skills CALL the ontology ──▶  clife-onto-engine  plugins/{ontology_id}/
        │      (MCP tools / Session.ask / HTTP /ask)     governed OAG: do (Action: guard→rollback→audit) / query (OQL)
        └──────────────────────────────────────────────────┘

Deployment shapes (pick per product, not a hard fork):
  • Governed assistant   = BOTH layers — skills on top of the ontology        ← most common
  • Structured UI / workflow = ontology only — forms/workflow call Action endpoints (no conversational skills)
  • Open Q&A / RAG only   = skills/RAG only — no governed writes, no ontology
```

## Architecture

Astra Studio is a **marketplace** (collection of plugins), not a monolithic plugin. Each plugin is independently installable:

- **studio-core**: Zero dependencies. Manages the `studio/` workspace.
- **studio-insight**: Zero dependencies. Standalone business analysis tools. Useful beyond plugin development.
- **studio-planner**: Depends on studio-core and studio-insight. Orchestrates the planning pipeline.
- **studio-quality**: Zero dependencies. Can validate any plugin, not just studio-managed ones.
- **studio-platform**: Depends on studio-core. Generates industry AI model platform documentation from planning artifacts.

The `studio/` directory is **git-tracked** — it holds development documentation (briefs, design decisions, status) with version control value. Inspired by [OpenSpec](https://github.com/Fission-AI/OpenSpec)'s spec-driven workspace pattern.

Domains evolve **incrementally** — re-running the pipeline updates artifacts in-place (git diff = revision history), `changelog.md` logs each iteration, and only affected plugins (`create`/`modify`) get change workspaces.

Promotion creates versioned milestones — design docs are **copied** (not moved) to `studio/archive/{plugin}/{date}-iteration-{N}/`, so the active workspace remains available for continued iteration. Version numbers increment automatically (v0.1.0 → v0.1.1).

## Development

```bash
# Test locally
claude --plugin-dir ./studio-core --plugin-dir ./studio-insight --plugin-dir ./studio-planner --plugin-dir ./studio-quality --plugin-dir ./studio-docs --plugin-dir ./studio-platform
```

## License

Apache-2.0
