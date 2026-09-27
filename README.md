# video-download-skill

多平台视频下载工具，按链接平台自动分发：

- **抖音** → 浏览器方案（Playwright Chromium + CDP，在页面上下文内请求详情接口）
- **其他站点** → [yt-dlp](https://github.com/yt-dlp/yt-dlp)（YouTube / B站 / 腾讯视频 / 小红书 / X / Vimeo 等数千个站点）

输入可以直接是带噪音的分享文本，脚本会自己把链接抠出来。

> 本项目是一个 [WorkBuddy](https://workbuddy.ai/invite?code=Y7CADHL3) Skill，也可以当独立命令行工具用。

## 特性

- **贴原文就行** —— `2.56 复制打开抖音，看看【xxx的作品】… https://v.douyin.com/xxxx/ :6pm …` 这种分享文本直接丢进来，不用手工清理
- **一次多条、可混合平台** —— 分通道处理
- **环境自举** —— 依赖缺失时自动重建 venv 并重入，不会突然失效
- **跨平台** —— macOS / Linux / Windows 都能跑，不写死任何绝对路径，路径可用环境变量覆盖
- **输出可追溯** —— 文件名含标题与视频 ID，便于校验

## 依赖

| 依赖 | 用途 | 安装 |
|---|---|---|
| Python 3.10+ | 运行脚本 | — |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 通用通道 | `brew install yt-dlp` |
| ffmpeg | 音视频流合并 | `brew install ffmpeg` |
| Playwright Chromium | 抖音通道 | `npx playwright install chromium` |

> **yt-dlp 要勤升级。** 它是按各站点私有接口写死的，站点一改版就得跟着升。
> 包管理器会把版本钉在安装那天，不会自动跟。版本超过 120 天时脚本会主动告警。
> 典型症状：B站报 `HTTP Error 412: Precondition Failed`，换 cookie / 换 IP / 改 UA 全都没用，
> **只有升级 yt-dlp 能解**。详见 SKILL.md 坑 10。

## 安装

作为 WorkBuddy Skill：

```bash
git clone https://github.com/vern33/video-download-skill.git \
  ~/.workbuddy-ai/skills/video-download
```

当独立工具用的话，clone 到任意目录即可。

## 用法

```bash
python3 scripts/video_dl.py "<链接或分享文本>"
```

| 参数 | 说明 |
|---|---|
| `<输入>` | 链接或分享文本，可传多个、可混合平台 |
| `--info` | 只看信息不下载（标题 / 作者 / 时长） |
| `--out <目录>` | 输出目录，默认 `~/Downloads/视频/` |
| `--quality <n>` | **画面短边**上限，横竖屏通用，默认 1080；设 `0` 表示不限制 |

示例：

```bash
# 单条链接
python3 scripts/video_dl.py "https://www.youtube.com/watch?v=xxxxxxxxxxx"

# 分享文本原样贴进去
python3 scripts/video_dl.py "2.56 复制打开抖音，看看【xxx的作品】… https://v.douyin.com/xxxx/ :6pm …"

# 只看信息，不下载
python3 scripts/video_dl.py "<链接>" --info

# 多条混合平台，限制到 720p
python3 scripts/video_dl.py "<抖音链接>" "<YouTube链接>" --quality 720
```

## 工作原理

```
video_dl.py            统一入口：提取链接 → 按平台分发
├── _douyin.py         抖音通道：Playwright Chromium + CDP
├── _env.py            跨平台环境探测：venv / 解释器 / 浏览器
└── yt-dlp             通用通道（以子进程调用）
```

### 跨平台与环境变量

`_env.py` 负责所有路径推导，按平台自动选择默认位置：

| 平台 | 默认 venv |
|---|---|
| macOS / Linux | `~/.cache/video-download/venv`（遵循 `XDG_CACHE_HOME`） |
| Windows | `%LOCALAPPDATA%\video-download\venv` |

需要改路径时用环境变量，**不用改代码**：

| 变量 | 作用 |
|---|---|
| `VIDEO_DL_VENV` | 直接指定 venv 目录（优先级最高） |
| `VIDEO_DL_HOME` | 数据目录，venv 建在其下的 `venv/` |
| `VIDEO_DL_BOOTSTRAP_PY` | 建 venv 用的基础解释器 |
| `VIDEO_DL_BROWSER` | 直接指定浏览器可执行文件 |
| `VIDEO_DL_YTDLP` | 直接指定 yt-dlp 可执行文件（系统那份太旧时用） |
| `PLAYWRIGHT_BROWSERS_PATH` | Playwright 官方变量，同样被识别 |

浏览器按「`VIDEO_DL_BROWSER` → Playwright 缓存 → PATH」的顺序查找，优先用
`headless_shell`（纯二进制，启动最快）。

venv 只在**下抖音时**才会按需创建；只下 YouTube / B站等站点不会产生任何 venv。

### 为什么抖音要单独走浏览器

抖音的 Argus 风控要求 `UIFID_TEMP` / `s_v_web_id` cookie，且**服务端校验真实性**；
纯 Python 直连还会因 TLS 指纹不同直接吃 403。所以必须在真实浏览器上下文里、
用页面内的 `fetch`（`credentials: 'include'`）去请求接口。

已实测确认各条公开路径都取不到直链：share 页的 `_ROUTER_DATA` 只剩渲染上下文、
`iteminfo` 返回空 body、oEmbed 404、移动端 API 返回空，
**浏览器方案是唯一可行路径。**

### X / Twitter

**公开推文免登录、免 cookie 就能下。** 下列链接写法都支持：

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

镜像域名会 302 到 `x.com`，yt-dlp 自己跟得上，脚本不需要做域名改写。
一条推文含多个视频时会全部下下来。

仅粉丝可见、敏感/年龄限制的推文拿不到——这是 X 的服务端边界，不是脚本问题。

### 通用通道为什么要中转

部分受限环境下，目标目录「能新建文件但不能改名/删除」，而 yt-dlp 收尾必须把
`.part` 改名成正式文件名。脚本会自动先下到临时目录完成收尾，再把成品拷贝过去。

## 已知限制

- **抖音 IP 限流** —— 短时间反复请求会吃 `HTTP 403`。脚本内置 3 次重试 + 4 秒退避。
- **抖音图文作品**没有 mp4 直链，会报「没找到 mp4 直链」，属正常情况。
- **抖音直链带签名会过期**，每次都要重新解析。
- **会员 / 付费内容** —— 各平台的 DRM 正片都拿不到，只能下免费或试看部分。
- **需要登录的内容**（如 YouTube 年龄限制视频）可能需要 cookies，当前未配置。
- **X / Twitter 私密内容**（仅粉丝可见、敏感推文）拿不到，只有公开推文可下。
- **X / Twitter 已删除内容**会报 `No video could be found in this tweet` / `Broadcast no longer exists`，属正常情况。

## 故障排查

| 现象 | 处理 |
|---|---|
| 抖音「浏览器 CDP 端口没起来」 | 检查 `--no-sandbox` 是否还在 |
| 抖音抓取失败 / 403 | 等几分钟再试，大概率是 IP 限流 |
| 进度跑到 100% 后报 rename / `.part` 错误 | 目标目录不允许改名，确认走临时目录中转逻辑 |
| 推文报 `No video could be found in this tweet` | 该推文确实没有视频，或被删除/设为私密；不是脚本问题 |
| 推文报 `Broadcast no longer exists` | 直播回放已被删除，正常现象 |
| 推文下载很慢 / 中途超时 | 一条推文可能含多个长视频，会全部下完；放后台跑或加长超时 |
| 竖屏视频画质明显偏低 | 清晰度上限必须用 `res` 而非 `height`，见 SKILL.md 坑 8 |
| **任何站点突然 `HTTP Error 412` / 解析失败** | **先查 `yt-dlp --version` 对比最新版**，多半是版本旧了，别怀疑站点封了你 |
| B站 `412 Precondition Failed` | 换 cookie / 换 IP / 改 UA 都无效，升级 yt-dlp 才好；临时可 `VIDEO_DL_YTDLP` 指向新版 |
| 找不到 Playwright 浏览器 | `npx playwright install chromium`，或用 `VIDEO_DL_BROWSER=/path/to/chrome` 指定 |
| 报找不到 yt-dlp | `brew install yt-dlp` |
| 环境重建失败 | 手动 `python3 -m venv <VIDEO_DL_VENV>` 再 `<venv>/bin/pip install websocket-client` |
| 想确认当前用的是哪套环境 | `python3 -c "import sys;sys.path.insert(0,'scripts');import _env;print(_env.venv_dir(),_env.venv_python(),_env.find_browser())"` |

## 开发笔记

`SKILL.md` 记录了开发过程中踩过的坑，**改动代码前建议先读一遍**，尤其是：

- `--no-sandbox` 为什么不能删
- 为什么不能用 `--cookies-from-browser`
- `.part` 改名失败的成因与中转方案
- 清晰度上限为什么必须用 `res` 而不是 `height`（否则竖屏视频静默降级）
- macOS 钥匙串弹窗的成因与处理

## 免责声明

本工具仅供个人学习与备份用途。请遵守各平台的用户协议与著作权法规，
不要用于下载或传播侵权内容。

## License

[MIT](LICENSE)
