# Browser-local demo

This build reuses `static/index.html` and the bundled public assets. A browser-only API
adapter supplies fictional data and stores visitor edits in a separate localStorage
namespace. It never connects to the Flask server or opens its SQLite database.

Build from the repository root:

```sh
python scripts/build_demo.py
python -m http.server 5178 --bind 127.0.0.1 --directory data/demo-site/dist
```

Open `http://127.0.0.1:5178`. No environment file or provider credentials are needed.
Build output stays in ignored `data/demo-site/dist`. Do not deploy the repository root.

## Behavior

- Chat, history, notes, todos, memory, diary and visual preferences use the existing UI.
- Default chat replies are clearly marked preset demonstrations, not model output.
- Room footprints, future notes and proactive messages are fictional records. They do not
  run in the background. Push, MCP, uploads, cloud backup and scheduling fail closed.
- The demonstration controls provide a JSON data export and a destructive reset with confirmation.
- Browser-local storage is not a backup. Clearing site data removes edits; clearing only the
  HTTP cache may not. Browsers and devices do not share data. A reset restores the fictional seed.
- Optional bring-your-own-key mode supports HTTPS OpenAI-compatible text endpoints that permit
  browser CORS. It sends the last 20 messages of the active conversation and the current persona.
  It does not call tools or perform automatic memory retrieval. Keys and endpoint configuration
  remain in page memory and are excluded from localStorage and exports. Refresh clears them.
- Model requests go directly to the chosen provider with no site-owned proxy, cookies or redirects.
  The provider receives the key and conversation. The demo operator does not provide model credits.

## Cloudflare Pages with Git integration

Create a Pages project in your own Cloudflare account and connect the selected repository.
Select the intended production branch and use:

| Setting | Value |
| --- | --- |
| Framework preset | None |
| Root directory | Repository root |
| Build command | `python scripts/build_demo.py` |
| Build output directory | `data/demo-site/dist` |

No runtime secrets, database bindings, Flask process, Functions or Worker are required.
Pushes to the selected branch rebuild the demo automatically. The builder derives the page
from the current product UI; new API behavior still needs a corresponding demo adapter update.
Before pushing, review the staged diff and public asset list. Do not publish local runtime files.

For a subdomain such as `morrow.example.com`, keep the registrar and DNS provider unchanged:

1. Add that hostname in Pages > Custom domains first.
2. At the authoritative DNS provider, add a CNAME record named `morrow`, targeting the exact
   `project-name.pages.dev` hostname shown by Pages (no scheme or path).
3. Wait for domain and HTTPS activation. Retain unrelated DNS records.

Do not create the CNAME before associating the hostname with the Pages project. A custom
apex domain has different nameserver requirements. See [Cloudflare custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/).

## Cloudflare Workers Builds

Workers projects use a deploy command rather than the Pages output-directory field.
The root `wrangler.json` declares the static asset directory and a pinned runtime
compatibility date. No Worker script, account ID, credentials or database bindings
are included. Keep the dashboard Worker name consistent with the configuration name.

| Setting | Value |
| --- | --- |
| Production branch | The branch containing this demo |
| Build command | `python scripts/build_demo.py` |
| Deploy command | `npx wrangler deploy` |
| Root directory | Repository root |

The explicit command `npx wrangler deploy --name morrow --assets ./data/demo-site/dist`
also reads the compatibility date from `wrangler.json`. A missing compatibility date
causes deployment to fail even when the static build succeeds. A new push triggers
a new build for the selected branch; an old build record retains its original commit.
Workers custom-domain setup differs from Pages; follow the dashboard instructions
for the selected product when attaching a domain.

## Checks

```sh
node --test tests/static_demo.test.mjs
python -m unittest discover -s tests -p "test_static_demo.py"
```

Also run the repository-wide required checks. Browser QA should include desktop and mobile,
the room and alarm views, a chat round trip, a reload, export/reset and mocked BYOK requests.
Provider tests must never call a real model or use real credentials.
