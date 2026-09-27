#!/usr/bin/env python3
"""
抖音视频下载（浏览器内请求方案）

用法:
    python3 douyin_dl.py "<抖音分享文本或链接>" [...更多]
    python3 douyin_dl.py "<链接>" --info            # 只看信息
    python3 douyin_dl.py "<链接>" --keep-cookies    # 顺便导出 cookies.txt
    python3 douyin_dl.py "<链接>" --out ~/Downloads # 指定输出目录

设计要点:
  - 自动从分享文本里提取链接（抖音分享出来的是带噪音的文本）
  - 启动 Playwright 的 Chromium（必须 --no-sandbox，否则 GPU 进程崩溃）
  - 在页面上下文里 fetch 详情接口，cookie/Referer/TLS 指纹全原生
  - 环境自举：依赖缺失时自动重建 venv，不会突然失效
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _env  # noqa: E402  （同目录模块，跨平台环境探测）

VENV_DIR = _env.venv_dir()
VENV_PY = _env.venv_python()
BOOTSTRAP_PY = _env.bootstrap_python()


# ─────────────────────────── 环境自举 ───────────────────────────

def ensure_env():
    """确保 websocket-client 可用；不在合适的解释器里就重新拉起自己"""
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
        if os.sep in BOOTSTRAP_PY and not os.path.isfile(BOOTSTRAP_PY):
            print(f"  ❌ 找不到基础 Python: {BOOTSTRAP_PY}")
            sys.exit(1)
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


G, R, Y, B, N = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"
def ok(m): print(f"{G}  ✅ {m}{N}")
def bad(m): print(f"{R}  ❌ {m}{N}")
def warn(m): print(f"{Y}  ⚠️  {m}{N}")
def info(m): print(f"{B}  →  {m}{N}")


# ─────────────────────────── 链接提取 ───────────────────────────

URL_RE = re.compile(
    r"https?://(?:v\.douyin\.com/[A-Za-z0-9_\-]+/?|"
    r"www\.douyin\.com/video/\d+|"
    r"www\.iesdouyin\.com/share/video/\d+|"
    r"douyin\.com/video/\d+)")


def extract_urls(texts):
    """从任意文本里捞出抖音链接，去重并保持顺序"""
    found = []
    for t in texts:
        for u in URL_RE.findall(t):
            if u not in found:
                found.append(u)
    return found


def resolve_short_url(url, timeout=20):
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                          "Mobile/15E148 Safari/604.1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = r.geturl()
        m = re.search(r"/video/(\d+)", final) or re.search(r"(\d{19})", final)
        if m:
            return m.group(1)
    except Exception as e:
        warn(f"短链解析失败：{e}")
    return None


def find_browser():
    """交给 _env 做跨平台探测：显式指定 → Playwright 缓存 → PATH"""
    return _env.find_browser()


class CDP:
    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=30,
                                              suppress_origin=True,
                                              max_size=128 * 1024 * 1024)
        self._id = 0

    def send(self, method, params=None):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        return self._id

    def wait_result(self, want_id, limit=15):
        t0 = time.time()
        while time.time() - t0 < limit:
            try:
                self.ws.settimeout(2)
                m = json.loads(self.ws.recv())
            except Exception:
                continue
            if m.get("id") == want_id:
                return m.get("result")
        return None

    def drain(self, seconds):
        t0 = time.time()
        while time.time() - t0 < seconds:
            try:
                self.ws.settimeout(1)
                self.ws.recv()
            except Exception:
                pass

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


class Browser:
    """复用一个浏览器实例处理多条链接，避免反复冷启动"""

    def __init__(self, binpath, port=9566):
        self.binpath = binpath
        self.port = port
        self.profile = os.path.join(tempfile.gettempdir(), "douyin-dl-profile")
        shutil.rmtree(self.profile, ignore_errors=True)
        self.proc = None
        self.cdp = None

    def __enter__(self):
        cmd = [self.binpath, "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
               "--no-first-run", "--no-default-browser-check", "--disable-extensions",
               "--mute-audio", "--window-size=1440,900",
               "--autoplay-policy=no-user-gesture-required",
               # Chromium 在 macOS 启动时会去碰登录钥匙串（"Chrome Safe Storage"），
               # 沙箱会因此弹「允许访问 login.keychain-db」。这是浏览器自身的通用初始化，
               # 与抓视频无关；被拒绝也不影响下载（详见 SKILL.md 坑 7）。
               # 这个 switch 在 headless_shell 里存在，但实测**无法**消除该请求；
               # 注意 --password-store=basic 在该构建中不存在，别加。
               "--use-mock-keychain",
               f"--remote-debugging-port={self.port}", "--remote-allow-origins=*",
               f"--user-data-dir={self.profile}", "about:blank"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        targets = None
        for _ in range(80):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=2) as r:
                    targets = json.loads(r.read())
                break
            except Exception:
                time.sleep(0.4)
        if not targets:
            raise RuntimeError("浏览器 CDP 端口没起来")

        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            raise RuntimeError("没有可用的 page target")

        self.cdp = CDP(page["webSocketDebuggerUrl"])
        self.cdp.send("Network.enable")
        self.cdp.send("Page.enable")
        self.cdp.send("Runtime.enable")

        # 预热：访问首页，让服务端下发 s_v_web_id / UIFID_TEMP
        self.cdp.send("Page.navigate", {"url": "https://www.douyin.com/"})
        self.cdp.drain(7)
        return self

    def fetch_detail(self, video_id, retries=3):
        self.cdp.send("Page.navigate", {"url": f"https://www.douyin.com/video/{video_id}"})
        self.cdp.drain(8)

        js = """(async () => {
            const r = await fetch('/aweme/v1/web/aweme/detail/?aweme_id=%s',
                {credentials: 'include',
                 headers: {'accept': 'application/json, text/plain, */*'}});
            return await r.text();
        })()""" % video_id

        last = ""
        for attempt in range(retries):
            res = self.cdp.wait_result(self.cdp.send("Runtime.evaluate", {
                "expression": js, "awaitPromise": True, "returnByValue": True}), limit=30)
            body = ((res or {}).get("result") or {}).get("value") or ""
            if body:
                try:
                    d = json.loads(body).get("aweme_detail")
                    if d:
                        return d
                    last = body[:80]
                except Exception:
                    last = body[:80]
            if attempt < retries - 1:
                warn(f"第 {attempt + 1} 次未拿到数据，退避重试…")
                time.sleep(4)

        # 兜底：从页面 video 标签取直链
        res = self.cdp.wait_result(self.cdp.send("Runtime.evaluate", {
            "expression": """(() => {
                const v = document.querySelector('video');
                if (!v) return '';
                return v.src || (v.querySelector('source') && v.querySelector('source').src) || '';
            })()""", "returnByValue": True}), limit=8)
        src = ((res or {}).get("result") or {}).get("value") or ""
        if src:
            info("走兜底通道：从 video 标签取直链")
            return {"_fallback_src": src, "desc": None}
        if last:
            warn(f"接口返回异常：{last}")
        return None

    def cookies(self):
        res = self.cdp.wait_result(self.cdp.send("Network.getAllCookies"), limit=8)
        if not res:
            return []
        return [c for c in res.get("cookies", []) if "douyin" in (c.get("domain") or "")]

    def __exit__(self, *exc):
        if self.cdp:
            self.cdp.close()
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


def pick_mp4(detail):
    if detail.get("_fallback_src"):
        return detail["_fallback_src"]
    video = detail.get("video") or {}
    for key in ("play_addr", "download_addr", "play_addr_h264", "bit_rate"):
        node = video.get(key)
        if isinstance(node, list):
            for n in node:
                urls = (n.get("play_addr") or {}).get("url_list") or []
                if urls:
                    return urls[0]
            continue
        if isinstance(node, dict):
            urls = node.get("url_list") or []
            if urls:
                return urls[0]
    return None


def safe_name(title, vid):
    clean = re.sub(r"#[^\s#]+", "", title or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r'[\\/:*?"<>|]', "_", clean)[:60].strip("_ ")
    return clean or vid


def write_cookies(cookies, path):
    if not cookies:
        return 0
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n# 由 douyin_dl.py 自动生成\n\n")
        for c in cookies:
            dom = c.get("domain") or ""
            f.write("\t".join([
                dom,
                "TRUE" if dom.startswith(".") else "FALSE",
                c.get("path") or "/",
                "TRUE" if c.get("secure") else "FALSE",
                str(int(c.get("expires") or 0) or int(time.time()) + 86400 * 30),
                c.get("name") or "", c.get("value") or ""]) + "\n")
    return len(cookies)


def download(mp4, dest):
    rc = subprocess.call(["curl", "-sSL", "--fail", "--progress-bar",
                          "-A", "Mozilla/5.0", "-e", "https://www.douyin.com/",
                          "-o", dest, mp4])
    if rc == 0 and os.path.isfile(dest):
        return os.path.getsize(dest)
    return None


def main():
    ap = argparse.ArgumentParser(description="抖音视频下载")
    ap.add_argument("input", nargs="+", help="抖音链接或分享文本")
    ap.add_argument("--info", action="store_true", help="只看信息不下载")
    ap.add_argument("--keep-cookies", action="store_true", help="顺便导出 cookies.txt")
    ap.add_argument("--out", default=None, help="输出目录，默认 ~/Downloads/抖音视频")
    args = ap.parse_args()

    urls = extract_urls(args.input)
    if not urls:
        bad("没在输入里找到抖音链接")
        info("支持的格式：v.douyin.com/xxx、douyin.com/video/数字ID")
        sys.exit(1)

    out_dir = args.out or os.path.expanduser("~/Downloads/抖音视频")
    print()
    print("=" * 62)
    print(f"  抖音视频下载  ·  共 {len(urls)} 条链接")
    print("=" * 62)
    print()

    binpath = find_browser()
    if not binpath:
        bad("找不到 Playwright 浏览器")
        info("请先执行: npx playwright install chromium")
        sys.exit(1)
    print(f"  浏览器: {os.path.basename(binpath)}")
    print()

    ok_count = 0
    results = []

    try:
        with Browser(binpath) as br:
            for idx, url in enumerate(urls, 1):
                print(f"[{idx}/{len(urls)}] {url}")
                vid = resolve_short_url(url)
                if not vid:
                    bad("拿不到 video_id，跳过")
                    results.append((url, None, "无 video_id"))
                    print()
                    continue
                print(f"      video_id = {vid}")

                detail = br.fetch_detail(vid)
                if not detail:
                    bad("没能拿到视频数据（可能 IP 限流或 headless 被识别）")
                    results.append((url, None, "抓取失败"))
                    print()
                    continue

                title = (detail.get("desc") or "(无标题)").strip()
                video = detail.get("video") or {}
                dur = video.get("duration")
                author = (detail.get("author") or {}).get("nickname")

                ok(f"标题：{title[:64]}")
                meta = []
                if author:
                    meta.append(f"作者 {author}")
                if dur:
                    meta.append(f"时长 {dur / 1000:.1f}s")
                if meta:
                    print(f"      {'  ·  '.join(meta)}")

                if args.keep_cookies:
                    n = write_cookies(br.cookies(), os.path.join(os.getcwd(), "cookies.txt"))
                    if n:
                        info(f"已导出 cookies.txt（{n} 条）")

                if args.info:
                    results.append((url, title, "仅信息"))
                    print()
                    continue

                mp4 = pick_mp4(detail)
                if not mp4:
                    bad("没找到 mp4 直链（可能是图文作品）")
                    results.append((url, title, "无直链"))
                    print()
                    continue

                os.makedirs(out_dir, exist_ok=True)
                dest = os.path.join(out_dir, f"{safe_name(title, vid)}.mp4")
                size = download(mp4, dest)
                if size:
                    ok(f"完成  {size / 1048576:.2f} MB")
                    print(f"      {dest}")
                    ok_count += 1
                    results.append((url, title, f"{size / 1048576:.2f} MB"))
                else:
                    bad("下载失败（CDN 直链有时效，可重跑）")
                    results.append((url, title, "下载失败"))
                print()
    except RuntimeError as e:
        bad(str(e))
        sys.exit(1)

    print("=" * 62)
    if args.info:
        print(f"  完成：{len(results)} 条（仅信息模式）")
    else:
        print(f"  成功 {ok_count}/{len(urls)} 条")
        if ok_count:
            print(f"  输出目录：{out_dir}")
    print("=" * 62)
    for url, title, status in results:
        print(f"  · {status:12} {title or url}")
    print()


if __name__ == "__main__":
    ensure_env()
    main()
