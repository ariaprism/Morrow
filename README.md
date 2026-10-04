# Nook

Nook is a private, self-hosted home for long-running AI conversations. It keeps the chat surface calm while memory, tools, model routing, summaries, scheduled activities, backups, and usage records stay behind the wall.

The interface is currently Chinese-first. The codebase is a Flask application with a single-file SPA and SQLite storage.

## What is included

- Streaming chat with OpenAI-compatible and Anthropic providers
- Multiple conversations, branching, continuation windows, message versions, editing, regeneration, and export
- Persona and memory management, summaries, image descriptions, and optional MCP tools
- Notes, todos, weather, diary, keepsakes, listening history, cycle records, and voice letters
- Scheduled room activity, proactive messages, one-shot reminders, and Web Push
- Encrypted ZIP and optional GitHub Release backups
- PWA support for desktop and mobile installation

## Privacy model

Nook is designed for one trusted household, not as a multi-tenant service. Conversation data, provider keys, MCP credentials, and personal records are stored in the local SQLite database. Keep the data directory and backups private.

The repository contains no production database, uploads, credentials, deployment hostnames, or private Git history. Example secrets used by tests are synthetic.

## Requirements

- Python 3.11 or newer
- Node.js 20 or newer only for the optional music bridge
- An OpenAI-compatible or Anthropic API provider

## Quick start

```bash
git clone <your-repository-url> nook
cd nook
python -m venv .venv
```

Activate the virtual environment, then install dependencies:

```bash
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell, use `Copy-Item .env.example .env` instead of `cp`.

Set at least these values in `.env`:

```dotenv
AUTH_PASSWORD=use-a-long-unique-password
SECRET_KEY=generate-a-random-64-character-value
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=your-model-id
LLM_API_KEY=your-api-key
```

Generate a session secret with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Start Nook from the repository root:

```bash
python app.py
```

Open `http://localhost:5050`. The database and uploads are created under `data/` unless `DB_PATH` is set.

## Optional configuration

The checked-in `.env.example` documents weather, encrypted GitHub backups, Web Push identity, and model settings. Provider and MCP credentials can also be managed from the settings screen after login.

For the optional music bridge:

```bash
npm install
node music_bridge.mjs
```

## Testing

```bash
python -m unittest discover -p "test_*.py"
```

Tests use temporary databases. Set `AUTH_PASSWORD` and `SECRET_KEY` in the environment when running them outside the included CI workflow.

## Security notes

- Never commit `.env`, `data/`, exports, or backups.
- Put Nook behind HTTPS before exposing it outside a trusted network.
- Use a unique login password and session secret.
- Treat full and cloud backups as sensitive even though cloud backup assets are encrypted.
- Review every MCP server before enabling it; tools can read or change personal data.
- This is a personal application, not a hardened multi-user identity platform.

Please report vulnerabilities according to [SECURITY.md](SECURITY.md).

## License

Nook is available under the [MIT License](LICENSE).

---

Nook 是一个可自托管的私人 AI 对话空间。界面目前以中文为主，聊天之外的记忆、工具、整理、生活记录与备份都安静地留在后台。首次运行前请复制 `.env.example`，设置独立的访问密码、Session 密钥和模型供应商；不要把数据库、上传文件或备份提交到公开仓库。
