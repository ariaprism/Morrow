# AGENTS.md

This file defines the repository-wide working agreement for human and automated contributors. Keep it public-safe: never add private deployment details, credentials, personal conversation content, or machine-specific paths.

## Project shape

- `app.py` is the Flask entry point and owns HTTP routes, the SQLite schema, and migrations.
- `chat_engine.py` handles model orchestration; focused service modules cover backups, exports, scheduling, push, voice, music, recall, and provider integrations.
- `tools.py` contains built-in assistant tools. Treat changes here as access to personal data and test authorization boundaries carefully.
- `static/` is the Chinese-first single-page UI and PWA. There is no separate frontend build step.
- `tests/` contains the automated `unittest` regression suite.
- `scripts/` contains opt-in operator diagnostics. Scripts may use real configured services and must never run during automated tests.
- Runtime databases, uploads, exports, backups, and diagnostic logs belong under `data/` and must remain untracked.

## Local setup

Use Python 3.11 or newer. Copy `.env.example` to `.env`, install `requirements.txt`, and set synthetic local values for development. Node.js is only needed for the optional music bridge.

Run the application from the repository root with `python app.py`. Code and tests assume the repository root is the current working directory.

## Required checks

Before handing off a change, run from the repository root:

```bash
python -m compileall -q -x '(^|[\\/])(?:\.venv|venv|node_modules|data)([\\/]|$)' .
python -m unittest discover -s tests -p "test_*.py"
```

Use explicit synthetic environment values when a local `.env` may contain real configuration:

```text
AUTH_PASSWORD=test-password-only
SECRET_KEY=test-secret-key-only
FLASK_DEBUG=0
```

Add or update regression tests for behavior changes. Keep tests deterministic, offline, and isolated with temporary databases. Mock provider, network, push, GitHub, Supabase, MCP, and subprocess boundaries; never require real credentials. Put new automated tests in `tests/test_<area>.py`.

## Change guidelines

- Preserve the single-household privacy model and the quiet chat surface: provider, tool, memory, and internal routing details should not leak into ordinary messages.
- Keep schema changes backward compatible. Add idempotent migrations for existing databases and cover both fresh initialization and upgrade behavior.
- Keep API and UI changes in sync. `static/index.html` is intentionally large, so make targeted edits and add contract tests for important markup or interaction behavior.
- Keep optional integrations disabled or harmless when unconfigured. Fail clearly without exposing secrets, request bodies, private URLs, or filesystem locations.
- Prefer small, focused modules and changes, but do not perform unrelated rewrites solely to reduce the size of established files.
- Update `README.md`, `.env.example`, `CONTRIBUTING.md`, and CI when setup, configuration, paths, or verification commands change.

## Public safety checklist

- Never commit `.env`, `data/`, databases, uploads, backups, exports, cookies, tokens, provider keys, MCP credentials, personal prompts, generated conversations, private hostnames, email addresses, repository coordinates, or local absolute paths.
- Examples and fixtures must use reserved domains such as `example.com` or `example.test`, synthetic secrets, and fictional content.
- Treat logs and manual diagnostic output as sensitive. Store them only in ignored locations and redact them before including excerpts in issues or pull requests.
- Do not weaken authentication, secret handling, backup encryption, path validation, or tool authorization for convenience.
- Before publishing, inspect the staged diff and tracked file list for secrets and identifying strings. Report vulnerabilities through the private process in `SECURITY.md`, not a public issue.
