# 闪词 · LittleFlash

一款安静、顺手的单词闪卡网页：从 `src/wordbooks` 动态读取词书，按你设定的方向、顺序与节奏在英文/中文之间切换；登录后还能把学习进度、学习时长与设置保存到服务器，换一台设备也能接着学。

- 后端：Flask 3 + SQLite（WAL），无外部数据库依赖
- 前端：原生 JavaScript + CSS，无构建步骤
- 数据：账号、偏好、学习进度、站点设置全部存放在 `data/littleflash.db`

## 功能

- **闪卡学习**：自动/手动切换，可调 1–30 秒间隔；按词书顺序或随机乱序（每完成一轮重新打乱）。
- **词书管理**：把 JSON 放进 `src/wordbooks` 即自动识别，可按整本或单个单元学习。
- **发音**：调用有道词典音频并由后端缓存，前端控制音量与开关。
- **用户账号**：内置 440 个默认账号 + `admin` + `telecomadmin`，支持改密、新增、停用。
- **学习进度**：记录词书、单元、第几个词、轮次、累计时长与最近学习时间，首页自动续学。
- **个人设置**：每个账号独立保存词书、方向、顺序、节奏、音量，互不影响。
- **游客模式**：不登录也能调整设置，偏好只写进当前浏览器的 localStorage。
- **站点默认值**：超级管理员可修改未登录访客看到的默认设置。
- **用户管理**：管理员可查看所有人的进度、重置密码、新增账号；超级管理员还能创建管理员、停用账号。

## 快速开始

```bash
# 任选其一
uv sync
# 或
python -m venv .venv && .venv/bin/pip install -e .
```

启动：

```bash
# 开发模式（Flask 内置服务器）
.venv/bin/python -m littleflash

# 生产入口（内置 gunicorn，1 worker + 4 threads）
.venv/bin/python deploy/LittleFlash.py
```

首次启动会自动创建 `data/littleflash.db`：建表、写入站点默认设置，并生成初始账号。终端会打印随机生成的管理员密码，同时写入 `data/initial-accounts.txt`（权限 600，登录后请尽快修改密码并妥善保管）。

默认访问：

- 学习页：<http://127.0.0.1:4570/>
- 登录：<http://127.0.0.1:4570/login>
- 设置：<http://127.0.0.1:4570/settings>
- 学习档案：<http://127.0.0.1:4570/account>
- 用户管理：<http://127.0.0.1:4570/admin>

## 账号体系

### 初始账号

| 账号                  | 角色    | 初始密码                   |
| ------------------- | ----- | ---------------------- |
| `240101` … `240855` | 普通用户  | 与用户名相同（共 8×55 = 440 个） |
| `admin`             | 管理员   | 随机 8 位字母数字             |
| `telecomadmin`      | 超级管理员 | 随机 8 位字母数字             |

- 用户名规则：`240` + 一位组号（1–8）+ 两位序号（01–55），例如 `240101`、`240155`、`240801`、`240855`。
- 随机密码去掉了容易混淆的 `0 O 1 l I`，首次建库后只出现一次；请查看 `data/initial-accounts.txt` 或启动日志。
- 默认账号的初始密码等于用户名，属于公开的初始凭据，建议首次登录后在「我的学习档案 → 修改密码」里改掉。

### 权限

| 能力               | 普通用户 | 管理员 | 超级管理员 |
| ---------------- | ---- | --- | ----- |
| 保存自己的设置 / 学习进度   | ✅    | ✅   | ✅     |
| 查看自己的学习档案、改自己的密码 | ✅    | ✅   | ✅     |
| 查看所有人的进度         | —    | ✅   | ✅     |
| 重置他人密码、新增普通账号    | —    | ✅   | ✅     |
| 新增管理员、停用/启用账号    | —    | —   | ✅     |
| 修改未登录用户的默认设置     | —    | —   | ✅     |

### 忘记密码

- 普通用户：让管理员在「用户管理」里点「重置密码」，留空会自动生成 8 位随机密码。
- 管理员：让超级管理员重置。
- 超级管理员也进不去时，在服务器上直接改库：
  ```bash
  cd /home/admin/littleflash
  LITTLEFLASH_HOME=$PWD PYTHONPATH=$PWD/src .venv/bin/python - <<'PY'
  from pathlib import Path
  from littleflash.users import find_user, set_password
  db = Path("data/littleflash.db")
  user = find_user(db, "telecomadmin")
  set_password(db, user.id, "新的强密码")
  print("done")
  PY
  ```

## 学习进度

- 首页加载时读取服务器上保存的断点：同词书、同单元时优先按 `word_en` 定位，找不到再退回词序号；轮次一并恢复。
- 登录状态下每 12 秒上报一次学习时长；切词时额外补一次；关闭/切走页面前再补一次（`keepalive`）。
- 单次上报的时长上限为 600 秒，越界或非法字段由服务端夹紧后再累加。
- 游客不上报进度，只保留本机设置。

## 配置

配置不再使用 `config.yml`，而是存放在数据库的 `site_settings` 表；首次启动写入默认值，可用任意 SQLite 客户端修改。

| 键                                | 默认值             | 说明                             |
| -------------------------------- | --------------- | ------------------------------ |
| `app.host`                       | `127.0.0.1`     | 监听地址                           |
| `app.port`                       | `4570`          | 监听端口                           |
| `app.debug`                      | `false`         | Flask 调试模式                     |
| `app.session_hours`              | `2`             | 登录会话有效期                        |
| `app.secret_key`                 | 随机 64 位十六进制     | 会话签名密钥（首次建库生成）                 |
| `wordbooks.directory`            | `src/wordbooks` | 词书目录，相对项目根                     |
| `wordbooks.auto_reload`          | `true`          | 词书变动后自动重载                      |
| `wordbooks.max_file_size_kb`     | `4096`          | 单个词书文件大小上限                     |
| `wordbooks.max_books`            | `100`           | 最多加载词书数                        |
| `audio.enabled`                  | `true`          | 是否启用发音                         |
| `audio.type`                     | `2`             | 有道发音类型                         |
| `audio.timeout_seconds`          | `8`             | 下载音频超时                         |
| `audio.max_bytes`                | `3145728`       | 音频大小上限                         |
| `audio.cache_dir`                | `.cache/audio`  | 音频缓存目录                         |
| `audio.cache_ttl_hours`          | `720`           | 音频缓存时长                         |
| `security.trust_proxy_headers`   | `true`          | 是否信任 `X-Forwarded-*`（子路径部署需开启） |
| `security.cookie_secure`         | `false`         | HTTPS 站点请设为 `true`             |
| `security.login_max_attempts`    | `5`             | 单账号连续失败次数                      |
| `security.login_window_seconds`  | `600`           | 失败计数窗口                         |
| `security.login_lockout_seconds` | `900`           | 锁定时长                           |

环境变量优先级最高，便于同一份代码跑不同环境：

| 变量                                                             | 作用                                    |
| -------------------------------------------------------------- | ------------------------------------- |
| `LITTLEFLASH_HOME`                                             | 项目根目录（默认由代码位置推断）                      |
| `LITTLEFLASH_DB`                                               | 数据库路径（默认 `<项目根>/data/littleflash.db`） |
| `LITTLEFLASH_HOST` / `LITTLEFLASH_PORT`                        | 覆盖监听地址/端口                             |
| `LITTLEFLASH_DEBUG`                                            | `true` / `false`                      |
| `LITTLEFLASH_SECRET_KEY`                                       | 覆盖会话密钥                                |
| `LITTLEFLASH_WORDBOOKS`                                        | 覆盖词书目录                                |
| `LITTLEFLASH_TRUST_PROXY`                                      | 是否信任代理头                               |
| `LITTLEFLASH_COOKIE_SECURE`                                    | Cookie 是否带 Secure                     |
| `LITTLEFLASH_SESSION_HOURS`                                    | 会话有效期（小时）                             |
| `LITTLEFLASH_LOGIN_MAX_ATTEMPTS` / `LITTLEFLASH_LOGIN_LOCKOUT` | 登录限流参数                                |

> 修改 `app.host` / `app.port` 后需要重启服务；`security.cookie_secure` 修改后重启生效。

## 词书格式

```json
{
  "id": "pep2019-compulsory-1",
  "title": "人教版高中英语必修一",
  "stage": "senior",
  "edition": "2019",
  "units": [
    {
      "id": "unit-1",
      "title": "Unit 1 · Teenage Life",
      "words": [
        {
          "en": "serendipity",
          "zh": "n. 不期而遇的美好；意外发现珍奇事物的本领",
          "phonetic": "/ˌserənˈdɪpəti/"
        }
      ]
    }
  ]
}
```

说明：

- `id` 可省略，会从文件名生成；同一目录下重复 id 会自动追加后缀。
- 单元支持嵌套；也兼容顶层直接的 `"words"` 数组。
- 单词至少需要 `en` 和 `zh`；`phonetic` 可省略。
- 支持 UTF-8（包括 BOM），忽略符号链接文件。
- 修改后刷新浏览器即可应用，不需要重启服务。

## 使用方式

- **自动模式**：每一面停留设定秒数，英文面 → 中文面 → 下一个单词，循环进行。卡片上的暂停按钮可随时暂停/继续。
- **手动模式**：点击卡片或按空格翻面；第二面再操作即进入下一个单词。
- **方向**：设置页可以调换英文/中文哪一首先出现。
- **顺序**：按词书顺序，或每次打开学习页随机打乱；每完成一轮会重新洗牌。
- **单词范围**：可以选整本词书，也可以只选一个单元。
- **快捷键**：`Space` 暂停/继续（手动模式为翻面/下一个）、`←` / `→` 切换单词、`R` 重播发音。
- **声音**：浏览器自动播放策略要求页面至少有一次用户交互；页面会用「等待点击」和提示文字引导。
- 设置页任何修改都会在约 0.3 秒后自动保存；离开前若有未完成的保存，会提示确认。

### 两种设置模式

| 场景                  | 保存位置                                  | 影响范围        |
| ------------------- | ------------------------------------- | ----------- |
| 未登录                 | 当前浏览器的 `localStorage`                 | 仅这台设备，不影响他人 |
| 已登录                 | SQLite 的 `preferences` 表（`user:<id>`） | 仅当前账号       |
| 超级管理员 `?scope=site` | `preferences` 表的 `anonymous` 行        | 所有未登录访客的默认值 |

## 生产部署

实现方式是 gunicorn 监听本机端口，nginx 反向代理；示例配置见 [`deploy/nginx.conf`](deploy/nginx.conf)。

1. 准备环境：


   ```bash
   cd /path/to/LittleFlash
   uv sync      # 或 python -m venv .venv && .venv/bin/pip install -e .
   ```
2. 启动：
   ```bash
   # 单文件入口：读取 SQLite 配置，内置 gunicorn（1 进程 + 4 线程）
   .venv/bin/python deploy/LittleFlash.py
   ```
   systemd 示例见 [`deploy/littleflash.service`](deploy/littleflash.service)：
   ```ini
   [Service]
   User=admin
   WorkingDirectory=/home/admin/littleflash
   Environment=LITTLEFLASH_HOME=/home/admin/littleflash
   ExecStart=/home/admin/littleflash/.venv/bin/python /home/admin/LittleFlash/LittleFlash.py
   ```
   > 登录限流是进程内状态，请保持 `workers = 1`（多线程即可承载个人/小团队使用）。
3. 将 `deploy/nginx.conf` 复制到 nginx 配置目录，替换 `server_name`，然后：
   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```
4. 使用 HTTPS 时把 `security.cookie_secure` 设为 `true`，并让 nginx 转发 `X-Forwarded-Proto`（示例已包含）。

> 站点可以挂在域名根路径（`location /`），也可以挂在子路径（如 `https://example.com/littleflash/`）。

### 子路径反代（如 /littleflash）

```nginx
location = /littleflash {
    return 301 /littleflash/;
}

location ^~ /littleflash/ {
    proxy_pass http://127.0.0.1:4570/;   # 末尾的 / 会剥掉 /littleflash 前缀
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /littleflash;
    proxy_set_header Connection "";
    client_max_body_size 64k;
}
```

完整的片段见 [`deploy/nginx-littleflash-subpath.conf`](deploy/nginx-littleflash-subpath.conf)。

- 必须使用 `^~` 前缀匹配。若站点里存在 `location ~ .*\.(js|css)$` 之类的正则 location，正则优先级高于普通前缀匹配，`/littleflash/static/*.js` 会被当成站点本地文件而 404。
- `proxy_pass` 末尾的 `/` 不能省，否则前缀不会被剥掉，应用会按根路径收到整个 URI。
- `security.trust_proxy_headers` 为 `true` 时应用才会读取 `X-Forwarded-Prefix`（`ProxyFix(x_prefix=1)`）。

### 反向代理要点

- nginx 必须发送 `Host`、`X-Real-IP`、`X-Forwarded-For`、`X-Forwarded-Proto`；示例使用 `$http_host`，在非标准端口下也能让 Origin 校验通过。
- gunicorn 只绑定 `127.0.0.1`，不要直接把端口暴露公网。
- 登录/设置接口只传小型 JSON，示例限制 `client_max_body_size 64k`。
- 音频由后端代理，nginx 不需要额外放行有道域名。
- 静态文件由 Flask 提供；如果自行让 nginx `alias` 静态目录，请同时配置缓存头，并继续让 API 走反向代理。

## 安全设计

- 密码使用 Werkzeug 哈希存储，校验失败不区分「用户不存在」与「密码错误」。
- 登录失败按「IP + 用户名」滚动计数，另有更宽松的 IP 级总闸；触发后返回 `429` 与 `Retry-After`。
- 所有写接口校验 CSRF 双令牌（Session + `X-CSRF-Token`）与 Origin / `Sec-Fetch-Site`。
- 会话使用 HttpOnly + SameSite=Lax 的签名 Cookie；登录、改密成功时清空旧会话并轮换 CSRF。
- 严格 CSP（无 `unsafe-inline`）、`X-Frame-Options: DENY`、`nosniff`、COOP 等响应头。
- SQLite 文件建议保持 `600`；数据库内含密码哈希，不要提交到版本库（见 `.gitignore`）。
- 静态资源带内容指纹（`?v=<hash>`），升级后浏览器会自动拉取新版本。

## 测试

```bash
.venv/bin/python -m pytest -q
```

- `tests/test_app.py`：账号初始化、登录/限流、会话、权限、设置隔离、站点默认值、学习进度、管理员操作、音频与安全头。
- `tests/test_subpath.py`：`X-Forwarded-Prefix` 下的静态资源、链接与登录跳转。
- `.jstest/e2e-local.js`（可选）：用无头 Chrome 跑真实浏览器流程（登录、续学、设置保存、用户管理）。

本地跑端到端：

```bash
# 终端 A：启动服务（示例端口 5055）
LITTLEFLASH_PORT=5055 python -m littleflash

# 终端 B：运行浏览器检查
node .jstest/e2e-local.js
```

## 数据、备份与迁移

- 全部状态都在 `data/littleflash.db`（WAL 模式会额外产生 `-wal` / `-shm` 文件）。
- 备份：停服务后复制整个 `data/` 目录，或执行 `sqlite3 data/littleflash.db ".backup 'backup.db'"`。
- 迁移到新机器：复制项目与 `data/littleflash.db` 即可；`app.secret_key` 在库内，旧会话继续有效。
- 删除数据库会重新生成 440 个默认账号和新的管理员密码。

## 常见问题

**页面顶部显示「登录」，设置保存后没有同步？**  
未登录时设置只写本机浏览器；登录后才会保存到账号。登录入口在 `/login`。

**保存设置提示「登录已过期」？**  
会话默认 2 小时，过期后写接口返回 401，前端会跳到登录页；重新登录即可。

**登录提示「尝试次数过多」？**  
同一账号连续失败 5 次会锁定 15 分钟；等待倒计时结束，或让管理员重置密码。

**改完端口/HTTPS 没生效？**  
`app.port`、`security.cookie_secure` 等在启动时读取，修改后需要重启服务。
