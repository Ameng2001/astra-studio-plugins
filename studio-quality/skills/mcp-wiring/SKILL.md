---
name: mcp-wiring
description: Configure MCP server connections for a plugin. Use when a plugin needs external tool access, you want to generate or update a .mcp.json file, wire up filesystem access, database connections, API integrations, or troubleshoot MCP server configuration.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
user-invocable: true
---

# MCP Wiring

Configure MCP (Model Context Protocol) server connections for a plugin by scanning skill requirements, matching to available servers, and generating a validated `.mcp.json` file.

## Workflow

1. **Identify plugin** — get the plugin directory path
2. **Scan requirements** — detect what MCP tools the skills need
3. **Match servers** — map requirements to available MCP server packages
4. **Generate config** — produce `.mcp.json` with correct entries
5. **Validate** — verify the generated config is well-formed
6. **Review with user** — confirm server choices and env var requirements

## Step 1: Identify Plugin

Accept the plugin directory path. The `.mcp.json` file will be created/updated at the plugin root, alongside `.claude-plugin/plugin.json`.

If the plugin already has a `.mcp.json`, read it first — merge new entries rather than overwriting.

## Step 2: Scan Requirements

Run the MCP requirements scanner:

```bash
python ${CLAUDE_SKILL_DIR}/../../scripts/scan_mcp_requirements.py <plugin-dir>
```

This scans all SKILL.md files to detect:
- `allowed-tools` frontmatter entries referencing MCP tools (e.g., `mcp__server__tool`)
- Tool references in body text suggesting external capabilities
- Capability keywords (database, web search, Slack, etc.)

Capture the JSON output — it contains matched servers, unmatched references, and capability hints.

## Step 3: Match Servers

Map each requirement to a known MCP server:

| Requirement | MCP Server | Package |
|------------|------------|---------|
| Filesystem access | `@anthropic/mcp-filesystem` | `npx -y @anthropic/mcp-filesystem <path>` |
| Web search | `@anthropic/mcp-web-search` | `npx -y @anthropic/mcp-web-search` |
| GitHub access | `@modelcontextprotocol/server-github` | `npx -y @modelcontextprotocol/server-github` |
| Database (PostgreSQL) | `@modelcontextprotocol/server-postgres` | `npx -y @modelcontextprotocol/server-postgres` |
| Slack | `@modelcontextprotocol/server-slack` | `npx -y @modelcontextprotocol/server-slack` |

For the latest catalog, search the MCP registry if available:
```
Use tool: mcp__mcp-registry__search_mcp_registry
```

If a requirement has no known server match, flag it for the user.

## Step 4: Generate Config

Pipe the scan output into the config generator:

```bash
python ${CLAUDE_SKILL_DIR}/../../scripts/scan_mcp_requirements.py <plugin-dir> | \
python ${CLAUDE_SKILL_DIR}/../../scripts/generate_mcp_json.py <plugin-dir> --merge
```

The `--merge` flag preserves existing entries in `.mcp.json`. The script generates the standard structure:

```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "@package/name", "..."],
      "env": {
        "KEY": "${ENV_VAR}"
      }
    }
  }
}
```

Use `${CLAUDE_PLUGIN_ROOT}` for plugin-relative paths.
Use `${CLAUDE_PLUGIN_DATA}` for persistent data paths.
Both names also resolve on CodeBuddy / WorkBuddy, which treat them as aliases of `${CODEBUDDY_PLUGIN_ROOT}` and `${CODEBUDDY_PLUGIN_DATA}` — keep the `CLAUDE_*` form so one file works on both runtimes.

## Step 5: Validate

Run the MCP config validator:

```bash
python ${CLAUDE_SKILL_DIR}/../../scripts/validate_mcp_config.py <plugin-dir>
```

This checks: .mcp.json is valid JSON, each server has a command field, environment variables are documented, and the plugin manifest references .mcp.json.

## Step 6: Review with User

Present the generated config:
1. List each MCP server and what it provides
2. Highlight environment variables that need to be set
3. Note any unmatched requirements
4. Confirm the user wants to write the config

After approval, update the plugin manifest to reference `.mcp.json` if not already declared.
