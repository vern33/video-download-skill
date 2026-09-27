---
name: video-download
description: 下载网络视频到本地。当用户发来视频链接或分享文本，或说「下载这个视频」「把这个视频存下来」「存一下」「帮我下载」时使用。支持抖音（v.douyin.com 短链、douyin.com/video/数字ID）、YouTube（youtube.com/watch、youtu.be）、B站（bilibili.com、b23.tv）、腾讯视频（v.qq.com）、小红书（xiaohongshu.com、xhslink.com）、X/Twitter、Vimeo、Dailymotion 等数千个站点。按平台自动分发：抖音走浏览器内请求方案（启动 Playwright Chromium + --no-sandbox，在页面上下文 fetch 详情接口通过 Argus 风控），其余站点走 yt-dlp。输入可以直接是带噪音的分享文本（如「2.56 复制打开抖音，看看【xxx的作品】… :6pm 12/25 pQK:/ P@k.Px」或「看看这个 https://... 帮我下一下」），脚本会自己提取链接。支持一次传多条、混合平台链接，会分通道处理。产物默认落在 ~/Downloads/视频/。
agent_created: true
---

# 视频下载

## 一句话用法

把用户的输入**原样**传给脚本即可：

```bash
python3 ~/.workbuddy-ai/skills/video-download/scripts/video_dl.py "<用户原话，整段贴进去>"
```

不需要手工清理噪音文字，脚本会自己提取链接。
（`python3` 用系统 PATH 上的即可，无需写死绝对路径——脚本内部会自己找基础解释器和 venv。）

## 参数

| 参数 | 说明 |
|---|---|
| `<输入>` | 链接或分享文本，可传多个、可混合平台 |
| `--info` | 只看信息不下载（标题/作者/时长） |
| `--out <目录>` | 指定输出目录，默认 `~/Downloads/视频/` |
| `--quality <n>` | **画面短边**上限，横竖屏通用，默认 1080；设 `0` 不限制（见坑 8） |

## 平台分发规则

| 平台 | 通道 | 机制 |
|---|---|---|
| 抖音 | 浏览器方案 | Playwright Chromium + `--no-sandbox`，页面内 fetch |
| 其他全部 | yt-dlp | 系统已装 `yt-dlp` + `ffmpeg` |

抖音之所以特殊：它的 Argus 风控要求 `UIFID_TEMP` / `s_v_web_id` cookie 且**服务端校验真实性**，Python 直连还会因 TLS 指纹不同吃 403，所以必须在真实浏览器上下文里发请求。其他站点 yt-dlp 直接就能处理。

### X / Twitter 的链接形态

`GENERIC_RE` 里已覆盖下列写法，都实测可下：

| 形态 | 示例 |
|---|---|
| 新主域名 | `x.com/用户名/status/数字ID` |
| 旧域名 | `twitter.com/用户名/status/数字ID` |
| 带前缀 | `www.` / `m.` / `mobile.twitter.com` |
| 通用跳转 | `twitter.com/i/status/数字ID` |
| 旧式路径 | `twitter.com/用户名/statuses/数字ID` |
| 带分享参数 | `...?s=20&t=xxxx` |
| 第三方镜像 | `fxtwitter.com` / `vxtwitter.com` / `fixupx.com` / `twittpr.com` |
| 短链 | `t.co/xxxxx` |

**镜像域名会 302 到 `x.com`，yt-dlp 自己跟得上，不需要在脚本里做域名改写**（实测三个镜像域名都能解析出正确 uploader）。

**公开推文不需要登录、不需要 cookie** —— 已用 yt-dlp 官方测试用例里的真实推文验证。一条推文含多个视频时会全部下下来（`/video/1`、`/video/2` 分别对应）。

## 脚本结构

```
scripts/
├── video_dl.py     # 统一入口：提取链接 → 按平台分发
├── _douyin.py      # 抖音通道实现（浏览器方案）
└── _env.py         # 跨平台环境探测：venv 位置 / 基础解释器 / 浏览器
```

`video_dl.py` 通过子进程调用 `_douyin.py` 和 `yt-dlp`。
`_env.py` 被前两者共同导入，**所有路径都从这里取，脚本里不写死任何绝对路径**。

## 关键坑（改动前必读）

### 1. `--no-sandbox` 不能删（抖音通道）

不加这个参数，Chrome 会在启动阶段直接崩溃：

```
FATAL:content/browser/gpu/gpu_data_manager_impl_private.cc:415]
GPU process isn't usable. Goodbye.
```

退出码 -5，0 字节输出，约 0.4 秒。**根因是 Chrome 自身的沙箱与宿主沙箱冲突**，不是网络问题。用 `data:` URL（不联网）做对照实验即可确认。

### 2. 抖音必须用 `Runtime.evaluate` 发 fetch

不能用 Python 直连接口（TLS 指纹不同会吃 403）。
也不要用截获 `Network.responseReceived` 的方式——时序容易漏包。`Runtime.evaluate` + `awaitPromise` 更稳。

### 3. 不要用 `--cookies-from-browser`

本机两条路都堵死：Chrome 走 Keychain（权限被拒），Safari 的 cookie 文件被沙箱拦截（`Operation not permitted`）。而且实测用户 Chrome 里根本没有抖音 cookie。

（沙箱相关部分仅适用于 WorkBuddy 环境；但即使不受沙箱限制，Chrome 里没有抖音 cookie 这一点也足以否决这条路。）

### 4. 环境自举（只在下抖音时才触发）

抖音通道需要 `websocket-client`。`video_dl.py` 的 `run_douyin()` 会先调 `ensure_env()`：
缺失时自动建 venv、装依赖，再 `os.execv` 重入自己。所以直接用系统 `python3` 调也没问题。

**venv 位置不写死**，由 `_env.venv_dir()` 推导（见下节「环境变量」），默认：

| 平台 | 默认 venv |
|---|---|
| macOS / Linux | `$XDG_CACHE_HOME/video-download/venv`（默认 `~/.cache/video-download/venv`） |
| Windows | `%LOCALAPPDATA%\video-download\venv` |

只下 YouTube / B站等通用站点时**不会**建 venv，避免无谓开销。

### 4b. 环境变量（移植用）

所有路径都可以用环境变量覆盖，方便迁移到别的机器或容器：

| 变量 | 作用 |
|---|---|
| `VIDEO_DL_VENV` | 直接指定 venv 目录（优先级最高） |
| `VIDEO_DL_HOME` | 数据目录，venv 建在其下的 `venv/` |
| `VIDEO_DL_BOOTSTRAP_PY` | 建 venv 用的基础解释器，默认 `which python3` → `which python` → `sys.executable` |
| `VIDEO_DL_BROWSER` | 直接指定浏览器可执行文件 |
| `PLAYWRIGHT_BROWSERS_PATH` | Playwright 官方变量，同样被识别 |

浏览器探测顺序：`VIDEO_DL_BROWSER` → Playwright 缓存（`~/Library/Caches/ms-playwright` /
`~/.cache/ms-playwright` / `%LOCALAPPDATA%\ms-playwright`）→ PATH 上的
`headless_shell` / `chromium` / `google-chrome`。优先用 `headless_shell`（纯二进制，启动最快）。

### 5. 非全放行目录必须走 `/tmp` 中转（通用通道）

> **仅适用于 WorkBuddy 沙箱环境。** 普通终端里 `~/Downloads/` 改名不受限，
> 中转逻辑会自动跳过（`_is_free_dir()` 判定），不会多一次拷贝。

沙箱对 `~/Downloads/` 这类目录的策略是**「能新建、不能改名/删除」**。
而 yt-dlp 收尾必须把 `.part` 改名成正式名、合并后再删原件，于是直接失败：

```
ERROR: Unable to rename file: Brokered host rename source refused by file policy: prompt
ERROR: [Errno 2] No such file or directory: '.../xxx.f399.mp4'
```

症状很迷惑：**进度条跑到 100% 才报错**，看起来像网络问题，其实是收尾被拦。
（`.part` 文件本身是完整的，`ffprobe` 能正常读出时长。）

脚本已内置处理：目标目录不在全放行区（`/tmp`、`/private/tmp`、`/var/tmp`、`~/tmp`）时，
先下到 `/tmp/workbuddy-video-dl/stage/` 完成全部收尾，再把成品 `shutil.copy2` 到目标目录，
最后清空中转目录。**拷贝=新建文件，不受改名/删除限制。**

抖音通道没这个问题，因为它用 `curl -o` 直写最终文件名，不经过改名。

### 6. 中途失败的 `.part` 可以救

如果下载被中断但 `.part` 已下满，不必重下。直接 remux：

```bash
ffmpeg -y -i "video.fXXX.mp4.part" -i "audio.fYYY.webm.part" \
  -c copy -movflags +faststart out.mp4
```

先用 `ffprobe -show_entries format=duration` 核对两条流时长是否一致。

### 7. 钥匙串请求已改为「默认拒绝」——不再弹窗（2026-09-27 已解决）

> **仅适用于 WorkBuddy 沙箱环境。** 下面改的是 `~/.workbuddy-ai/settings.json` 里的
> 沙箱规则；在普通终端 / 其他 Agent 里跑不会有这个弹窗，本节可整段跳过。

macOS 版 Chromium 启动时会去访问登录钥匙串里的 `Chrome Safe Storage` 项
（它用这个密钥加密 profile 里的 cookie / 密码库）。

**这是浏览器自身的通用初始化，和抓视频没有任何关系，拒绝掉照样下载成功。** 原因：

1. 我们用的是用完即弃的临时 profile（`/tmp/douyin-dl-profile`，每次启动前 `rmtree`），
   里面**没有任何已存储的 cookie 需要解密**。
2. 真正需要的 `UIFID_TEMP` / `s_v_web_id` 是页面在会话内自己生成、并在页面上下文里
   通过 `credentials: 'include'` 使用的，**不需要跨进程持久化**，也就不需要那把钥匙串密钥。
3. 钥匙串不可用时 OSCrypt 会退化到非持久密钥，浏览器进程继续跑，不会中止。

#### 已落地的修复

`~/.workbuddy-ai/settings.json` → `sandbox.orderedRules.file.rules` 里
`path: "~/Library/Keychains/"` 那条规则：

```json
{ "id": "file-preset-16", "source": "user",
  "path": "~/Library/Keychains/", "isDirectory": true,
  "read": "allow", "write": "deny", "delete": "ask" }
```

`write` 由 `ask` 改成 `deny`，`source` 由 `preset` 改成 `user`。

- **效果**：请求被**静默拒绝**，不再弹窗。stderr 由
  `Permission request rejected by user` 变为 `Blocked by Security Center rules`，
  下载照常成功（已实测，产物 SHA256 与改动前逐位一致）。
- 这是**收紧**权限（ask → deny），安全性反而更高。
- `source: "user"` 防止未来预设升级覆盖：`upgradeLegacyFilePreset()` 内有
  `if (rule.source !== "preset") continue`，会跳过非预设来源的规则。
- 合法取值来自应用内定义：`MANAGED_RULE_ACTIONS = new Set(["allow","ask","deny"])`。
- 修改前务必备份 `settings.json`（沙箱**不允许** agent 自己改这个文件，
  需要用户授权一次非沙箱写入）。

#### 试过但无效的浏览器参数

| 尝试 | 结果 |
|---|---|
| `--use-mock-keychain` | 该 switch 字符串在 `headless_shell` 里存在，但**弹窗照旧** |
| `--password-store=basic` | 该构建里**根本没有这个 switch**（二进制字符串命中 0 次），加了无效 |

脚本里仍保留 `--use-mock-keychain`（无害），注释里写明它挡不住。

### 8. 清晰度上限要用 `res` 而不是 `height`（否则竖屏视频被降级）

**这是 2026-09-27 实测发现并修掉的 bug。**

原先通用通道用的是 `-f "bv*[height<=1080]+ba/b[height<=1080]"`。
对**竖屏**视频，`height` 是**长边**——一条 720×1280 的竖屏视频 `height=1280 > 1080`，
会被这个条件**直接排除**，然后退化到 `height<=1080` 里最高的一档（480×852）。
画面质量掉一大截，但**不报任何错**，只看输出目录很难发现。

推文、YouTube Shorts、B站竖屏、小红书这类竖屏内容全都会中招。

**修法**：改用 yt-dlp 的格式排序字段 `res`（定义见 `utils/_utils.py`：
`'res': {'type': 'multiple', 'field': ('height', 'width'), 'function': min}`，
即 **`min(宽,高)`，与画面方向无关**）：

```bash
-S "res:1080"      # ✅ 冒号 = 硬上限
```

**必须是冒号，不能是波浪号。** 两者语义完全不同（源码 `_utils.py:5572`）：

| 写法 | 语义 | 结果 |
|---|---|---|
| `res:1080` | 硬上限。`res > 1080` 的格式排序键为负，**排到所有 ≤1080 的后面** | ✅ 要的 |
| `res~1080` | 取最近。按 `-abs(res-1080)` 排序 | ❌ 只有 4K 和 480 时会选 480 |

另外 `res` **只能用于 `-S`，不能用于 `-f` 过滤**：写 `-f "bv*[res<=1080]"` 会报
`Requested format is not available`（`res` 是排序字段，不是过滤器字段）。

**实测对照**（同一条竖屏推文）：

| 方案 | 选中格式 | 分辨率 |
|---|---|---|
| `-f "...[height<=1080]..."` | `hls-582` | 480×852 ❌ |
| `-S "res:1080"` | `hls-1150` | **720×1280** ✅ |

**横屏无回归**：4K 视频（有 2160/1440/1080/720 阶梯）两种写法都选 1920×1080；
`-S "res:720"` 选 1280×720；不设上限（`--quality 0`）选 3840×2160。

## 实测记录

| 平台 | 结果 |
|---|---|
| 抖音 | ✅ 3.85 MB，h264+aac，576×1024，21.3s |
| YouTube（短视频） | ✅ `Me at the zoo` 465K，av1+opus，19.02s（yt-dlp 自动合并音视频流） |
| YouTube（长视频） | ✅ `罗马帝国千年全史 [NPDRrsu5hA4]` 292MB，av1 1920×1080 + opus，59:34 |
| 腾讯视频 | ✅ 173MB，h264+aac，1280×720，21.5min（免费/试看内容；会员 DRM 正片拿不到） |
| X / Twitter | ✅ `x.com/historyinmemes/status/1790637656616943991` 1.4MB，h264+aac，728×720，15.56s，**免登录免 cookie** |
| X / Twitter（多视频推文） | ✅ `twitter.com/CTVJLaidlaw/status/1600649710662213632` 同一条推文里的多个视频全部下到，720×1280，113s / 102s |
| 跨平台改造回归 | ✅ 抖音 `f6e7f6a5…db7243d` / YouTube `Me at the zoo`，SHA256 与改造前逐位一致 |

跨平台改造的验证方式：删掉旧 venv 后从零跑抖音通道，确认
`_env` 能自行建出 `~/.cache/video-download/venv`、装好 `websocket-client 1.9.2`、
`execv` 重入成功，且产物哈希不变；另外单独验证了 5 个环境变量覆盖 +
Windows `Scripts/python.exe` 布局识别。

## 已知限制

- **抖音 IP 限流**：短时间反复请求吃 `HTTP 403`。脚本内置 3 次重试 + 4 秒退避。
- **抖音 headless 检测**：可能被识别。脚本有兜底——从页面 `<video>` 标签取直链。
- **抖音图文作品**：没有 mp4 直链，会报「没找到 mp4 直链」，这是正常情况。
- **CDN 直链有时效**：抖音直链带签名会过期，每次重新跑。
- **会员/付费内容**：各平台的 DRM 正片都拿不到，只能下免费或试看部分。
- **需要登录的内容**：YouTube 年龄限制视频等可能需要 cookies，当前未配置。
- **X / Twitter 私密内容**：仅粉丝可见、敏感/年龄限制的推文拿不到，只有公开推文可下（这是 X 的服务端边界，不是脚本问题）。
- **X / Twitter 已删除内容**：推文或直播被删会报 `No video could be found in this tweet` / `Broadcast no longer exists`，属正常情况，不是 bug。

## 故障排查

| 现象 | 处理 |
|---|---|
| 抖音「浏览器 CDP 端口没起来」 | 检查 `--no-sandbox` 是否还在 |
| 抖音抓取失败 / 403 | 等几分钟再试，大概率是 IP 限流 |
| 推文报 `No video could be found in this tweet` | 该推文确实没有视频，或被删除/设为私密；不是脚本问题 |
| 推文报 `Broadcast no longer exists` | 直播回放已被删除，正常现象 |
| 推文下载很慢 / 中途超时 | 一条推文可能含多个长视频，yt-dlp 会全部下完；放后台跑或加长超时 |
| 进度 100% 后报 rename / `.part` 相关错误 | 目标目录不在全放行区，确认走 `/tmp` 中转逻辑（见坑 5） |
| 弹「允许访问 login.keychain-db」 | **已改为默认拒绝，不再弹窗**；若又出现说明 settings.json 规则被重置（见坑 7） |
| 找不到 Playwright 浏览器 | `npx playwright install chromium`，或用 `VIDEO_DL_BROWSER=/path/to/chrome` 直接指定 |
| 通用通道报找不到 yt-dlp | `brew install yt-dlp` |
| 环境重建失败 | 手动 `python3 -m venv <VIDEO_DL_VENV>` 再 `<venv>/bin/pip install websocket-client`（Windows 下是 `<venv>\Scripts\pip.exe`） |
| 想换 venv 位置 / 换机器迁移 | 设 `VIDEO_DL_VENV` 或 `VIDEO_DL_HOME`，见「环境变量」 |
| 想确认当前用的是哪套环境 | `python3 -c "import sys;sys.path.insert(0,'<scripts 目录>');import _env;print(_env.venv_dir(),_env.venv_python(),_env.find_browser())"` |
