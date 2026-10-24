# v0.1.0 release readiness check

Checked on 2026-10-23 using Windows and Python 3.12, in a new virtual environment and a separate source snapshot with synthetic configuration.

## Sensitive files

- Scanned publishable working-tree files and every blob reachable from the current local Git history for common credential patterns, private keys, private-network URLs, personal filesystem paths, and matches against local secret values. No sensitive matches were found. Credential-like test literals were reviewed as synthetic fixtures.
- No database, upload directory, private environment file or backup was included in the publishable file set.
- Confirmed ignore rules for environment variants, databases and journals, backups, private-key files, dependencies and deployment build metadata. `.env.example` remains publishable.
- Checked local Markdown links and PNG metadata; no broken links or embedded metadata were found.

## Installation and startup

- Installed `requirements.txt` in a fresh virtual environment; `pip check` reported no dependency conflicts.
- Started the real HTTP server with a new database and no real service credentials. Verified database/upload creation, rejected unauthenticated requests, rejected an incorrect password, successful login, v0.1.0, an empty conversation list, conversation creation, and static resources.
- Restarted the server and verified that both the conversation and signed login remained valid with a stable session secret.
- Installed the optional music dependencies with `npm ci` in the clean snapshot; all eight offline music bridge tests passed.

## Regression and corrections

- Python compilation and all 368 Python regression tests passed with external TCP connections blocked.
- Isolated the smoke test's delayed push worker from its temporary database; reran the five smoke tests and eight Anthropic tests successfully.
- Disabled debug mode by default in code and in the environment template.
- Expanded platform-specific setup instructions and common first-run troubleshooting. Moved detailed preference documentation to `configuration.md`.
- Removed the obsolete instruction to start the stdin-driven music bridge manually; documented the shared music connection entry.
- Added offline music bridge tests to CI.

## Scope

This is a release hygiene and startup check, not a comprehensive application security audit. Real AI-provider requests, Web Push delivery, cloud backups and an internet-facing production deployment were not exercised. macOS/Linux installation was documented but not executed locally. The public repository was selected as `ariaprism/Morrow`; README clone URLs have been filled in. No release was published by this check.

## Configuration follow-up

- Standardized the code default, environment template and README URL on port 5151. `PORT` remains configurable.
- Updated the main and diagnostic default model to `deepseek-flash`, verified against the official DeepSeek documentation. Missing, empty and whitespace-only `LLM_MODEL` values fall back to this model; custom model IDs are preserved.
- Kept the legacy `data/nook.db` path to preserve existing installations, and documented why a path change is not a data migration.
- Verified all four model configuration cases and the default port in isolated processes; the five smoke and eight Anthropic tests passed after this change.

## VPS and local push follow-up

- Added a detailed Chinese VPS guide linked from both READMEs, with systemd and Caddy examples. A real Linux/VPS rollout remains unverified locally.
- Added `wsgi.py` so a single non-preloaded production worker starts the existing scheduler. Its startup contract is covered by a regression test.
- Replaced Android-only push gating with secure-context and browser capability detection; updated permission and delivery feedback and added a test-request timeout.
- All nine push tests and the new WSGI test passed. An isolated browser confirmed desktop localhost support, insecure-origin rejection, delivery success/error feedback and valid inline JavaScript. No real notification permission was requested and no real push was sent.
- Documented the Quick Tunnel SSE limitation and the distinction between push-provider acceptance and device display.
