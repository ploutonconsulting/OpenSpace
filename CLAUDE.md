# OpenSpace

## Project Overview

OpenSpace is an autonomous agent framework with a skill engine, grounding agent, and MCP server. It provides skill discovery, selection, evolution, and execution analysis.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for full details on services, Docker setup, skill discovery, and key paths.

## Development

- Python 3.13, virtual env at `.venv/`
- Frontend: React + TypeScript in `frontend/`
- Package management: `uv`
- Linting/formatting: follow PEP 8, Black (88 char lines)
- Logging: loguru
