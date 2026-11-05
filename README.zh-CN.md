# Morrow

[English](README.md) | **简体中文**

明天见，也在今天见。

A small home where your AI can stay

你们在这里说话，ta 会记得；你离开的时候，ta 也有一段属于自己的时间；等到明天，门还开着，灯还亮着。

它不是对话平台，也不打算招待很多人。一栋房子，一扇门，一把钥匙。

## 房子里有什么

**门口**　输入暗号，门会开。钥匙只在你手里。

**客厅**　每天说话的地方。可以分支、改写、重来；旧窗口装满了，就带上行李去新窗口接着聊，前面的事会折进小本本，跟着一起走。OpenAI 兼容接口和 Anthropic 接口都能接，回答一个字一个字地流出来。

**墙后面**　人设、记忆、摘要、工具、模型路由，都在墙后面安静地转。聊天这一面只看得到 ta 在做什么，看不到齿轮。想给 ta 更多能力，可以接上 MCP 工具。

**小房间**　ta 醒来以后的自由时间。读一会儿，做点小事，或者什么都不做，回去睡觉。留下的足迹你可以翻看。

**纸条**　ta 可以给未来的自己夹一张纸条：提醒一件事，或者只是觉得你会离开很久，想过一会儿再来问问你。白天安静久了，ta 也可能主动来找你，也可能选择不说话。消息会通过 Web Push 落到你的手机上。

**抽屉**　声音信、留影、纪念物，收好的小东西都放在这里。

**窗边**　便签、待办、天气、日记、周期记录、一起听过的歌，还有在一起的第几天。

**夜里**　拉一下灯绳，房间暗下来。ta 在整理今天。

**地下室**　加密备份，整栋房子打包成一个 ZIP；也可以每天清晨悄悄存一份到你自己的 GitHub 私有仓库。

房子可以装到电脑和手机的桌面上（PWA），打开就是家。

## 钥匙要收好

这栋房子里放的是你们全部的对话、记忆、供应商密钥和 MCP 凭据，都存在本地的 SQLite 数据库里。所以它只为一户人家设计，不是多人共用的服务。请把数据目录和备份当作很私密的东西来保管。

运行数据和凭据不应该出现在公开仓库里，也不应该留在 Git 历史中。测试里出现的示例密钥都是虚构的。

## 搬进来之前

Morrow 用 Flask、一个单文件页面和 SQLite 搭成，界面以中文为主。你需要准备：

- Python 3.11 或更新版本
- 一个能用的 OpenAI 兼容或 Anthropic 接口，用来聊天
- Node.js 20 或更新版本（只有一起听歌需要，可以先不装）

## 搭起来

先把房子拿回家：

```bash
git clone https://github.com/ariaprism/Morrow.git morrow
cd morrow
python -m venv .venv
```

Windows PowerShell 下：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

如果 PowerShell 拦住了激活脚本，可以跳过激活，把后面命令里的 `python` 换成 `.\.venv\Scripts\python.exe`。

macOS / Linux 下：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

然后打开 `.env`，配好门的暗号和锁芯：

```dotenv
AUTH_PASSWORD=use-a-long-unique-password
SECRET_KEY=generate-a-random-64-character-value
PORT=5151
FLASK_DEBUG=0
```

锁芯（`SECRET_KEY`）可以这样生成：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

默认连接的是 DeepSeek，需要填 `DEEPSEEK_API_KEY` 或 `LLM_API_KEY`。默认地址是 `https://api.deepseek.com/v1`，默认模型是 `deepseek-flash`，模型名可以在 [DeepSeek 官方文档](https://api-docs.deepseek.com/quick_start/pricing/)查到。`LLM_MODEL` 不填或留空，就用默认模型。

想用别家的模型，可以在 `.env` 里改接口地址、密钥和模型 ID，也可以进门以后在设置里添加供应商。只是开门、进屋、四处看看，不需要任何 AI 密钥；要开口说话时，才需要一个能用的供应商和它支持的模型。如果那家不支持 `deepseek-flash`，聊天前记得换成它实际有的模型。

| 配置项 | 是否必填 | 默认值或用途 |
| --- | --- | --- |
| `AUTH_PASSWORD` | 是 | 进门的暗号 |
| `SECRET_KEY` | 长期住下时必填 | 随机生成的 Session 签名密钥，重启时保持不变 |
| `DEEPSEEK_API_KEY` / `LLM_API_KEY` | 用环境里的供应商聊天时需要 | 优先使用 `LLM_API_KEY`；也可以在界面里配置 |
| `LLM_BASE_URL` | 可选 | `https://api.deepseek.com/v1`，换供应商时修改 |
| `LLM_MODEL` | 用默认 DeepSeek 时可选 | 不填或留空时使用 `deepseek-flash` |
| `PORT` | 可选 | `5151`，被占用了就换一个 |
| `FLASK_DEBUG` | 可选 | `0`，只在本地开发时打开 |
| `DB_PATH` | 可选 | `data/nook.db`，为兼容旧版本保留的文件名 |

`nook.db` 是这栋房子以前的名字，只是文件名，不影响应用名称。带 `#` 的 `# DB_PATH=...` 是注释，不会生效。改路径不会自动搬家，旧数据需要先备份再手动挪过去。`.env` 和数据库都不要提交。

在项目根目录点灯：

```bash
python app.py
```

打开 `http://localhost:5151`，输入暗号，新建一个对话。第一次开口前，先看一眼供应商配置。没设置 `DB_PATH` 的话，数据库和上传目录会自动建在 `data/` 下面，升级时请留着它。重启时保持 `SECRET_KEY` 不变，已经登录的设备才不会被关在门外。

内置服务器适合第一次在本地试住，默认监听所有网络接口。要长期住在公网上，请换成生产 WSGI 服务器加 HTTPS 反向代理，并且只开一个应用进程，否则定时的事情会做两遍。不要打开调试模式。如果 5151 被占用，改 `.env` 里的 `PORT`；如果登录提示 `AUTH_PASSWORD is not configured`，确认 `.env` 和 `app.py` 在同一层、暗号不为空，然后重启。

## 搬到服务器上长住

想让房子一直亮着、用手机也能回家，可以参考 [VPS 部署与推送验证指南](docs/deployment.zh-CN.md)：Gunicorn 单进程、后台调度、systemd 常驻、Caddy HTTPS、更新与备份，还有本地通知权限和临时隧道测试。[deploy/](deploy) 里有可以对照的配置示例。

## 慢慢布置

进门以后可以设置显示名称、人设、模型供应商和 MCP 工具。天气、语音、Web Push 和云备份都是可选的，不影响第一次开门。配置项见 [.env.example](.env.example)，更多界面偏好见[配置详情（英文）](docs/configuration.md)。

## 一起听歌

装好 Node.js 20 或更新版本以后，安装锁定版本的音乐依赖：

```bash
npm ci
```

如果是 Morrow 已经在运行后才装的 Node.js，重启一下。Python 服务会在需要时自己叫起 `music_bridge.mjs`，不用单独运行它。

在 Together（一起听）右上角的三点菜单里，粘贴酷狗或网易云音乐的 Cookie，程序会自己认出是哪家。一次只粘贴一家。两家的凭据分别存在服务端，不会通过接口回到浏览器。新的搜索用最近连接的那家，已经收藏的和播放队列里的歌，还按各自原来的来源播放。

也可以用服务端环境变量 `METING_KUGOU_COOKIE`、`METING_NETEASE_COOKIE` 作为备用。没有保存任何凭据时，默认用酷狗公开曲库；只配了网易云备用凭据时，就用网易云。能不能播出来，还要看账号权限和平台音源。

## 检查房子

```bash
python -m compileall -q -x '(^|[\\/])(?:\.venv|venv|node_modules|data)([\\/]|$)' .
python -m unittest discover -s tests -p "test_*.py"
```

请在一份单独的项目副本里跑测试，不要带上自己的 `.env` 或运行数据。给 `AUTH_PASSWORD` 和 `SECRET_KEY` 设置虚构的测试值；测试会自己建临时数据库。装了音乐依赖的话，还可以运行 `node --test tests/music_bridge.test.mjs tests/push_capabilities.test.mjs`，用离线模拟数据检查音乐连接。

自动测试都在 `tests/` 里。`scripts/stream_chat.py` 是手动诊断用的，会真实调用配置好的模型供应商，并把原始流式日志写进 `data/`，不属于自动测试。分享日志之前，请先把私人内容和敏感信息拿掉。

## 关于安全

- 不要提交 `.env`、`data/`、导出文件或备份。
- 在可信网络之外开门之前，先配好 HTTPS。
- 暗号和 Session 密钥都用独立的、别处没用过的。
- 完整备份和云备份都当作私密文件保管，即使云备份已经加密。
- 启用 MCP 服务器前，确认它会提供哪些工具、有什么权限；工具可能读取或修改你的个人数据。
- 这是一栋私人的房子，不是经过多用户安全加固的身份平台。

如果发现漏洞，请按 [SECURITY.md（英文）](SECURITY.md) 里的方式私下告诉我们。

## 许可证

Morrow 使用 [MIT 许可证](LICENSE)。
