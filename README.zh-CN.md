# Morrow

[English](README.md) | **简体中文**

Morrow 是一个可自托管的私人 AI 对话空间，适合让对话、记忆和日常慢慢积累。聊天界面保持安静，人设、工具、模型路由、摘要、定时活动、备份和用量记录都在后台各自运转。

界面目前以中文为主，项目基于 Flask、单文件 SPA 和 SQLite。

## 包含哪些功能

- 支持 OpenAI 兼容接口和 Anthropic 接口的流式聊天
- 多会话、对话分支、续聊窗口、消息版本、编辑、重新生成和导出
- 人设与记忆管理、摘要、图片描述，以及可选的 MCP 工具
- 便签、待办、天气、日记、纪念物、一起听歌、周期记录和声音信
- 定时活动、主动消息、一次性提醒和 Web Push 推送
- 加密 ZIP 备份，以及可选的 GitHub Release 云端备份
- 支持在电脑和手机上通过 PWA 安装

## 数据与隐私

Morrow 面向个人或彼此信任的家庭使用，不是多租户服务。对话、供应商密钥、MCP 凭据和个人记录保存在本地 SQLite 数据库中，请妥善保护数据目录及备份。

运行数据与凭据不应出现在公开仓库中，也不应留在 Git 历史里。测试中的示例密钥均为虚构数据。

## 环境要求

- Python 3.11 或更新版本
- Node.js 20 或更新版本，仅可选的音乐功能需要
- 可用的 OpenAI 兼容或 Anthropic API 供应商，用于聊天

## 快速开始

克隆仓库：

```bash
git clone https://github.com/ariaprism/Morrow.git morrow
cd morrow
python -m venv .venv
```

Windows PowerShell 下，激活虚拟环境并安装依赖：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

如果 PowerShell 阻止激活脚本，可以跳过激活，将后续命令中的 `python` 换成 `.\.venv\Scripts\python.exe`。

macOS / Linux 下：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

启动前，编辑 `.env`，设置访问密码和 Session 密钥：

```dotenv
AUTH_PASSWORD=use-a-long-unique-password
SECRET_KEY=generate-a-random-64-character-value
PORT=5151
FLASK_DEBUG=0
```

使用默认的 DeepSeek 连接聊天时，还需要填写 `DEEPSEEK_API_KEY` 或 `LLM_API_KEY`。默认接口地址为 `https://api.deepseek.com/v1`，默认模型为 `deepseek-flash`，模型名称见 [DeepSeek 官方文档](https://api-docs.deepseek.com/quick_start/pricing/)。`LLM_MODEL` 可以不设置或留空，此时会使用默认模型。

如果使用其他供应商，可以在 `.env` 中设置它的 API 地址、密钥和模型 ID，也可以登录后在设置中添加供应商。只启动、登录和浏览界面不需要 AI 密钥；发送消息前，需要配置可用的供应商、密钥及其支持的模型。如果供应商不支持 `deepseek-flash`，聊天前应选择它实际支持的模型。

| 配置项 | 是否必填 | 默认值或用途 |
| --- | --- | --- |
| `AUTH_PASSWORD` | 是 | 进入私人空间的密码 |
| `SECRET_KEY` | 稳定部署时必填 | 随机生成的 Session 签名密钥，重启时保持不变 |
| `DEEPSEEK_API_KEY` / `LLM_API_KEY` | 使用环境配置的供应商聊天时需要 | 优先使用 `LLM_API_KEY`；也可在界面中配置供应商 |
| `LLM_BASE_URL` | 可选 | `https://api.deepseek.com/v1`，使用其他供应商时修改 |
| `LLM_MODEL` | 使用默认 DeepSeek 时可选 | 不设置或留空时使用 `deepseek-flash` |
| `PORT` | 可选 | `5151`，端口被占用时可自行修改 |
| `FLASK_DEBUG` | 可选 | `0`，仅在本地开发时开启调试 |
| `DB_PATH` | 可选 | `data/nook.db`，为兼容旧版本保留的文件名 |

`nook.db` 只是数据库文件名，不影响应用名称。带 `#` 的 `# DB_PATH=...` 是注释，不会设置路径。修改路径不会自动迁移已有数据；如需修改，请先备份并迁移数据库文件。不要提交 `.env` 或数据库。

可以用下面的命令生成 Session 密钥，并将结果填写到 `SECRET_KEY`：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

在项目根目录启动：

```bash
python app.py
```

打开 `http://localhost:5151`，输入 `AUTH_PASSWORD` 对应的密码，然后新建对话。发送第一条消息前，检查供应商配置。未设置 `DB_PATH` 时，程序会自动在 `data/` 下创建数据库和上传目录；升级时请保留此目录。重启时保持 `SECRET_KEY` 不变，已有登录状态才能继续有效。

内置服务器适合本地首次运行，默认监听所有网络接口。部署到公网时，请使用生产 WSGI 服务器和 HTTPS 反向代理，并保持单个应用进程，避免重复执行定时任务。不要开启调试模式。如果 5151 端口被占用，修改 `.env` 中的 `PORT`。如果登录提示 `AUTH_PASSWORD is not configured`，请确认 `.env` 位于 `app.py` 同级目录，且密码不为空，然后重启。

## VPS 部署

需要长期运行和手机访问时，请参考详细的 [VPS 部署与推送验证指南](docs/deployment.zh-CN.md)，包含 Gunicorn 单进程启动、后台调度、systemd 常驻、Caddy HTTPS、更新备份，以及本地通知权限和临时隧道测试。[deploy/](deploy) 提供可审阅的配置示例。

## 可选配置

登录后可以配置显示名称、人设、模型供应商和 MCP 工具。天气、语音、Web Push 和云备份均为可选服务，不影响首次启动。配置项见 [.env.example](.env.example)，更多界面偏好说明见[配置详情（英文）](docs/configuration.md)。

## 可选的音乐连接

安装 Node.js 20 或更新版本后，安装锁定版本的音乐依赖：

```bash
npm ci
```

如果是在 Morrow 启动后才安装 Node.js，请重启 Morrow。Python 服务会在需要时自动调用 `music_bridge.mjs`，不需要将它单独作为服务运行。

在 Together（一起听）右上角的三点菜单中，粘贴酷狗或网易云音乐的 Cookie，程序会自动识别平台。每次只粘贴一个平台的 Cookie。两个平台的凭据分别保存在服务端，不会通过接口返回浏览器。新搜索使用最近连接的平台，已有收藏和播放队列中的歌曲仍按各自的来源播放。

也可以通过服务端环境变量 `METING_KUGOU_COOKIE`、`METING_NETEASE_COOKIE` 设置备用凭据。未保存凭据时，默认使用酷狗公开曲库；如果仅配置了网易云备用凭据，则使用网易云。能否播放仍取决于账号权限和平台音源是否可用。

## 测试

```bash
python -m compileall -q -x '(^|[\\/])(?:\.venv|venv|node_modules|data)([\\/]|$)' .
python -m unittest discover -s tests -p "test_*.py"
```

请在单独的项目副本中运行测试，不要放入个人 `.env` 或运行数据。通过环境变量为 `AUTH_PASSWORD` 和 `SECRET_KEY` 设置虚构的测试值；测试会创建临时数据库。安装音乐依赖后，还可以运行 `node --test tests/music_bridge.test.mjs tests/push_capabilities.test.mjs`，使用离线模拟数据验证音乐连接。

自动测试位于 `tests/`。`scripts/stream_chat.py` 是可选的手动诊断脚本，会调用配置的模型供应商，并将原始流式日志写入 `data/`，不属于自动测试。分享日志前，请先检查并移除个人内容和敏感信息。

## 安全说明

- 不要提交 `.env`、`data/`、导出文件或备份。
- 在可信网络之外提供访问前，请配置 HTTPS。
- 使用独立的访问密码和 Session 密钥。
- 完整备份和云备份都应按敏感文件保管，即使云备份内容已加密。
- 启用 MCP 服务器前，请确认它提供的工具和权限；工具可能读取或修改个人数据。
- 本项目是私人应用，不是经过多用户安全加固的身份平台。

如发现漏洞，请按照 [SECURITY.md（英文）](SECURITY.md) 中的方式私下报告。

## 许可证

Morrow 使用 [MIT 许可证](LICENSE)。
