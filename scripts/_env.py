#!/usr/bin/env python3
"""
跨平台环境探测：venv 位置、基础 Python、Playwright 浏览器。

所有路径都可以用环境变量覆盖，默认值按平台推导，不写死任何绝对路径。

环境变量
    VIDEO_DL_HOME          数据目录（venv 的父目录）
    VIDEO_DL_VENV          venv 目录，优先级最高
    VIDEO_DL_BOOTSTRAP_PY  用来创建 venv 的基础 Python
    VIDEO_DL_BROWSER       直接指定浏览器可执行文件
    PLAYWRIGHT_BROWSERS_PATH  Playwright 官方变量，同样被识别
"""

import glob
import os
import shutil
import sys

APP = "video-download"


def data_dir():
    """放 venv 等可再生数据的目录。优先环境变量，其次按平台推导。"""
    home = os.environ.get("VIDEO_DL_HOME")
    if home:
        return os.path.expanduser(home)
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(root, APP)
    root = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache")
    return os.path.join(os.path.expanduser(root), APP)


def venv_dir():
    return os.path.expanduser(os.environ.get("VIDEO_DL_VENV")
                              or os.path.join(data_dir(), "venv"))


def venv_python():
    """venv 里的解释器路径（Windows 与 Unix 布局不同）"""
    d = venv_dir()
    for rel in (("bin", "python"), ("bin", "python3"), ("Scripts", "python.exe")):
        p = os.path.join(d, *rel)
        if os.path.isfile(p):
            return p
    return os.path.join(d, "bin", "python")


def bootstrap_python():
    """用来创建 venv 的基础解释器"""
    explicit = os.environ.get("VIDEO_DL_BOOTSTRAP_PY")
    if explicit:
        return os.path.expanduser(explicit)
    return shutil.which("python3") or shutil.which("python") or sys.executable


def playwright_bases():
    """所有可能的 Playwright 浏览器缓存根目录，已存在的排前面"""
    cands = []
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env:
        cands.append(os.path.expanduser(env))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            cands.append(os.path.join(local, "ms-playwright"))
    else:
        cands.append(os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright"))
        cands.append(os.path.join(os.path.expanduser("~"), "Library",
                                  "Caches", "ms-playwright"))
    seen, out = set(), []
    for c in cands:
        if c and c not in seen and os.path.isdir(c):
            seen.add(c)
            out.append(c)
    return out


_BROWSER_GLOBS = (
    "chromium_headless_shell-*/chrome-*/headless_shell",   # 首选：纯二进制
    "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    "chromium-*/chrome-linux/chrome",
    "chromium-*/chrome-win/chrome.exe",
)

_PATH_NAMES = ("headless_shell", "chromium", "chromium-browser",
               "google-chrome", "google-chrome-stable", "chrome")


def find_browser():
    """按「显式指定 → Playwright 缓存 → PATH」的顺序找浏览器"""
    explicit = os.environ.get("VIDEO_DL_BROWSER")
    if explicit:
        p = os.path.expanduser(explicit)
        if os.path.isfile(p):
            return p

    for base in playwright_bases():
        for pat in _BROWSER_GLOBS:
            hits = sorted(glob.glob(os.path.join(base, pat)))
            if hits:
                return hits[-1]

    for name in _PATH_NAMES:
        p = shutil.which(name)
        if p:
            return p
    return None
