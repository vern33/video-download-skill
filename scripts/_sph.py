#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信视频号的「跨平台转载」兜底通道。

微信视频号（weixin.qq.com/sph/<id>）在浏览器里拿不到视频地址：短链 301 到
channels.weixin.qq.com/finder-preview/pages/sph?id=...，那是个等
WeixinJSBridgeReady 的空壳启动器，页面里 0 个 mp4/m3u8 —— 不是反爬，是
微信客户端的原生能力，浏览器永远触发不了。yt-dlp 也没有对应提取器。

唯一能脚本化的路：拿分享文本里的标题/作者去别的平台找同一条的转载页，
从页面内嵌数据里抠出视频直链再下载。搜索那一步依赖联网检索，由调用方
（上层 agent）完成，本模块只做确定性的「解析转载页 → 下载 → 落盘」。

实测样本（2026-11-15）：有驾 yoojia.com 的视频页把 mp4 直链放在
__INITIAL_STATE__ 里，URL 被 `%3A%5C%2F` 双重转义，需要先 unquote 再
把 `\\/` 还原成 `/` 才能正则提取。只提供 540p / 720p 两档。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36")

_TRAILING_JUNK = " \t\r\n.,;:!?、，。；：！？-—…·_"
_ILLEGAL = r'[\\/:*?"<>|]'
_MIN_SIZE = 100_000          # 小于这个数基本是错误页，不是视频


def ok(m): print(f"  \033[32m✅ {m}\033[0m")
def bad(m): print(f"  \033[31m❌ {m}\033[0m")
def info(m): print(f"  \033[34m→  {m}\033[0m")
def warn(m): print(f"  \033[33m⚠️  {m}\033[0m")


def _referer(url):
    p = urllib.parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


def fetch_html(url, timeout=30):
    """抓转载页 HTML，失败返回空串（由调用方继续试下一个候选页）"""
    curl = shutil.which("curl")
    if not curl:
        bad("找不到 curl")
        return ""
    try:
        p = subprocess.run(
            [curl, "-sL", "-A", _UA, "-e", _referer(url),
             "--max-time", str(timeout), url],
            capture_output=True, timeout=timeout + 10)
        return p.stdout.decode("utf-8", "ignore")
    except Exception as e:
        warn(f"抓页面失败（{type(e).__name__}）")
        return ""


def extract_media(html):
    """从页面内嵌数据里抠出所有 mp4/m3u8 直链，按清晰度从高到低排。

    页面里的 JSON 常被 URL 编码过一层（`https%3A%5C%2F%5C%2F...`），
    只 unquote 一次还不够，残留的 `\\/` 也要还原，否则正则取不到完整 URL。
    """
    if not html:
        return []
    t = urllib.parse.unquote(html)
    t = t.replace("\\/", "/").replace("\\u002F", "/").replace('\\"', '"')
    urls = set(re.findall(r'https?://[^\s"\'<>\\)]+?\.(?:mp4|m3u8)', t,
                          re.IGNORECASE))

    def rank(u):
        m = re.search(r"/(\d{3,4})p/", u)
        return int(m.group(1)) if m else 0

    return sorted(urls, key=lambda u: (-rank(u), u))


def download(url, dest, referer, timeout=300):
    """直链下载。返回 (是否成功, 说明)"""
    curl = shutil.which("curl")
    if not curl:
        return False, "找不到 curl"
    try:
        p = subprocess.run(
            [curl, "-sL", "-A", _UA, "-e", referer,
             "--max-time", str(timeout), "-o", dest,
             "-w", "%{http_code}", url],
            capture_output=True, text=True, timeout=timeout + 20)
    except Exception as e:
        return False, f"下载异常 {type(e).__name__}"
    code = (p.stdout or "").strip()
    if code != "200":
        return False, f"HTTP {code or '无响应'}"
    size = os.path.getsize(dest) if os.path.isfile(dest) else 0
    if size < _MIN_SIZE:
        return False, f"只有 {size} B，不像视频"
    return True, f"{size:,} B"


def safe_name(title, sph_id, limit=80):
    """把标题清成可落盘的文件名（沿用 _douyin.py 的清洗思路）"""
    s = re.sub(r"\s+", " ", title or "").strip()
    s = re.sub(_ILLEGAL, "_", s).strip()
    if len(s) > limit:
        s = s[:limit].rstrip() + "…"
    s = s.strip(_TRAILING_JUNK)
    if not s:
        s = "视频号转载"
    tag = f"sph-{sph_id}" if sph_id else "转载"
    return f"{s} [{tag}].mp4"


def pick_title(texts):
    """没显式给 --title 时，从分享文本里兜底抠一句标题"""
    blob = " ".join(texts)
    blob = re.sub(r"https?://\S+", " ", blob)
    blob = re.sub(r"[#【】\[\]（）()|]", " ", blob)
    blob = re.sub(r"\s+", " ", blob).strip()
    return blob[:80]


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="微信视频号跨平台转载兜底下载")
    ap.add_argument("--page", action="append", default=[],
                    help="转载页 URL，可重复；依次尝试，第一个成功即停")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--title", default=None, help="用于命名的标题")
    ap.add_argument("--sph-id", default=None, help="视频号短链 ID，仅用于文件名标记")
    ap.add_argument("--info", action="store_true", help="只列出直链，不下载")
    args = ap.parse_args()

    if not args.page:
        bad("没给转载页")
        info("用法：python3 _sph.py --page <转载页URL> --out <目录> [--title 标题]")
        sys.exit(2)

    os.makedirs(args.out, exist_ok=True)
    title = args.title or pick_title(args.page)
    name = safe_name(title, args.sph_id)
    staging = tempfile.mkdtemp(prefix="video-dl-sph-")
    saved = []

    try:
        for page in args.page:
            html = fetch_html(page)
            media = extract_media(html)
            if not media:
                warn(f"页面里没找到视频直链：{page[:90]}")
                continue
            info(f"找到 {len(media)} 条直链，取清晰度最高的一条")
            best = media[0]
            m = re.search(r"/(\d{3,4})p/", best)
            if m:
                info(f"清晰度 {m.group(1)}p")
            if args.info:
                print(f"  {best}")
                sys.exit(0)

            tmp = os.path.join(staging, name)
            good, why = download(best, tmp, _referer(page))
            if not good:
                warn(f"下载失败：{why}")
                if os.path.isfile(tmp):
                    os.remove(tmp)
                continue
            dst = os.path.join(args.out, name)
            # 目标目录可能不允许改名/删除（如 ~/Downloads），所以先落 /tmp
            # 完成收尾，再用「拷贝=新建文件」的方式放过去。
            shutil.copy2(tmp, dst)
            saved.append((dst, why))
            break
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if not saved:
        bad("所有转载页都没能拿到视频")
        info("可以换一个转载平台再试，或改用手机端「保存到相册」/ 录屏")
        sys.exit(1)

    for path, why in saved:
        ok(f"已保存 → {path}    {why}")
    sys.exit(0)


if __name__ == "__main__":
    main()
