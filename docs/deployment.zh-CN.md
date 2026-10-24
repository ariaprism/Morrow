# VPS 部署与推送验证

[返回中文版 README](../README.zh-CN.md) · [English README](../README.md)

本指南使用 Ubuntu 24.04 LTS、Python 虚拟环境、Gunicorn、systemd 和 Caddy，将 Morrow 部署到独立域名。Debian 等系统也可以采用相同结构，但请确认 Python 至少为 3.11。

这是部署步骤和配置示例；目前已在 Windows 的隔离环境验证应用入口及自动测试，尚未在真实 VPS 上完整执行本文命令。

## 1. 部署后的结构

```text
浏览器 / 手机 PWA
        │ HTTPS（443）
        ▼
      Caddy
        │ HTTP，仅本机
        ▼
127.0.0.1:5151 — Gunicorn（单 worker，多线程）
        │
        ├─ Flask 应用
        ├─ 后台调度：主动消息、提醒、整理及备份
        └─ /opt/morrow/data/：数据库和上传文件
```

本地运行 `python app.py` 适合开发和首次试用。VPS 使用生产 WSGI 服务器常驻运行，由 Caddy 负责 HTTPS，systemd 负责开机启动和异常重启。

**使用 `wsgi:app`，保持一个 worker，不要使用 `--preload` 或 `--reload`。** `wsgi.py` 会在 Gunicorn worker 内启动后台调度；直接使用 `app:app` 只导入应用，不会运行 `python app.py` 的启动分支。多个 worker 或同时运行两份应用可能重复执行定时任务。需要更多并发时，先调整线程数。

## 2. 准备服务器、域名和网络

你需要：

- 一台可通过 SSH 登录、具有 sudo 权限的 Linux VPS。
- 一个域名或子域名，本文用 `morrow.example.com` 占位，请替换成你的真实域名。
- 可用的模型 API 账号；也可以先部署，登录后再配置。
- 服务端和客户端均能访问对应浏览器的推送服务，才能验证 Web Push。

在域名 DNS 中添加指向 VPS 公网 IPv4 的 A 记录。只有服务器确实配置了 IPv6 时才添加 AAAA 记录；错误的 AAAA 记录可能导致访问或证书申请失败。

在云服务商安全组中允许 TCP 80、443，以及你的 SSH 端口。**不要把 5151 暴露到公网**，它只供 Caddy 在本机访问。确认服务器系统时间同步正常；定时活动依赖服务器时区：

```bash
timedatectl
```

如果希望按中国时间执行定时任务，可以设置为：

```bash
sudo timedatectl set-timezone Asia/Shanghai
```

已有其他服务的服务器，请先确认它们对时区的要求。

## 3. 安装基础依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl ca-certificates
python3 --version
```

音乐功能还需要 Node.js 20 或更新版本及 npm。请通过 [Node.js 官方下载说明](https://nodejs.org/en/download) 安装，随后确认：

```bash
node --version
npm --version
```

不使用音乐功能时，可以跳过 Node.js 和后面的 `npm ci`。

## 4. 创建专用运行用户并取得源码

以下命令假定 `morrow` 用户和 `/opt/morrow` 还不存在。若已存在，请先检查，避免混用其他服务的目录。

```bash
sudo useradd --system --user-group --create-home --home-dir /var/lib/morrow --shell /usr/sbin/nologin morrow
sudo install -d -o morrow -g morrow -m 750 /opt/morrow
sudo -u morrow git clone https://github.com/ariaprism/Morrow.git /opt/morrow
sudo -u morrow python3 -m venv /opt/morrow/.venv
sudo -u morrow /opt/morrow/.venv/bin/python -m pip install -r /opt/morrow/requirements.txt
sudo -u morrow /opt/morrow/.venv/bin/python -m pip install gunicorn
```

以上命令使用 Morrow 的公开仓库地址。Gunicorn 为 Linux 部署单独安装的依赖，不用于 Windows 本地运行。运行用户不需要 root 权限。

需要音乐功能时：

```bash
cd /opt/morrow
sudo -u morrow npm ci --ignore-scripts --no-audit --no-fund
```

如果 Node.js 只安装在个人用户的 nvm 环境里，systemd 下的 `morrow` 用户通常找不到它。请使用可供该用户访问的安装位置，或在 `.env` 中将 `NODE_BIN` 设置为实际可执行文件的绝对路径。可先检查：

```bash
sudo -u morrow node --version
```

## 5. 填写私有配置

```bash
sudo -u morrow cp /opt/morrow/.env.example /opt/morrow/.env
sudo chmod 600 /opt/morrow/.env
sudoedit /opt/morrow/.env
```

至少替换访问密码和 Session 密钥。示例中的占位文字不能原样作为真实凭据：

```dotenv
AUTH_PASSWORD=replace-with-a-long-unique-password
SECRET_KEY=replace-with-a-generated-random-secret
PORT=5151
FLASK_DEBUG=0
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-flash
DEEPSEEK_API_KEY=replace-with-your-api-key
VAPID_SUBJECT=mailto:you@example.com
GITHUB_BACKUP_AUTO_ALLOWED=0
```

使用命令生成 `SECRET_KEY`：

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

保留这个密钥，重启或更新时不要重新生成。`VAPID_SUBJECT` 请替换为真实的管理联系邮箱；推送签名密钥由 Morrow 自动生成并保存在数据库中。

模型密钥可以稍后在界面中配置。不填写 `LLM_MODEL` 或留空时使用 `deepseek-flash`；其他供应商需要填写或选择它实际支持的模型。

`data/nook.db` 是兼容旧版本的默认数据库路径，不影响应用名称。不要仅为了改名而修改已有安装的数据库路径。`.env` 会由应用从项目目录读取，本文的 systemd 配置不需要额外的 `EnvironmentFile`。

`GITHUB_BACKUP_AUTO_ALLOWED` 先保持 `0`，确认私有备份仓库、权限和加密口令配置正确后，再决定是否让这台服务器承担自动备份。

## 6. 设置常驻服务

仓库附带可直接审阅的 [systemd 示例](../deploy/morrow.service)。它使用固定的 `/opt/morrow` 路径和 `morrow` 用户；若你采用其他路径或用户，需要同步修改。

```bash
sudo install -m 644 /opt/morrow/deploy/morrow.service /etc/systemd/system/morrow.service
sudo systemctl daemon-reload
sudo systemctl enable --now morrow
sudo systemctl status morrow --no-pager
```

验证后端已经在本机运行：

```bash
curl -I http://127.0.0.1:5151/
sudo journalctl -u morrow -n 80 --no-pager
```

首页应返回 HTTP 200。未登录时请求 `/api/version` 返回 401 是正常的权限保护。若看到错误，先修复后端，再继续配置反向代理。

systemd 中 Gunicorn 的 `--bind 127.0.0.1:5151` 决定生产监听端口；单独修改 `.env` 的 `PORT` 不会覆盖该参数。要换端口，需要同时修改服务文件和 Caddy 的上游地址，再执行 `daemon-reload` 并重启应用。

常用操作：

```bash
sudo systemctl restart morrow
sudo systemctl stop morrow
sudo journalctl -u morrow -f
```

本文不设置自动访问日志，以减少把查询参数写入日志的机会。排障日志也可能包含个人内容或供应商信息，分享前请检查。

## 7. 配置域名与 HTTPS

通过 [Caddy 官方安装文档](https://caddyserver.com/docs/install#debian-ubuntu-raspbian) 的 Debian / Ubuntu 稳定仓库步骤安装 Caddy。安装后确认：

```bash
caddy version
sudo systemctl status caddy --no-pager
```

新服务器可以参考 [Caddy 配置示例](../deploy/Caddyfile.example) 编辑 `/etc/caddy/Caddyfile`。如果已有其他站点，将下面这一段合并到原文件，不要覆盖现有配置：

```caddyfile
morrow.example.com {
    reverse_proxy 127.0.0.1:5151 {
        flush_interval -1
    }
}
```

替换域名后检查并加载：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
sudo journalctl -u caddy -n 80 --no-pager
```

DNS 指向正确且 80/443 可达时，Caddy 会自动申请并维护 HTTPS 证书。这里仅反向代理应用，不把项目根目录配置为静态文件目录，避免公开 `.env` 等文件。

聊天使用 SSE 流式输出。示例关闭响应缓冲；如果你使用 Nginx 或其他代理，也需要保留流式响应，并检查读取超时。详见 [Caddy 反向代理文档](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)。

打开 `https://你的域名`，确认没有证书警告，然后登录。正式访问不需要在域名后加 `:5151`。

如果服务器使用 UFW，先确认已放行实际 SSH 端口，再按需要放行 80/443。云安全组和系统防火墙都需要允许访问；不要直接照抄可能锁住自己 SSH 的防火墙命令。

## 8. 上线后的验证

依次确认：

1. HTTPS 首页、登录和 `v0.1.0` 版本显示正常。
2. 新建对话，检查供应商，发送一条测试消息；正文应逐步出现，而非最后一次性显示。
3. 上传一张测试图片，确认资源能正常读取。
4. 重启 `morrow` 服务，确认已有对话仍在，Session 密钥未变时登录仍有效。
5. 启用需要的定时功能，使用短时、可控的提醒验证；后台调度约每 30 秒检查一次，不保证精确到秒。
6. 按下一节订阅并测试通知；音乐、天气和云备份按需另行验证。

## 9. Web Push 推送与权限

推送需要四个条件：支持 Web Push 的浏览器、安全访问环境、通知授权，以及能访问浏览器推送服务的网络。程序会按浏览器能力判断，不再只允许 Android。

- 电脑本机可用 `http://localhost:5151` 或回环地址测试。
- 手机访问电脑的普通局域网 HTTP 地址不具备同样的安全环境，需要可信 HTTPS。
- iPhone / iPad 需要支持该功能的系统版本，并先将网页添加到主屏幕，再从主屏幕打开后授权。参考 [WebKit 官方说明](https://webkit.org/blog/13878/)。

打开设置里的推送区域，先打开推送开关，在浏览器提示中选择允许，再点击「测试通知」。订阅按设备和站点来源分别保存；localhost 的授权不会自动变成正式域名的授权。

如果没有通知：

1. 看开关下方的提示。若提示需要 HTTPS，检查地址栏是否真的使用 localhost 或可信 HTTPS；手机上的 localhost 指手机自身。
2. 查看浏览器地址栏的网站设置，允许该站点的通知。若以前点过禁止，需要手动恢复权限。
3. 检查系统通知设置是否允许该浏览器通知，关闭勿扰/专注模式，并查看通知中心。有些系统不会每次都显示横幅。
4. 如果显示订阅超时，检查客户端能否连接浏览器的推送服务；不同浏览器可能使用 Google、Mozilla 或 Apple 的服务。
5. 如果测试请求失败，检查 `journalctl -u morrow`，并在登录状态下查看 `/api/push/status` 的 `available`、`subscription_count`、`last_delivery`。不要公开分享完整订阅信息。
6. 「已交给浏览器推送服务」表示服务端提交被接受，不等于设备已经显示通知。仍需用目标手机实际验证锁屏和后台接收。

测试通知会主动请求显示；普通消息在你正打开并聚焦 Morrow 时可能被抑制，这是避免重复提醒的设计。

电脑浏览器完全退出、被系统强制停止或手机省电策略限制时，接收效果取决于系统与浏览器。推送依赖网络，不保证即时送达。VPS 只要持续运行，就能继续执行主动消息和提醒；本地电脑关机后则无法生成新的后台消息。

`VAPID_SUBJECT` 是联系身份，不是需要注册的推送账号。签名密钥、订阅和发送记录在数据库中；迁移服务器时保留数据库。换域名后，应在新域名重新授权订阅。

## 10. 不先部署 VPS：临时 HTTPS 隧道测试

如果只想用手机验证通知，可以让本地 Morrow 继续运行，再使用一个临时 HTTPS 隧道，不必先购买或配置 VPS。

以 Cloudflare Quick Tunnel 为例：按 [官方下载说明](https://developers.cloudflare.com/tunnel/downloads/) 安装 `cloudflared`。Windows 可使用官方 MSI，安装后重新打开终端，确认 `cloudflared --version` 能运行。

先将本地 `.env` 的 `FLASK_DEBUG` 设为 `0`，设置独立的访问密码和稳定的 Session 密钥，然后重启 Morrow。临时隧道会把本地应用暴露到公网，知道地址的人能访问登录页，不要分享地址或凭据。

保持 Morrow 在一个终端运行，在另一个终端执行：

```powershell
cloudflared tunnel --url http://localhost:5151
```

终端会给出一个临时的 `https://...trycloudflare.com` 地址。用手机打开它、登录、按设备要求安装或授权，再启用推送并点击测试。测试时需要本地程序、隧道进程和网络都保持运行。结束后在隧道终端按 `Ctrl+C`。

注意：[Quick Tunnel 官方限制](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)明确说明不支持 SSE，且地址会变化，因此这里只用于通知等非流式接口的短期验证，不用它判断聊天流式功能是否正常。完整聊天测试应使用支持 SSE、具有稳定域名的隧道或正式 VPS。

隧道地址改变后属于新站点，需重新登录并授权通知。临时隧道不提供生产可用性保证，也不能让关机的电脑持续运行后台任务。

## 11. 更新、备份与迁移

更新前先在界面导出加密完整备份，并确认离线保存了解密口令。若计划迁移，也要单独安全保存 `.env`，它包含运行凭据。不要把这些文件上传到公开代码仓库。

暂停服务后更新源码：

```bash
sudo systemctl stop morrow
cd /opt/morrow
sudo -u morrow git pull --ff-only
sudo -u morrow /opt/morrow/.venv/bin/python -m pip install -r requirements.txt
sudo -u morrow /opt/morrow/.venv/bin/python -m pip install gunicorn
```

使用音乐功能时，再执行：

```bash
sudo -u morrow npm ci --ignore-scripts --no-audit --no-fund
```

确认命令成功后启动并检查：

```bash
sudo systemctl start morrow
sudo systemctl status morrow --no-pager
sudo journalctl -u morrow -n 80 --no-pager
```

更新失败时先修复问题，不要删除 `data/` 重新开始。源码回退不等于数据库回退；如果新版迁移了数据结构，回滚前需要核对兼容性，必要时恢复更新前的备份。

迁移时应停止旧实例，再迁移 `.env`、数据库及上传文件，保持文件归属为运行用户，最后启动新实例。不要让两台正式实例同时对同一份数据执行后台任务。域名保持不变且保留数据库中的签名密钥时，订阅通常可以沿用；最终仍需在目标设备测试。

## 12. 常见故障

| 现象 | 优先检查 |
| --- | --- |
| 502 Bad Gateway | `systemctl status morrow`、Gunicorn 日志、Caddy 上游端口 |
| 域名打不开或证书申请失败 | A/AAAA 记录、80/443 安全组和防火墙、Caddy 日志 |
| 聊天一直等待或最后才显示 | 供应商响应、SSE 缓冲、代理超时，以及中间隧道是否支持 SSE |
| 登录一直失效 | 稳定的 `SECRET_KEY`、是否启动了其他实例、同域名其他应用的 Cookie 冲突 |
| 聊天正常，主动消息/提醒不执行 | 是否使用 `wsgi:app`、是否单 worker、相关功能开关与服务器时区 |
| 找不到 Node.js 或音乐不可用 | 运行用户的 PATH、`NODE_BIN`、是否在正确目录执行 `npm ci` |
| 通知开关不可用 | HTTPS/localhost、浏览器能力、iOS 是否从主屏幕打开 |
| 请求显示已发送但没有通知 | 系统权限、通知中心、勿扰模式、浏览器推送网络 |
| 更新后看起来没有旧数据 | `WorkingDirectory`、`DB_PATH`、文件权限，先检查路径而非创建新数据库 |

## 参考资料

- [Flask：Gunicorn 部署](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/)
- [Caddy 安装](https://caddyserver.com/docs/install)
- [Caddy 反向代理与流式响应](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
- [MDN：安全环境](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts)
- [WebKit：iOS / iPadOS Web Push](https://webkit.org/blog/13878/)
- [Cloudflare：Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
