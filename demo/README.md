# Standalone browser demo

This is a separate static demo of the actual Morrow interface. The regular Flask
application and its authentication are unchanged. Fictional Chinese examples
cover plants, meals, flowers, notes, memories, and a diary.

## Build and host

From the repository root, run:

```bash
python scripts/build_demo.py
```

Only the Python standard library is needed. Publish **the contents of `dist/`**,
never the repository root, to Cloudflare Pages. In a Git-connected Pages project,
use `python scripts/build_demo.py` as the build command and `dist` as the output
directory. A Direct Upload deployment can upload a ZIP containing these files at
its root. Cloudflare supplies the Pages URL; a custom domain can be bound later.

## Behavior

- The normal UI is copied from `static/index.html` at build time.
- A demo-only adapter answers `/api/` requests inside the browser. It never
  forwards unknown API requests to a real backend.
- Chat replies are preset and identified as such. No model credits are used.
- Chat sessions, edits to text messages, memories, notes, diary pages, comments,
  favorites, todos, identity, and selected appearance preferences are local.
- Each browser has its own localStorage. Reloading preserves local changes;
  clearing site data removes them. Reset restores the fictional starting state.
- Provider credentials are empty and their inputs/saving are disabled. API,
  MCP, music Cookie, TTS, push and backup integrations are unavailable.
- Unsupported actions display a demo notice; no real integration is simulated
  as a successful live connection.
- No service worker is shipped. The demo origin should be separate from any
  personal Morrow installation. `_headers` adds a same-origin connection policy
  on Cloudflare Pages. The demo does not include analytics or remote fonts.
- If storage fills, new changes remain in memory and a notice explains that
  they will be lost when the page closes. Do not use the demo as a real diary.

## Checks and updating sample data

```bash
node --test tests/demo.test.mjs
python -m unittest discover -s tests -p 'test_demo_build.py'
```

`demo/fixtures.js` contains public fictional data only. It is generated from a
new temporary database by `python scripts/demo_fixtures.py` (requires the normal
Python dependencies). The generator disables dotenv and overrides database,
authentication and session settings before importing the application. It only
reads an explicit list of local endpoints and never opens an existing household
database. Review regenerated fixtures before committing.
