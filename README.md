# 闪词 · LittleFlash

> 一眼，入心。  
> 一个安静、好看的单词闪卡网页，带 Python 后端与 nginx 部署方案。

LittleFlash 从 `src/wordbooks` 动态读取词书，按你设定的方向、顺序与频率在英文/中文之间切换；可以在词面显示时朗读，也可以在释义显示时再朗读一遍。所有学习偏好保存在服务端，设置页由 `config.yml` 中的独立密码保护。

当前版本：**0.1.3.2 Vodyanitsa**。支持同时选择多本词书的多个单元、可复现的随机种子、后台发音预缓存，以及无后端的单文件 HTML 版本。

## 功能

- **双面闪卡**：先英后中，或先中后英；英文面显示单词与音标。
- **自动 / 手动节奏**：自动模式按 1–30 秒（0.5 秒步进）停留并切换；手动模式点击卡片、按钮或按空格翻面。
- **词书动态识别**：启动后仍可新增、删除或修改 `src/wordbooks/*.json`，无需重启；支持子目录。
- **独立学习范围**：可同时勾选**多本词书、每本书的多个单元**，组合成跨书学习范围。
- **有道单词发音**：英文面与中文面都会自动朗读；后端代理并缓存网易有道词典音频，不把用户 IP 直接交给第三方。开启后台预缓存后，会提前下载当前学习范围的发音。
- **免密码本地设置**：`/settings` 打开即可调整，默认只把偏好写进当前浏览器的 localStorage，不影响其他人、也不写服务器配置；需要改全站默认值时才用 `config.yml` 里的密码登录。
- **简洁的响应式界面**：居中闪卡布局，桌面到手机自动适配；内置 Fraunces / Manrope 字体，无需访问 Google Fonts。
- **适合 nginx 反代**：标准 Flask WSGI 应用，附 gunicorn 与 nginx 示例。
- **单文件版**：`scripts/build_standalone.py` 可生成一个无后端的 HTML，词书与设置全部内嵌，直接放到 Cloudflare Pages / 任意静态托管即可。

## 目录结构

```text
LittleFlash/
├── config.example.yml           # 配置示例；复制为 config.yml 后修改
├── pyproject.toml
├── src/
│   ├── littleflash/             # Flask 后端、模板、静态资源
│   │   ├── app.py
│   │   ├── audio.py             # 有道发音代理 + 磁盘缓存 + 后台预热
│   │   ├── config.py
│   │   ├── preferences.py       # 多词书选择 / 随机种子 / 原子写入
│   │   ├── security.py          # 密码校验与登录限流
│   │   ├── wordbooks.py         # 动态词书发现与校验
│   │   ├── static/
│   │   └── templates/
│   └── wordbooks/               # 词书目录
├── data/                        # 运行时生成：偏好、会话密钥
├── .cache/audio/                # 有道发音缓存
├── standalone/                  # 单文件版源码（模板 / JS / CSS）
├── scripts/
│   ├── build_standalone.py      # 生成 dist/littleflash-<版本号>-standalone.html
│   └── fill_translations.py     # 补全词书里缺失的释义
├── dist/                        # 单文件版构建产物（已忽略）
├── deploy/
│   ├── nginx.conf
│   └── littleflash.service
└── tests/
```

## 快速开始

需要 Python 3.11+。推荐使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync

# 第一次运行前，从示例复制配置并修改设置页密码
cp config.example.yml config.yml
uv run python -m littleflash
```

打开：

- 闪卡首页：<http://127.0.0.1:4570/>
- 设置页：<http://127.0.0.1:4570/settings>

也可以使用 venv + pip：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp config.example.yml config.yml
littleflash
```

默认只监听 `127.0.0.1:4570`，适合由 nginx 反代；本机调试也可以直接访问。生产环境不要使用 Flask 开发服务器。

## 配置 `config.yml`

`config.yml` 不会提交到 Git：从 [`config.example.yml`](config.example.yml) 复制一份再改。设置页密码也可用环境变量 `LITTLEFLASH_SETTINGS_PASSWORD` 覆盖。

| 键 | 说明 |
| --- | --- |
| `app.host` / `app.port` | 开发服务器监听地址与端口；默认只监听本机。 |
| `app.debug` | 生产环境必须为 `false`。 |
| `app.session_hours` | 设置页登录有效期，默认 2 小时。 |
| `app.secret_key` | 可选。留空时自动生成到 `data/.secret_key`（权限 600）。 |
| `wordbooks.directory` | 词书目录，相对路径以 `config.yml` 所在目录为基准。 |
| `wordbooks.auto_reload` | 是否自动感知词书增删改。 |
| `wordbooks.max_file_size_kb` / `max_books` | 防止异常大文件拖垮服务。 |
| `settings.password` | 设置页访问密码，只存在服务器端。 |
| `settings.password_hash` | 可选，Werkzeug 密码哈希；设置后优先生效。 |
| `settings.state_file` | 学习偏好 JSON 的保存路径。 |
| `audio.enabled` | 是否启用有道发音代理。 |
| `audio.type` | 有道发音类型，1 美音 / 2 英音。 |
| `audio.cache_dir` / `cache_ttl_hours` | 音频缓存目录与有效期。 |
| `audio.prefetch` | 是否允许后台预拉取发音，默认 `true`；设置页里管理员还可以临时关闭。 |
| `security.trust_proxy_headers` | 部署在 nginx 后设为 `true`，以正确识别客户端 IP 和 HTTPS。 |
| `security.cookie_secure` | 全站启用 HTTPS 后改为 `true`。 |
| `security.login_*` | 登录失败次数、统计窗口与锁定时长。 |

### 两种设置模式

| 模式 | 进入方式 | 保存位置 | 影响范围 |
| --- | --- | --- | --- |
| 本地模式（默认） | 直接打开 `/settings` | 浏览器 `localStorage` | 仅当前浏览器 |
| 服务器模式 | 页面上点「管理员登录」并输入密码 | `settings.state_file` | 所有访客的默认值 |

- 未登录时表单照样可用，改完立即写入本机；刷新后仍是自己的设置，换一台设备或换浏览器则互不影响。
- 未登录时的任何修改都不会调用写接口，`/api/settings` 仍然要求密码 + CSRF，因此不会污染别人的设置。
- 本机设置优先于服务器默认值；一旦登录，页面改用服务器设置，退出登录后回到自己的本地设置。
- 「恢复默认」在本地模式下只清除本机存储，服务器设置不受影响。

### 修改设置页密码

```yaml
settings:
  password: "换成你自己的强密码"
  state_file: "data/preferences.json"
```

密码只用于解锁“写入服务器”的模式。更推荐使用哈希：

```bash
uv run python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('换成你自己的强密码'))"
```

把输出写入 `settings.password_hash`，并移除 `settings.password`。也可以用环境变量覆盖：

```bash
export LITTLEFLASH_SETTINGS_PASSWORD='另一个强密码'
```

其他可用环境变量：`LITTLEFLASH_CONFIG`、`LITTLEFLASH_SECRET_KEY`、`LITTLEFLASH_HOST`、`LITTLEFLASH_PORT`、`LITTLEFLASH_DEBUG`、`LITTLEFLASH_TRUST_PROXY`、`LITTLEFLASH_COOKIE_SECURE`。

> 示例密码 `littleflash-admin` 只用于第一次打开页面。部署前一定修改，并限制配置文件权限：

```bash
chmod 600 config.yml
```

## 词书格式与动态识别

每个 `.json` 文件是一本词书。后端会递归扫描词书目录，读取成功的新文件会自动出现在页面上（词书列表请求时重新检查目录签名）。损坏、超大、无有效单词的文件会被跳过并在服务日志中给出警告，不会让站点崩溃。

```json
{
  "id": "my-book",
  "title": "我的词书",
  "stage": "senior",
  "edition": "2026",
  "source": "自建",
  "units": [
    {
      "id": "u1",
      "title": "Unit 1",
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
- **顺序**：按词书顺序，或用随机种子生成可复现的随机顺序。
- **单词范围**：可以同时勾选多本词书，每本书还能只挑几个单元。
- **快捷键**：`Space` 暂停/继续（手动模式为翻面/下一个）、`←` / `→` 切换单词、`R` 重播发音。
- **声音**：浏览器自动播放策略要求页面至少有一次用户交互；页面会用「等待点击」和提示文字引导。点击任意处后，后续英文面和中文面都会尝试朗读。
- 设置页任何修改都会在约 0.3 秒后自动保存；离开前若有未完成的保存，会提示确认。

## 0.1.3.2：选择与预览的细节修正

### 勾选词书不再自动全选单元

- 在设置页点某本词书的复选框，现在只会展开它的单元列表，**默认一个单元都不勾**；
- 取消勾选词书才会清空这本书已选的单元；
- 如果只勾了词书、还没点单元，卡片边框会变成提示色，摘要显示「已勾选 N 本词书，还需要在展开的列表里点选至少一个单元」；
- 「全选本书 / 清空本书」按钮保留，需要整本时一键即可。

### 预览窗口默认暂停

- 打开设置页时，右侧预览卡停在原地，不会再自动翻面；
- 新增一排控制按钮：**上一张 / 播放（暂停）/ 下一张**，右上角徽标同步显示「已暂停」或「演示中」；
- 点卡片仍是翻面：英文面 → 中文面，已在中文面时再点进入下一张；
- 预览卡聚焦时可以用 `←` / `→` 切换卡片；
- 播放只在点了「播放」后才按「自动切换间隔」推进；手动切换卡片不会朗读，播放推进时才发音；
- 单文件版打开设置抽屉时也会暂停主卡，关闭抽屉后按原状态恢复。

## 0.1.3：多词书、随机种子与预缓存

### 多本词书 + 多个单元

设置页的「词书与范围」现在是一组可勾选的卡片：

- 勾选任意多本词书；每本书下面可以只选部分单元；
- 首页会把所有选中单元合并成一个学习队列，卡片下方显示「词书 · 单元」；
- 进度条按合并后的总词数计算；
- 保存格式为 `selections: [{wordbook_id, unit_ids}]`，`unit_ids: ["all"]` 表示整本。

### 随机种子

- 顺序选项从「随机打乱」改为「随机顺序」，并新增种子输入框；
- 首次使用自动生成 8 位种子，点「换一个」可刷新；
- 种子支持中英文、数字与常见符号，最长 64 个字符；
- 同一个种子 + 同一个范围 = 同一套顺序；每完成一轮会用「种子 + 轮次」重新洗牌；
- 旧配置里的 `order: shuffle` 会自动迁移为 `random`。

### 发音预缓存

- 首页加载 deck 时，后端会把当前选中范围的单词排进后台队列，提前下载到 `.cache/audio`；
- 默认开启，可在设置页「后台预缓存发音」关闭（仅管理员登录后可见）；
- 预缓存是尽力而为：不阻塞页面请求，失败会在后续触发时重试，单次最多入队 800 个词。

### 加载速度

- 首页只请求一个 `/api/deck`，不再逐本下载完整词书；
- `/api/deck` 与 `/api/wordbooks/*` 支持 ETag / 304；
- HTML、JSON、CSS、JS 自动 gzip（实测 deck 102 KB → 18.7 KB）；
- 单文件版使用紧凑数据、无外部字体、首屏 HTML 在前、数据块在后，gzip 后约 132 KB（对比旧单文件版 236 KB）。

## 单文件 HTML（无后端）

```bash
python scripts/build_standalone.py
# 产物：dist/littleflash-<版本号>-standalone.html
```

- 词书、样式、脚本、图标全部内嵌，双击即可打开；
- 设置保存在 `localStorage`，支持多词书/多单元、随机种子、发音；
- 发音直接请求有道 `dictvoice`，不经过后端；
- 不含任何服务端接口，可直接放到 Cloudflare Pages、对象存储或任意静态托管。

## nginx 生产部署

实现方式是 gunicorn 监听本机端口，nginx 反向代理；示例配置见 [`deploy/nginx.conf`](deploy/nginx.conf)。

1. 准备环境：

   ```bash
   cd /path/to/LittleFlash
   uv sync
   # 修改 config.yml：密码、缓存路径、cookie_secure 等
   ```

2. 启动 gunicorn（建议 1 个进程 + 多线程）：

   ```bash
   .venv/bin/gunicorn \
     --workers 1 --threads 4 \
     --bind 127.0.0.1:4570 \
     --access-logfile - --error-logfile - \
     littleflash.wsgi:app
   ```

   也可以使用 systemd 示例 [`deploy/littleflash.service`](deploy/littleflash.service)。

3. 将 `deploy/nginx.conf` 复制到 nginx 的配置目录，替换 `server_name`，然后：

   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```

4. 使用 HTTPS 时，在 `config.yml` 设置：

   ```yaml
   security:
     trust_proxy_headers: true
     cookie_secure: true
   ```

   并让 nginx 转发 `X-Forwarded-Proto`（示例配置已经包含）。

> 站点可以挂在域名根路径（`location /`），也可以挂在子路径（如 `https://example.com/littleflash/`）。子路径部署时按下面的「子路径反代」配置：nginx 剥掉前缀并发送 `X-Forwarded-Prefix`，应用会自动把静态资源、接口与跳转地址都加上前缀。

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

### 单文件启动入口

`deploy/LittleFlash.py` 是可直接运行的生产入口：它把 `<部署目录>/src` 加入模块搜索路径、读取 `<部署目录>/config.yml`，并用内置的 gunicorn（1 进程 + 4 线程）监听 `config.yml` 里的地址与端口。

```bash
./.venv/bin/python /path/to/LittleFlash.py
```

部署目录默认为 `/home/admin/littleflash`，可用环境变量 `LITTLEFLASH_HOME` 覆盖。

### 反向代理要点

- nginx 必须发送 `Host`、`X-Real-IP`、`X-Forwarded-For`、`X-Forwarded-Proto`；示例使用 `$http_host`，在非标准端口下也能让 Origin 校验通过。
- gunicorn 只绑定 `127.0.0.1`，不要直接把 4570 暴露公网。
- 设置接口只传小型 JSON，示例限制 `client_max_body_size 64k`。
- 音频由后端代理，nginx 不需要额外放行有道域名。
- 静态文件由 Flask 提供；如果自行让 nginx `alias` 静态目录，请同时配置缓存头，并继续让 API 走反向代理。

## 安全设计

- 设置页密码从不通过 API 或 HTML 下发；支持明文或 Werkzeug 哈希，使用常量时间比较。
- 登录失败按 IP 滚动计数并锁定；同时校验 CSRF 双令牌（Session + `X-CSRF-Token`）与 Origin / Fetch-Site。
- 会话使用 HttpOnly + SameSite=Lax 的签名 Cookie；登录成功时清空旧会话并轮换 CSRF。
- 设置页所有写操作要求已认证且 CSRF 通过；非 JSON 请求拒绝。
- 未登录访客只拿到与 `/api/bootstrap` 相同的公开默认值，修改只写浏览器 `localStorage`；读取时会做类型与范围清洗，内容损坏或被篡改则回退默认值。
- 响应带 CSP、X-Content-Type-Options、X-Frame-Options、Referrer-Policy、Permissions-Policy 等安全头；HTTPS 下启用 HSTS。
- 有道发音只允许词书中实际存在的单词，固定上游域名，限制重定向域名、文件大小和超时；缓存路径由哈希生成，无路径穿越。
- 词书扫描限制文件大小、文件数量和总词数，忽略符号链接和隐藏路径。
- 偏好文件使用文件锁 + 原子替换写入，权限 600；会话密钥首次运行自动生成，权限 600。
- 登录限流是进程内状态，示例部署使用单 worker；多 worker 时每个进程各自计数。

## 测试

```bash
uv sync
uv run pytest -q
```

当前 20 项测试，覆盖登录/CSRF/限流、多词书选择与 deck、随机种子迁移、设置保存、词书动态发现、发音白名单、gzip/ETag、音频预缓存与安全响应头。

浏览器端到端脚本（可选，需本机 Chrome）：

- `.jstest/e2e-013.js`：登录、多选、种子、保存、首页续学；
- `.jstest/e2e-standalone.js`：用 `file://` 打开单文件版，检查首屏、设置、多选与种子。

## 依赖与致谢

- [Flask](https://flask.palletsprojects.com/) + [Gunicorn](https://gunicorn.org/)
- 单词发音来自[网易有道词典](https://dict.youdao.com/)，仅作个人学习用途；音频请求经服务端缓存，请遵守上游服务条款。
- 界面使用 Fraunces 与 Manrope 字体，SIL Open Font License 1.1（许可文件在 `src/littleflash/static/fonts/`）。
- 现有词书来自项目初期的 PEP 2019 数据，具体来源见各 JSON 的 `source` 字段。

## 常见问题

**页面能打开，但没有声音？**

先点击页面任意位置解锁浏览器音频策略，再检查服务器是否能访问 `dict.youdao.com`。首次请求有数秒延迟属正常，成功后音频会缓存到 `.cache/audio`。

**修改了词书但没有出现？**

确认文件是合法 UTF-8 JSON、单词包含 `en` 和 `zh`、文件不超过 `wordbooks.max_file_size_kb`。查看后端日志会看到对应的跳过原因。页面刷新后会重新扫描。

**设置保存失败？**

本地模式下只依赖浏览器存储：无痕/隐私模式可能禁用 localStorage，状态会显示“本机存储不可用”。
服务器模式下请检查 `data/` 目录的写权限；若超过 2 小时未操作，重新登录即可。网络失败时页面会保留“重试”按钮。
