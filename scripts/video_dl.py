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
    python3 video_dl.py "<链接>" --quality 1080      # 限制最高分辨率（默认 1080）

输入可以直接是分享文本，脚本会自己把链接抠出来。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.expanduser("~/.workbuddy-ai/binaries/python/envs/douyin-dl")
VENV_PY = os.path.join(VENV_DIR, "bin", "python")
BOOTSTRAP_PY = (os.environ.get("VIDEO_DL_BOOTSTRAP_PY")
                or shutil.which("python3") or sys.executable)

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
        subprocess.run([BOOTSTRAP_PY, "-m", "venv", VENV_DIR], check=True, capture_output=True)
        subprocess.run([os.path.join(VENV_DIR, "bin", "pip"), "install", "-q",
                        "websocket-client"], check=True, capture_output=True)
        print("  ✅ 环境已重建")
        os.execv(VENV_PY, [VENV_PY, me] + sys.argv[1:])
    except Exception as e:
        print(f"  ❌ 环境重建失败: {e}")
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
    r"(?:www\.)?(?:twitter|x)\.com/[^\s\u4e00-\u9fff]+|"
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
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if os.path.isfile(os.path.join(p, "yt-dlp")):
            return True
    return bool(subprocess.run(["which", "yt-dlp"], capture_output=True).returncode == 0)


# ─────────────────────── 通道实现 ───────────────────────

def run_douyin(urls, out_dir, only_info):
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


def run_ytdlp(urls, out_dir, quality, only_info):
    print("=" * 62)
    print(f"  通用通道（yt-dlp） · {len(urls)} 条链接")
    print("=" * 62)
    print()
    os.makedirs(out_dir, exist_ok=True)

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

        base = ["yt-dlp", "--no-warnings", "--newline",
                "--no-playlist", "--no-mtime",
                "-o", os.path.join(staging, "%(title).80s [%(id)s].%(ext)s")]

        if only_info:
            base += ["--skip-download", "--print",
                     "  ✅ %(title)s | %(duration)s秒 | %(uploader)s"]
            rc = subprocess.call(base + [url])
        else:
            fmt = (f"bv*[height<={quality}]+ba/b[height<={quality}]/b"
                   if quality else "bv*+ba/b")
            base += ["-f", fmt, "--merge-output-format", "mp4"]
            rc = subprocess.call(base + [url])
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
    ap = argparse.ArgumentParser(description="统一视频下载入口")
    ap.add_argument("input", nargs="+", help="视频链接或分享文本")
    ap.add_argument("--info", action="store_true", help="只看信息不下载")
    ap.add_argument("--out", default=None, help="输出目录，默认 ~/Downloads/视频")
    ap.add_argument("--quality", type=int, default=1080, help="最高分辨率，默认 1080")
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
    ensure_env()
    main()
