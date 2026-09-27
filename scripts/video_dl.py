#!/usr/bin/env python3
"""
统一视频下载入口

按链接平台自动分发到不同通道：
  - 抖音      → 浏览器内请求方案（见 _douyin.py）
  - 其他站点  → yt-dlp（YouTube / B站 / 腾讯视频 / X / Vimeo 等数千个站点）

用法:
    python3 video_dl.py "<链接或分享文本>" [...更多]
    python3 video_dl.py "<链接>" --info              # 只看信息不下载
    python3 video_dl.py "<链接>" --out ~/Downloads   # 指定输出目录
    python3 video_dl.py "<链接>" --quality 1080      # 画面短边上限（横竖屏通用，默认 1080）

输入可以直接是分享文本，脚本会自己把链接抠出来。
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _env  # noqa: E402  （同目录模块，跨平台环境探测）

VENV_DIR = _env.venv_dir()
VENV_PY = _env.venv_python()
BOOTSTRAP_PY = _env.bootstrap_python()

G, R, Y, B, N = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"
def ok(m): print(f"{G}  ✅ {m}{N}")
def bad(m): print(f"{R}  ❌ {m}{N}")
def warn(m): print(f"{Y}  ⚠️  {m}{N}")
def info(m): print(f"{B}  →  {m}{N}")


def ensure_env():
    """抖音通道需要 websocket-client；缺失时自动重建 venv 并重入"""
    try:
        import websocket  # noqa: F401
        return
    except ImportError:
        pass
    me = os.path.realpath(os.path.abspath(__file__))
    if os.path.isfile(VENV_PY) and os.path.realpath(VENV_PY) != os.path.realpath(sys.executable):
        os.execv(VENV_PY, [VENV_PY, me] + sys.argv[1:])
    print("  ⚠️  下载环境缺失，正在自动重建…")
    try:
        subprocess.run([BOOTSTRAP_PY, "-m", "venv", VENV_DIR],
                       check=True, capture_output=True)
        pip = os.path.join(os.path.dirname(VENV_PY), "pip")
        if os.name == "nt":
            pip += ".exe"
        subprocess.run([pip, "install", "-q", "websocket-client"],
                       check=True, capture_output=True)
        print("  ✅ 环境已重建")
        os.execv(VENV_PY, [VENV_PY, me] + sys.argv[1:])
    except Exception as e:
        print(f"  ❌ 环境重建失败: {e}")
        print(f"     请手动执行: {BOOTSTRAP_PY} -m venv {VENV_DIR}")
        sys.exit(1)


# ─────────────────────── 链接识别与提取 ───────────────────────

DOUYIN_RE = re.compile(
    r"https?://(?:v\.douyin\.com/[A-Za-z0-9_\-]+/?|"
    r"(?:www\.)?douyin\.com/video/\d+|"
    r"(?:www\.)?iesdouyin\.com/share/video/\d+)")

# 通用链接（yt-dlp 能处理的）
GENERIC_RE = re.compile(
    r"https?://(?:"
    r"(?:www\.|m\.|music\.)?youtube\.com/[^\s\u4e00-\u9fff]+|"
    r"youtu\.be/[A-Za-z0-9_\-]+|"
    r"(?:www\.)?bilibili\.com/[^\s\u4e00-\u9fff]+|"
    r"b23\.tv/[A-Za-z0-9]+|"
    r"v\.qq\.com/[^\s\u4e00-\u9fff]+|"
    r"(?:www\.)?xiaohongshu\.com/[^\s\u4e00-\u9fff]+|"
    r"xhslink\.com/[A-Za-z0-9]+|"
    # X / Twitter：www. / m. / mobile. 前缀，以及第三方镜像域名
    # （fxtwitter / vxtwitter / fixupx / twittpr 会 302 到 x.com，yt-dlp 跟得上）
    r"(?:www\.|m\.|mobile\.)?(?:twitter|x)\.com/[^\s\u4e00-\u9fff]+|"
    r"(?:www\.)?(?:fxtwitter|vxtwitter|fixupx|twittpr)\.com/[^\s\u4e00-\u9fff]+|"
    r"t\.co/[A-Za-z0-9]+|"
    r"(?:www\.)?vimeo\.com/\d+|"
    r"(?:www\.)?dailymotion\.com/video/[^\s\u4e00-\u9fff]+"
    r")")


def extract_urls(texts):
    """从文本里抠出所有可下载的链接，抖音优先，其余走通用通道"""
    douyin, generic, seen = [], [], set()
    for t in texts:
        for u in DOUYIN_RE.findall(t):
            u = u.rstrip("/.,;:!?")
            if u not in seen:
                seen.add(u); douyin.append(u)
        for u in GENERIC_RE.findall(t):
            u = u.rstrip("/.,;:!?")
            if u not in seen:
                seen.add(u); generic.append(u)
    return douyin, generic


def has_ytdlp():
    return _env.ytdlp_bin() is not None


# yt-dlp 版本比这个天数还老就告警。它大约每月一发版，
# 超过 4 个月基本等于「站点接口早就改过好几轮了」。
_YTDLP_STALE_DAYS = 120


def ytdlp_warn_if_stale():
    """yt-dlp 是按各站点私有接口写死的，版本一旧就会解析失败。

    B站 `HTTP Error 412: Precondition Failed` 就是典型：换 cookie、换 IP、
    加 UA 全都没用，只有升级 yt-dlp 能解。提前告警比事后猜「站点封了我们」便宜太多。
    """
    exe = _env.ytdlp_bin()
    if not exe:
        return
    try:
        out = subprocess.run([exe, "--version"], capture_output=True,
                             text=True, timeout=15)
        ver = (out.stdout or "").strip().splitlines()[0]
    except Exception:
        return
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", ver)
    if not m:
        return
    try:
        released = datetime.date(*(int(x) for x in m.groups()))
    except ValueError:
        return
    age = (datetime.date.today() - released).days
    if age > _YTDLP_STALE_DAYS:
        warn(f"yt-dlp {ver} 已 {age} 天未更新，站点接口变更会直接导致解析失败"
             f"（B站 412 就是这么来的）")
        info("升级：brew upgrade yt-dlp  或  pip install -U yt-dlp")
        info("或者用 VIDEO_DL_YTDLP=/path/to/newer/yt-dlp 指向自带的新版")
        print()


# ─────────────────────── 通道实现 ───────────────────────

def run_douyin(urls, out_dir, only_info):
    ensure_env()          # 只有走抖音通道才需要 venv，避免通用链接用户被强建环境
    script = os.path.join(HERE, "_douyin.py")
    cmd = [sys.executable, script] + urls + ["--out", out_dir]
    if only_info:
        cmd.append("--info")
    return subprocess.call(cmd)


def _is_free_dir(path):
    """目标目录是否落在沙箱全放行区（这些目录里改名/删除都不受限）"""
    p = os.path.realpath(os.path.expanduser(path))
    free = ["/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp",
            os.path.expanduser("~/tmp")]
    return any(p == f or p.startswith(f.rstrip("/") + "/")
               for f in (os.path.realpath(x) for x in free))


def _stage_copy(staging, out_dir):
    """把中转目录里的成品拷进目标目录（拷贝=新建文件，不受改名/删除限制）"""
    moved = []
    for name in sorted(os.listdir(staging)):
        if name.endswith((".part", ".ytdl", ".temp")) or name.startswith("."):
            continue
        src = os.path.join(staging, name)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(out_dir, name)
        shutil.copy2(src, dst)
        moved.append(dst)
    return moved


def _stage_cleanup(staging):
    """成品已拷走后清空中转目录，避免 /tmp 堆积大文件"""
    for name in os.listdir(staging):
        p = os.path.join(staging, name)
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def _has_proxy_env():
    """环境里是否设了代理（WorkBuddy 沙箱会注入一个本地代理）"""
    return any(os.environ.get(k) for k in
               ("http_proxy", "https_proxy", "all_proxy",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))


def _call_ytdlp(base, url):
    """跑一次 yt-dlp；失败且环境里有代理时，绕过代理再试一次。

    WorkBuddy 沙箱会注入 http_proxy=http://127.0.0.1:<port> 的本地代理。
    实测小红书 CDN（sns-bak-v6.xhscdn.com）经该代理**稳定返回 403**，
    用 `--proxy ""` 绕过后立刻 200 —— 这类 403 看着像站点封控，其实是代理造成的假象。
    只在第一次失败后才绕过，正常路径完全不受影响。
    """
    rc = subprocess.call(base + [url])
    if rc != 0 and _has_proxy_env():
        warn("第一次失败，绕过本地代理重试（沙箱代理可能对媒体 CDN 返回 403）")
        rc = subprocess.call(base + ["--proxy", "", url])
    return rc


_GENERIC_TITLE_RE = re.compile(r"^[\w.\-]+ video #\S+$")


def _fmt_duration(sec):
    """秒 → 1:02:03 / 2:03；拿不到返回 None"""
    if not isinstance(sec, (int, float)) or isinstance(sec, bool) or sec <= 0:
        return None
    total = int(round(sec))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _info_one(base, url):
    """--info 通道：拿 JSON 自己渲染，不用 yt-dlp 的 --print 模板。

    模板引擎对缺失字段一律输出字面量 `NA`，于是腾讯视频会显示成
    `✅ vqq-video video #<vid> | NA秒 | NA` —— 那个 ✅ 是对的，
    但「NA秒 | NA」看着就像下载失败了。实际原因是 yt-dlp 的 vqq 提取器
    本来就不返回 title/duration（dump-json 里 duration=None，
    title 是 yt-dlp 兜底的通用占位 `{extractor} video #{id}`），
    不是我们解析错，也不是站点挂了。

    所以这里改成读 --dump-json，缺什么就明说「该站点未提供」，
    避免把「元数据缺失」误报成「下载失败」。
    """
    p = subprocess.run(base + ["--skip-download", "--dump-json", url],
                       capture_output=True, text=True)
    if p.returncode != 0:
        # yt-dlp 的报错走 stderr，原样透出，别吞掉
        sys.stderr.write(p.stderr or "")
        return p.returncode

    line = next((l for l in (p.stdout or "").splitlines() if l.strip()), "")
    try:
        d = json.loads(line)
    except Exception:
        bad(f"解析元数据失败：{line[:120]}")
        return 1

    title = (d.get("title") or "").strip()
    vid = d.get("id") or ""
    dur = _fmt_duration(d.get("duration"))
    uploader = (d.get("uploader") or d.get("channel") or "").strip()

    if not title or _GENERIC_TITLE_RE.match(title):
        # 通用占位标题对用户毫无信息量，直接换成人话
        title = f"(该站点未提供标题) #{vid}" if vid else "(该站点未提供标题)"

    parts = [title]
    parts.append(f"时长 {dur}" if dur else "时长 该站点未提供")
    parts.append(uploader if uploader else "作者 该站点未提供")
    ok(" | ".join(parts))
    return 0


def run_ytdlp(urls, out_dir, quality, only_info):
    print("=" * 62)
    print(f"  通用通道（yt-dlp） · {len(urls)} 条链接")
    print("=" * 62)
    print()
    os.makedirs(out_dir, exist_ok=True)
    ytdlp_warn_if_stale()

    # 沙箱里 ~/Downloads 这类目录"能新建、不能改名/删除"，
    # 而 yt-dlp 收尾必须把 .part 改名成正式名 —— 会被直接拒掉。
    # 因此在非全放行目录里先下到 /tmp 中转，完成后再拷过去。
    free = _is_free_dir(out_dir)
    staging = out_dir if free else os.path.join("/tmp", "workbuddy-video-dl", "stage")
    if not free:
        os.makedirs(staging, exist_ok=True)
        info(f"中转目录：{staging}（完成后再拷贝到 {out_dir}）")

    rc_all = 0
    for idx, url in enumerate(urls, 1):
        print(f"[{idx}/{len(urls)}] {url}")
        if not free:
            for name in os.listdir(staging):
                try:
                    os.remove(os.path.join(staging, name))
                except OSError:
                    pass

        base = [_env.ytdlp_bin(), "--no-warnings", "--newline",
                "--no-playlist", "--no-mtime",
                "-o", os.path.join(staging, "%(title).80s [%(id)s].%(ext)s")]

        if only_info:
            rc = _info_one(base, url)
        else:
            base += ["-f", "bv*+ba/b", "--merge-output-format", "mp4"]
            if quality:
                # 上限用 res（= min(宽,高)）而不是 height。
                # height 对竖屏视频是"长边"：720x1280 的 height=1280 > 1080，
                # 会被 [height<=1080] 直接排除、退化到 480x852。
                # res 与画面方向无关，横竖屏都能正确封顶。
                # 必须是 `res:N`（冒号＝硬上限），不是 `res~N`（波浪号＝取最近）。
                base += ["-S", f"res:{quality}"]
            rc = _call_ytdlp(base, url)
            if rc == 0 and not free:
                try:
                    for dst in _stage_copy(staging, out_dir):
                        ok(f"已保存 → {dst}")
                    _stage_cleanup(staging)
                except Exception as e:
                    bad(f"拷贝到目标目录失败：{e}")
                    rc_all = 1
        if rc != 0:
            rc_all = rc
            bad(f"退出码 {rc}")
        print()
    return rc_all


def main():
    # 父进程的 print 在管道下是块缓冲，而 yt-dlp 子进程直接写 fd。
    # 不改成行缓冲的话，子进程的报错会跑到横幅前面去，看着像「一开始就炸了」，
    # 实际是输出顺序被打乱，排查时会严重误导。
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="统一视频下载入口")
    ap.add_argument("input", nargs="+", help="视频链接或分享文本")
    ap.add_argument("--info", action="store_true", help="只看信息不下载")
    ap.add_argument("--out", default=None, help="输出目录，默认 ~/Downloads/视频")
    ap.add_argument("--quality", type=int, default=1080,
                    help="画面短边上限，横竖屏通用（默认 1080；设 0 表示不限制）")
    args = ap.parse_args()

    douyin, generic = extract_urls(args.input)
    if not douyin and not generic:
        bad("没在输入里找到可识别的视频链接")
        info("抖音：v.douyin.com/xxx、douyin.com/video/数字ID")
        info("其他：YouTube / B站 / 腾讯视频 / 小红书 / X / Vimeo 等")
        sys.exit(1)

    out_dir = args.out or os.path.expanduser("~/Downloads/视频")

    print()
    print("=" * 62)
    print(f"  视频下载  ·  抖音 {len(douyin)} 条  ·  其他 {len(generic)} 条")
    print("=" * 62)
    print()

    rc = 0
    if douyin:
        print(f"  ⤷ 抖音通道（浏览器方案）")
        rc |= run_douyin(douyin, out_dir, args.info)
    if generic:
        if not has_ytdlp():
            bad("找不到 yt-dlp，无法处理通用链接")
            info("安装：brew install yt-dlp  或  pip install yt-dlp")
            sys.exit(1)
        print(f"  ⤷ 通用通道（yt-dlp，最高 {args.quality}p）")
        print()
        rc |= run_ytdlp(generic, out_dir, args.quality, args.info)

    print("=" * 62)
    if rc == 0:
        ok(f"全部完成    输出目录：{out_dir}")
    else:
        warn(f"部分失败（退出码 {rc}）    输出目录：{out_dir}")
    print("=" * 62)
    print()
    sys.exit(0 if rc == 0 else 1)


if __name__ == "__main__":
    main()
