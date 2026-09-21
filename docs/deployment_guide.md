# GitHub Actions 自动部署指南

## 1. 部署结构

Pull Request 会运行测试和静态检查，但不会发布。代码推送到 `main` 后，GitHub Actions 会先执行相同检查；全部通过后，Workflow 将不含密钥的源码归档上传到服务器，再由服务器完成以下操作：

1. 解压到 `/opt/googlecloudvoice/releases/<commit-sha>`；
2. 按 `backend/requirements.lock` 创建独立 Python 虚拟环境；
3. 原子切换 `/opt/googlecloudvoice/current` 软链接；
4. 重启 `googlecloudvoice` systemd 服务；
5. 健康检查失败时自动切回上一个版本。

应用密钥只保存在服务器的 `/opt/googlecloudvoice/shared/backend.env`，不会打包到 GitHub Actions 产物。

## 2. 服务器前置条件

以下示例适用于 Ubuntu/Debian，默认部署用户是 `deploy`，应用目录是 `/opt/googlecloudvoice`。如果使用其他账户或目录，需要同步修改 systemd 模板和 GitHub 变量。

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv nginx curl
id -u deploy >/dev/null 2>&1 || sudo useradd --create-home --shell /bin/bash deploy
sudo install -d -o deploy -g deploy /opt/googlecloudvoice/releases
sudo install -d -o deploy -g deploy -m 750 /opt/googlecloudvoice/shared
```

为部署用户创建专用 SSH 密钥，并将公钥写入服务器的 `/home/deploy/.ssh/authorized_keys`。私钥仅保存为 GitHub Environment Secret，不要提交到仓库。

部署脚本只需要管理本应用服务。使用 `sudo visudo -f /etc/sudoers.d/googlecloudvoice-deploy` 添加最小权限：

```sudoers
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart googlecloudvoice, /usr/bin/systemctl stop googlecloudvoice, /usr/bin/systemctl status googlecloudvoice
```

先用 `command -v systemctl` 确认服务器路径；如果不是 `/usr/bin/systemctl`，按实际路径修改 sudoers。

## 3. 配置应用密钥

在服务器创建 `/opt/googlecloudvoice/shared/backend.env`：

```dotenv
# Gemini 服务端密钥，不得写入前端或 GitHub Workflow。
GEMINI_API_KEY=替换为真实密钥

# HTTP 和 WebSocket API 的共享访问令牌。
API_ACCESS_TOKEN=替换为高强度随机令牌
```

设置文件权限：

```bash
sudo chown deploy:deploy /opt/googlecloudvoice/shared/backend.env
sudo chmod 600 /opt/googlecloudvoice/shared/backend.env
```

生产包不会携带 `backend/.env`，应用会直接读取 systemd 提供的环境变量。不要把 `API_ACCESS_TOKEN` 写入前端 HTML；前端页面中的令牌输入只存放于当前浏览器会话。

## 4. 安装 systemd 和 Nginx 配置

首次部署前，用服务器管理员账户把仓库中的两个模板上传到服务器。以下命令前两行在本地项目根目录执行，后续命令在服务器执行；如果 SSH 不是 `22` 端口，请为 `scp` 增加 `-P 端口`：

```bash
scp deploy/systemd/googlecloudvoice.service 管理员账户@服务器地址:/tmp/googlecloudvoice.service
scp deploy/nginx/googlecloudvoice.conf 管理员账户@服务器地址:/tmp/googlecloudvoice.conf

# 以下命令在服务器执行；安装前先修改域名、用户或目录。
sudo install -m 644 /tmp/googlecloudvoice.service /etc/systemd/system/googlecloudvoice.service
sudo install -m 644 /tmp/googlecloudvoice.conf /etc/nginx/sites-available/googlecloudvoice.conf
sudo ln -sfn /etc/nginx/sites-available/googlecloudvoice.conf /etc/nginx/sites-enabled/googlecloudvoice.conf
sudo systemctl daemon-reload
sudo systemctl enable googlecloudvoice
sudo nginx -t
sudo systemctl reload nginx
```

首次执行 Workflow 前，systemd 服务尚无 `current` 版本，暂时无法启动是正常现象。第一次发布会创建该软链接并启动服务。

模板默认是 HTTP。浏览器麦克风在公网环境需要安全上下文，因此生产环境必须为域名配置可信 TLS 证书（例如 Certbot），并只开放 HTTPS。服务器还需允许应用出站访问 Microsoft Edge TTS 和 Gemini 的 HTTPS 服务。

WebSocket 当前通过 URL 查询参数传递令牌，因此 Nginx 模板对 `/api/stt/stream` 关闭了访问日志，避免令牌进入日志文件。

## 5. 配置 GitHub Environment

在 GitHub 仓库进入 **Settings → Environments → New environment**，创建 `production`。可按需要设置 Required reviewers，阻止未经审批的生产发布。

在 `production` 环境中添加以下 Secrets：

| Secret | 说明 | 示例 |
| --- | --- | --- |
| `DEPLOY_HOST` | 服务器地址 | `voice.example.com` |
| `DEPLOY_PORT` | SSH 端口 | `22` |
| `DEPLOY_USER` | SSH 部署账户 | `deploy` |
| `DEPLOY_SSH_KEY` | 专用 SSH 私钥全文 | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `DEPLOY_KNOWN_HOSTS` | 预先核验的服务器主机公钥记录 | `voice.example.com ssh-ed25519 ...` |

建议从可信管理通道获取服务器公钥指纹并人工核验，然后将完整 `known_hosts` 记录保存为 Secret。不要在 Workflow 中临时执行未核验的 `ssh-keyscan`。

如果 SSH 使用非 `22` 端口，`DEPLOY_KNOWN_HOSTS` 中的主机名必须使用 `[域名或IP]:端口` 格式。

可先在本机获取待核验的记录（替换地址和端口）：

```bash
ssh-keyscan -t ed25519 -p 22 服务器地址
```

再通过服务器控制台核对真实指纹：

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

也可以把 `ssh-keyscan` 输出保存后执行 `ssh-keygen -lf 文件名`，两边指纹一致时，才将 `ssh-keyscan` 输出的完整一行保存到 `DEPLOY_KNOWN_HOSTS`。其中的主机名必须与 `DEPLOY_HOST` 完全一致：Workflow 使用 IP 连接就保存 IP 记录，使用域名连接就保存域名记录。

### 5.1 两类 SSH 密钥的区别

部署同时涉及服务器身份和登录用户身份，两者不可混用：

| 配置或文件 | 作用 |
| --- | --- |
| `/etc/ssh/ssh_host_ed25519_key.pub` | 服务器自身的身份公钥，与登录账号无关 |
| `DEPLOY_KNOWN_HOSTS` | GitHub 信任的服务器身份记录 |
| `DEPLOY_SSH_KEY` | GitHub Actions 作为客户端登录的私钥 |
| `/home/deploy/.ssh/authorized_keys` | 允许登录 `deploy` 用户的客户端公钥 |

SSH 首先用 `DEPLOY_KNOWN_HOSTS` 验证服务器，再用 `DEPLOY_SSH_KEY` 登录 `deploy` 用户。因此 `REMOTE HOST IDENTIFICATION HAS CHANGED` 属于服务器身份不匹配，`Permission denied (publickey)` 属于登录密钥或账号配置错误。

### 5.2 重新生成并配置 DEPLOY_KNOWN_HOSTS

以下命令在本机执行，地址和端口必须与 `DEPLOY_HOST`、`DEPLOY_PORT` 完全一致：

```bash
server_host="替换为服务器IP或域名"
ssh_port="22"
known_hosts_file="/tmp/googlecloudvoice_known_hosts"

ssh-keyscan -t ed25519 -p "$ssh_port" "$server_host" > "$known_hosts_file"
ssh-keygen -lf "$known_hosts_file" -E sha256
```

通过云厂商控制台登录服务器，不依赖待验证的 SSH 连接，核对真实指纹：

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

两边 SHA256 指纹完全一致后，将下面命令输出的完整一行替换到 GitHub `production` Environment 的 `DEPLOY_KNOWN_HOSTS` Secret：

```bash
cat "$known_hosts_file"
```

不要删除或重新生成服务器的 `/etc/ssh/ssh_host_ed25519_key`。如果服务器重装、IP 重新分配或主机密钥确实更换，必须通过可信控制台重新核验，而不是关闭 `StrictHostKeyChecking`。

### 5.3 排查 Permission denied (publickey)

在本机从 `DEPLOY_SSH_KEY` 对应的私钥导出公钥并查看指纹：

```bash
ssh-keygen -y \
  -f "$HOME/.ssh/googlecloudvoice_github_actions" \
  > /tmp/googlecloudvoice_github_actions.pub
ssh-keygen -lf /tmp/googlecloudvoice_github_actions.pub -E sha256
```

在服务器控制台检查 `deploy` 用户已授权公钥的指纹：

```bash
sudo ssh-keygen -lf /home/deploy/.ssh/authorized_keys -E sha256
```

两边必须存在相同指纹。如果不一致，把本机 `/tmp/googlecloudvoice_github_actions.pub` 的完整一行加入服务器的 `authorized_keys`，随后修复权限：

```bash
sudo chown deploy:deploy /home/deploy
sudo chmod go-w /home/deploy
sudo chown -R deploy:deploy /home/deploy/.ssh
sudo chmod 700 /home/deploy/.ssh
sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

使用与 Workflow 相同的严格校验在本机测试；成功结果必须输出 `deploy`：

```bash
ssh \
  -i "$HOME/.ssh/googlecloudvoice_github_actions" \
  -p "$ssh_port" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$known_hosts_file" \
  "deploy@$server_host" \
  "whoami"
```

最后确认 GitHub `DEPLOY_SSH_KEY` 保存的是无 `.pub` 后缀的完整私钥，`DEPLOY_USER` 是 `deploy`。私钥本机权限应为 `600`，服务器 `.ssh` 和 `authorized_keys` 权限分别为 `700`、`600`。

添加以下 Environment Variable：

| Variable | 说明 | 默认值 |
| --- | --- | --- |
| `DEPLOY_PATH` | 服务器部署根目录；仅支持不含空格的绝对路径 | `/opt/googlecloudvoice` |

## 6. 发布和回滚

推送到 `main` 会触发自动发布，也可以在仓库 **Actions → CI and Deploy → Run workflow** 手动执行；手动发布同样只允许选择 `main` 分支。

部署完成后检查：

```bash
sudo systemctl status googlecloudvoice
curl --fail http://127.0.0.1:8000/
readlink -f /opt/googlecloudvoice/current
```

### 6.1 手动重启并证明 8000 端口服务生效

systemd 模板中的 `ExecStart` 明确让 Uvicorn 监听 `127.0.0.1:8000`。可记录重启前后的主进程 PID，再同时验证 systemd 状态、监听端口和 HTTP 响应：

```bash
before_pid="$(systemctl show --property=MainPID --value googlecloudvoice)"

sudo systemctl restart googlecloudvoice

after_pid="$(systemctl show --property=MainPID --value googlecloudvoice)"
active_since="$(systemctl show --property=ActiveEnterTimestamp --value googlecloudvoice)"

printf 'before_pid=%s\nafter_pid=%s\nactive_since=%s\n' \
  "$before_pid" "$after_pid" "$active_since"

systemctl is-active googlecloudvoice
sudo ss -ltnp 'sport = :8000'
curl --fail --silent --show-error http://127.0.0.1:8000/ >/dev/null \
  && echo "HTTP health check passed on 127.0.0.1:8000"
```

有效证据应同时满足：

1. `systemctl is-active` 输出 `active`；
2. `after_pid` 是非零 PID，正常情况下与 `before_pid` 不同；
3. `ss` 显示进程监听 `127.0.0.1:8000`；
4. `curl` 输出健康检查通过。

自动部署脚本也会执行同样的核心判断：服务必须是 `active` 且 `http://127.0.0.1:8000/` 返回成功，才会在 Workflow 日志输出重启前后 PID、服务启动时间和健康检查地址。失败时会自动回滚。

若任一检查失败，查看服务日志：

```bash
sudo systemctl status googlecloudvoice
sudo journalctl -u googlecloudvoice -n 100 --no-pager
readlink -f /opt/googlecloudvoice/current
```

### 6.2 查看和管理服务日志

生产服务使用 `backend/logging.json` 将业务日志、异常堆栈以及 Uvicorn 生命周期日志统一输出到 stdout，再由 systemd/journald 以 `googlecloudvoice` 标识收集。

仓库中的 systemd 模板不会自动覆盖服务器 `/etc/systemd/system` 下的现有配置。首次启用本日志配置或模板发生变化后，需要在服务器重新安装并加载一次：

```bash
sudo install -m 644 \
  /opt/googlecloudvoice/current/deploy/systemd/googlecloudvoice.service \
  /etc/systemd/system/googlecloudvoice.service
sudo systemctl daemon-reload
sudo systemctl restart googlecloudvoice
```

然后查看最近 100 行：

```bash
sudo journalctl -u googlecloudvoice -n 100 --no-pager
```

实时跟踪日志：

```bash
sudo journalctl -u googlecloudvoice -f
```

查看本次系统启动以来的日志，或者只查看最近 30 分钟：

```bash
sudo journalctl -u googlecloudvoice -b --no-pager
sudo journalctl -u googlecloudvoice --since "30 minutes ago" --no-pager
```

只查看 warning 及以上级别的异常日志：

```bash
sudo journalctl -u googlecloudvoice -p warning --no-pager
```

确认日志来自当前重启后的进程：

```bash
current_pid="$(systemctl show --property=MainPID --value googlecloudvoice)"
active_since="$(systemctl show --property=ActiveEnterTimestamp --value googlecloudvoice)"

printf 'pid=%s active_since=%s\n' "$current_pid" "$active_since"
sudo journalctl -u googlecloudvoice --since "$active_since" --no-pager
```

应用会记录 TTS 提供方切换、Gemini 模型失败、STT Live API 降级、客户端断开和异常堆栈，但不会记录音频内容、TTS 原文、API Token 或请求头。Uvicorn 原始访问日志被显式关闭，因为 WebSocket 鉴权 Token 当前位于 URL 查询参数中，直接记录访问 URL 会泄露凭证。

查看 journald 占用空间：

```bash
sudo journalctl --disk-usage
```

日志保留和磁盘上限由服务器 `/etc/systemd/journald.conf` 统一管理。生产环境应结合磁盘容量设置 `SystemMaxUse`、`SystemKeepFree` 和 `MaxRetentionSec`，修改后执行 `sudo systemctl restart systemd-journald`。

健康检查失败时 Workflow 会自动回滚。需要人工回滚时，先确认目标版本目录，再原子替换软链接：

```bash
cd /opt/googlecloudvoice
ln -s releases/替换为目标提交SHA .rollback-current
mv -Tf .rollback-current current
sudo systemctl restart googlecloudvoice
curl --fail http://127.0.0.1:8000/
```

版本目录不会自动删除，便于审计和手动回滚。确认旧版本不再需要后，再由运维人员按保留策略清理。

## 7. 网络与安全检查

- SSH 端口只允许可信来源访问；公网只开放 80/443，完成 TLS 后将 80 重定向到 443。
- `uvicorn` 只监听 `127.0.0.1:8000`，不直接暴露给公网。
- 不在 GitHub 中保存 `GEMINI_API_KEY` 或 `API_ACCESS_TOKEN`，两者由服务器环境文件维护。
- 每次更换服务器 SSH 主机密钥后，同步更新 `DEPLOY_KNOWN_HOSTS`。
- 当前按产品决定未限制上传大小，Nginx 模板使用 `client_max_body_size 0`；后续修复资源限制时应一并收紧该配置。
