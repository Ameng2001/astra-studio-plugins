---
name: create-expert
description: Create or customize a domain expert agent for brainstorming sessions. Use when you need a specialist perspective that doesn't exist in the built-in experts, when you want to customize an existing expert for your business, or when someone says "I need an expert in X". Produces an agent definition file that other skills can use.
allowed-tools: Read, Write, Glob
user-invocable: true
---

# Create Expert

Create a new domain expert agent definition, either from scratch or by customizing an existing one. The expert can then be used in brainstorming sessions (event-storm), persona development, and other insight skills.

## Agent Lookup Order

Skills look for agent definitions in this order:
1. **Project-level**: `studio/agents/{name}.md` — your team's customized experts (git-tracked)
2. **Built-in**: shipped with studio-insight in its `agents/` directory

Project-level agents with the same filename **override** built-in ones. This lets you customize the built-in product-manager or architect to match your company's terminology and processes.

## Built-in Experts

The following experts ship with studio-insight:

| Expert | File | Domain |
|--------|------|--------|
| Product Manager | `product-manager.md` | User research, journey mapping, prioritization |
| Architect | `architect.md` | System boundaries, dependencies, feasibility |
| UX Researcher | `ux-researcher.md` | Usability, interaction patterns, validation |
| Data Analyst | `data-analyst.md` | Metrics, data flow, measurement |
| Compliance Officer | `compliance-officer.md` | Regulations, risk, audit |
| Operations Manager | `operations-manager.md` | Workflows, bottlenecks, scale |
| Child Nutrition Expert | `child-nutrition-expert.md` | Pediatric dietary planning, allergies, growth |
| Child Exercise Expert | `child-exercise-expert.md` | Pediatric movement, motor development, play |
| Elderly Nutrition Expert | `elderly-nutrition-expert.md` | Geriatric diet, chronic disease, medication interactions |
| Elderly Rehab Exercise Expert | `elderly-rehab-exercise-expert.md` | Fall prevention, mobility, rehabilitation |
| Women's Skincare Expert | `skincare-expert.md` | Dermatology, routines, ingredients, hormonal changes |

## Workflow

1. **Choose approach** — new expert, customize existing, or from template
2. **Gather domain knowledge** — understand the expertise needed
3. **Write agent definition** — produce the .md file
4. **Save** — write to project-level `studio/agents/`

## Step 1: Choose Approach

Ask the user what they need:

**A) Customize a built-in expert**
- User wants to add company-specific terminology, processes, or standards to an existing expert
- Read the built-in expert file, then modify it
- Save to `studio/agents/` with the same filename (overrides built-in)

**B) Create from template**
- User needs a domain specialist not in the built-in list
- Read the `_domain-expert-template.md` from studio-insight's agents directory
- Fill in the template with the user's domain knowledge

**C) Create from scratch**
- User has very specific requirements that don't fit the template
- Build a custom agent definition following the standard structure

## Step 2: Gather Domain Knowledge

Ask the user:
- **Expert title**: What should this expert be called? (e.g., "Pediatric Sleep Consultant")
- **Domain scope**: What area does this expert cover?
- **Key knowledge**: What does this expert know that a generalist doesn't?
- **Real-world constraints**: What practical limitations does this domain have?
- **Quality criteria**: What does "correct" mean in this domain? What mistakes are dangerous?
- **Common misconceptions**: What do outsiders get wrong about this domain?

If the user provides a brief description, extrapolate the full expert profile using your knowledge of the domain. Present the draft for validation.

## Step 3: Write Agent Definition

Follow the standard structure:

```markdown
---
name: {expert-slug}
description: {One-line summary of the domain and when to invoke this expert for review — this is what the runtime matches against to discover relevant experts, so name the domain, the topics covered, and the situations that call for it.}
---

# Role: {Expert Title}

You are a {credentials} participating in a business analysis session.

## Your Domain

{2-3 sentence domain description}

## Your Perspective

{What lens this expert sees through, what they prioritize}

## What You Contribute

### Domain Knowledge
- {Key concepts, relationships, rules}

### Real-world Constraints
- {Practical limitations outsiders miss}

### Quality Criteria
- {What "correct" means, what mistakes are dangerous}

## How You Behave in Brainstorming

- {Specific behavioral patterns with examples}

## Output Format

**Domain model:**
- Key entity: [name] — [description] — [relationships]

**Workflow reality check:**
- Proposed step → Actual practice → Gap/correction

**Constraints:**
- [constraint] — [why it matters] — [impact on design]
```

## Step 4: Save

Write the agent definition to `studio/agents/{expert-slug}.md`.

If `studio/agents/` doesn't exist, create it (with `.gitkeep`).

The filename should be kebab-case: "Pediatric Sleep Consultant" → `pediatric-sleep-consultant.md`.

Print a summary:
```
Expert created: studio/agents/{expert-slug}.md

This expert is now available for:
  /studio-insight:persona-insight   — adds domain perspective
  /studio-insight:journey-map       — validates domain workflows
  /studio-planner:event-storm       — participates in brainstorming

To use in event-storm, specify this expert when assembling roles.
To share with your team, commit studio/agents/ to git.
```

If the user customized a built-in expert, note: "This overrides the built-in {name}. Delete `studio/agents/{name}.md` to revert to the default."
