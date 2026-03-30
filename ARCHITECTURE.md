# OpenSpace Architecture

## Services

- **MCP Server** (native) — spawned by Claude.app as `openspace-mcp` (stdio transport). Handles skill discovery, task execution, and DB sync. Can also run standalone with `python -m openspace.mcp_server --transport sse`.
- **Dashboard** (Docker) — Flask API + React frontend served from a single container on port 7788. Read-only against the DB; does not discover skills.

## Dashboard Docker Setup

- **Compose file:** `docker-compose.dashboard.yml`
- **Dockerfile:** `Dockerfile.dashboard` (multi-stage: Node frontend build + Python backend)
- **Container name:** `openspace-dashboard`
- **Port:** 7788
- **Volume mounts:**
  - `.openspace/` -> `/app/.openspace` (SQLite DB)
  - `logs/` -> `/app/logs`
- **Build:** `docker compose -f docker-compose.dashboard.yml build`
- **Run:** `docker compose -f docker-compose.dashboard.yml up -d`

The dashboard reads all data from the shared SQLite DB at `.openspace/openspace.db`. Skill discovery and DB writes happen in the native MCP server processes, not in the Docker container.

## Skill Discovery

Skills are discovered from directories configured in three places (priority order):
1. `OPENSPACE_HOST_SKILL_DIRS` env var (comma-separated paths)
2. `skills.skill_dirs` in `openspace/config/config_grounding.json` (currently `~/.claude/skills`)
3. Built-in skills at `openspace/skills/`

Discovery runs once at MCP server startup via `SkillRegistry.discover()`. New skills added after startup are only picked up when `execute_task` triggers `_auto_register_skill_dirs`, or on server restart.

Each skill directory must contain a `SKILL.md` file. A `.skill_id` sidecar is auto-generated on first discovery to provide stable identity across restarts.

## Key Paths

- **Database:** `.openspace/openspace.db` (SQLite, WAL mode)
- **Skill store:** `openspace/skill_engine/store.py`
- **Skill registry:** `openspace/skill_engine/registry.py`
- **MCP server:** `openspace/mcp_server.py`
- **Dashboard server:** `openspace/dashboard_server.py`
- **Tool layer (init):** `openspace/tool_layer.py`
- **Config:** `openspace/config/config_grounding.json`
