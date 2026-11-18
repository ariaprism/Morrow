"""Build a separate, backend-free demo from the real application shell."""
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(output=None):
    target = Path(output) if output else ROOT / "dist"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shutil.copytree(ROOT / "static", target / "static")
    # This origin must never install the full application's service worker.
    (target / "static" / "sw.js").unlink()
    page = (ROOT / "static" / "index.html").read_text()
    page = re.sub(r'<script>\s*if \(\x27serviceWorker\x27 in navigator\).*?</script>', '', page, flags=re.S)
    page = page.replace('navigator.serviceWorker.register("/static/sw.js",{updateViaCache:"none"})', 'Promise.reject(new Error("演示模式不启用推送"))')
    page = page.replace('<title>Morrow</title>', '<title>Morrow · 演示小家</title>')
    page = page.replace('<head>', '<head>\n  <script src="/demo/fixtures.js"></script>\n  <script src="/demo/demo.js"></script>', 1)
    page = page.replace("</head>", '<link rel="stylesheet" href="/demo/demo.css"></head>', 1)
    # Do not inherit third-party font requests into the self-contained demo.
    page = re.sub(r'\s*<link[^>]+https://cdn\.jsdelivr\.net[^>]*>', '', page)
    page = page.replace('<body>', '<body>\n<aside id="demoBanner" aria-label="演示说明"><span><b>演示小家</b><span class="demo-detail"> · 虚构内容 · 修改仅存本浏览器 · 预设回复</span></span><button id="demoReset" type="button">重置演示</button></aside>', 1)
    (target / "index.html").write_text(page)
    # Retain one entry point only; never serve a second unadapted app shell.
    (target / "static" / "index.html").unlink()
    shutil.copytree(ROOT / "demo", target / "demo", ignore=shutil.ignore_patterns("README.md"))
    (target / "_headers").write_text("/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n  Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; media-src 'self' data: blob:; object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'\n/index.html\n  Cache-Control: no-cache\n/demo/*\n  Cache-Control: no-cache\n")
    (target / "_redirects").write_text("/static/index.html / 301\n")
    print(f"Demo built: {target}")
    return target


if __name__ == "__main__":
    build()
