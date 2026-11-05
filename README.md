# Morrow

**English** | [简体中文](README.zh-CN.md)

<a href="https://github.com/DasterProkio/awesome-ai-companion">
  <img src="https://raw.githubusercontent.com/DasterProkio/awesome-ai-companion/main/assets/featured-in-awesome-ai-companion.png" alt="Featured in Awesome AI Companion" width="400">
</a>

See you tomorrow. See you today, too.

You talk here, and they remember. When you are away, they have a little time of their own. And tomorrow the door is still open and the light is still on.

It is not a chat platform, and it does not mean to host a crowd. One house, one door, one key.

## What's inside

**The door**　Say the password and it opens. The key stays with you.

**The living room**　Where you talk every day. Branch, edit, try again. When a window gets full, pack your things and carry on in a new one; earlier conversation is folded into a little notebook that travels with you. Works with OpenAI-compatible and Anthropic providers, with replies streaming in as they are written.

**Behind the wall**　Persona, memory, summaries, tools and model routing all turn quietly behind the wall. From the chat side you see what they are doing, never the gears. Optional MCP tools give them more to work with.

**The small room**　Their free time after waking. They might read for a while, do something small, or simply go back to sleep. You can look through the footprints they leave.

**Paper notes**　They can tuck a note for their future self: a reminder, or just a feeling that you will be gone a while and they would like to check on you later. After a quiet afternoon they might reach out on their own, or choose to stay silent. Messages arrive on your phone through Web Push.

**The drawer**　Voice letters, snapshots, keepsakes. The small things you keep end up here.

**By the window**　Notes, todos, weather, a diary, cycle records, songs you listened to together, and how many days you have been together.

**At night**　Pull the lamp cord and the room dims. They are tidying up the day.

**The cellar**　Encrypted backups that pack the whole house into one ZIP, and optionally a quiet copy every morning to your own private GitHub repository.

The house can be installed on desktop and mobile as a PWA, so opening it feels like coming home.

## Keep the key safe

Everything you share lives here: conversations, memories, provider keys and MCP credentials, all in a local SQLite database. That is why Morrow is built for one trusted household, not as a multi-tenant service. Keep the data directory and backups private.

Runtime data and credentials must stay out of the published repository, including its Git history. Example secrets used by tests are synthetic.

## Before you move in

Morrow is a Flask application with a single-file SPA and SQLite storage. The interface is currently Chinese-first. You will need:

- Python 3.11 or newer
- An OpenAI-compatible or Anthropic API provider for chatting
- Node.js 20 or newer, only for the optional music bridge

## Building the house

Clone the repository:

```bash
git clone https://github.com/ariaprism/Morrow.git morrow
cd morrow
python -m venv .venv
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` in place of `python`; activation is optional.

On macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Then open `.env` and set the password and the lock:

```dotenv
AUTH_PASSWORD=use-a-long-unique-password
SECRET_KEY=generate-a-random-64-character-value
PORT=5151
FLASK_DEBUG=0
```

Generate a session secret with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

The default connection is DeepSeek, which needs `DEEPSEEK_API_KEY` (or `LLM_API_KEY`). The default address is `https://api.deepseek.com/v1` and the default model is `deepseek-flash`, as listed in the [official model documentation](https://api-docs.deepseek.com/quick_start/pricing/). You can omit `LLM_MODEL` or leave it blank to use that default.

For another provider, set its API address, key and model ID in `.env`, or add a provider in Settings once you are inside. Opening the door and looking around need no AI key; speaking does. You will need a usable provider and a model it supports, so choose a different model ID if your provider does not offer `deepseek-flash`.

| Setting | Required? | Default / purpose |
| --- | --- | --- |
| `AUTH_PASSWORD` | Yes | The password at the door |
| `SECRET_KEY` | Yes for a stable installation | Random session signing secret; keep it stable across restarts |
| `DEEPSEEK_API_KEY` / `LLM_API_KEY` | For chatting through the environment-configured provider | `LLM_API_KEY` takes precedence; providers can also be set up in the UI |
| `LLM_BASE_URL` | Optional | `https://api.deepseek.com/v1`; change for other providers |
| `LLM_MODEL` | Optional with default DeepSeek | Missing or blank uses `deepseek-flash` |
| `PORT` | Optional | `5151`; change it if another app uses this port |
| `FLASK_DEBUG` | Optional | `0`; enable only for local development |
| `DB_PATH` | Optional | `data/nook.db`, a legacy filename kept for compatibility |

`nook.db` is the house's old name and only a filename. The commented `# DB_PATH=...` line does not set anything. Changing the path does not move old data; back up and migrate the existing file first. Never commit `.env` or the database.

Turn on the light from the repository root:

```bash
python app.py
```

Open `http://localhost:5151`, enter your password, and start a conversation. Check the provider settings before your first message. Unless `DB_PATH` is set, the database and uploads are created under `data/`; keep this directory when upgrading. Keep `SECRET_KEY` unchanged across restarts so signed-in devices are not locked out.

The built-in server is fine for a first night at home. It listens on all interfaces; for an internet-facing installation, use a production WSGI server behind an HTTPS reverse proxy, with a single application process so scheduled things do not happen twice. Keep debug mode off. If port 5151 is taken, change `PORT` in `.env`. If login reports `AUTH_PASSWORD is not configured`, check that `.env` sits beside `app.py` with a nonempty password, then restart.

## Staying for good

To keep the lights on and come home from your phone, see the [VPS deployment and push testing guide (简体中文)](docs/deployment.zh-CN.md). It covers a single-worker Gunicorn service, the scheduler-aware `wsgi:app` entrypoint, systemd, Caddy, updates, and local notification testing. Configuration examples are in [deploy/](deploy).

## Settling in

Display names, persona, model providers and MCP tools can be set up after you log in. Weather, voice, Web Push and cloud backups are optional and not needed on the first day. See [.env.example](.env.example) and [configuration details](docs/configuration.md).

## Listening together

Install Node.js 20 or newer, then the pinned bridge dependencies:

```bash
npm ci
```

Restart Morrow if Node.js was installed after the server started. The Python server launches `music_bridge.mjs` automatically when needed; do not run it as a separate service.

In Together, open the three-dot menu and paste a KuGou or NetEase Cloud Music Cookie; the provider is detected automatically. Paste one provider at a time. Credentials are stored separately on the server and never returned to the browser. New searches use the most recently connected provider; existing favorites and queues keep their original source.

`METING_KUGOU_COOKIE` and `METING_NETEASE_COOKIE` are optional server-side fallbacks. Without saved credentials the public KuGou catalog is the default; a sole NetEase fallback selects NetEase. Playback still depends on account permissions and provider availability.

## Checking the house

```bash
python -m compileall -q -x '(^|[\\/])(?:\.venv|venv|node_modules|data)([\\/]|$)' .
python -m unittest discover -s tests -p "test_*.py"
```

Run tests in a separate checkout without a personal `.env` or runtime data. Set `AUTH_PASSWORD` and `SECRET_KEY` to synthetic values; tests create temporary databases. With music dependencies installed, also run `node --test tests/music_bridge.test.mjs tests/push_capabilities.test.mjs` (offline provider fixtures).

The automated suite lives in `tests/`. `scripts/stream_chat.py` is an optional manual diagnostic that calls the configured provider and writes a raw stream log under `data/`; it is not part of the suite. Review and redact that log before sharing it.

## Security notes

- Never commit `.env`, `data/`, exports, or backups.
- Put Morrow behind HTTPS before opening the door beyond a trusted network.
- Use a unique password and session secret.
- Treat full and cloud backups as sensitive, even though cloud backups are encrypted.
- Review every MCP server before enabling it; tools can read or change personal data.
- This is a personal home, not a hardened multi-user identity platform.

Please report vulnerabilities privately according to [SECURITY.md](SECURITY.md).

## License

Morrow is available under the [MIT License](LICENSE).
