"""Record the README demo: boots the app on a scratch DB, drives it with
Playwright, then encodes docs/demo.mp4 and docs/demo.gif with ffmpeg.

    pip install playwright && playwright install chromium
    python scripts/record_demo.py
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
W, H = 1440, 900

CURSOR_JS = """
const c = document.createElement('div');
c.style.cssText = 'position:fixed;z-index:9999;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;'
  + 'background:rgba(35,78,122,.35);border:2px solid #234e7a;pointer-events:none;transition:transform .12s;left:-40px;top:-40px';
document.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
document.addEventListener('mousedown', () => c.style.transform = 'scale(.7)', true);
document.addEventListener('mouseup', () => c.style.transform = 'scale(1)', true);
window.addEventListener('DOMContentLoaded', () => document.body.appendChild(c));
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    port = free_port()
    tmp = Path(tempfile.mkdtemp())
    env = os.environ | {"UNDERWRITEIQ_DB": str(tmp / "demo.db")}
    env.pop("ANTHROPIC_API_KEY", None)  # deterministic recording
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "underwriteiq.api:app",
                               "--port", str(port)], env=env, cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        record(f"http://127.0.0.1:{port}/", tmp)
    finally:
        server.terminate()
    return 0


def record(url: str, tmp: Path) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1,
                                  record_video_dir=str(tmp), record_video_size={"width": W, "height": H})
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        page.goto(url)
        page.wait_for_selector("#trace tr")
        page.wait_for_timeout(600)
        mouse = Mouse(page)

        # 1. clean risk accepts; screenshot for README
        page.screenshot(path=str(DOCS / "desk.png"))
        page.wait_for_timeout(900)
        # 2. make it a new venture -> refer
        mouse.drag_range("#f-years", 1)
        page.wait_for_timeout(1200)
        # 3. ask for a $10M tower -> decline
        mouse.click("#f-limit button[data-v='10000000']")
        page.wait_for_timeout(1400)
        # 4. a messy risk: full trace + verified memo
        mouse.click("#queue button[data-id='SYN-2026-0002']")
        page.wait_for_timeout(1000)
        mouse.scroll_to("#trace")
        page.wait_for_timeout(1300)
        mouse.click("#draft")
        page.wait_for_timeout(700)
        mouse.scroll_to("#memo-body")
        page.wait_for_timeout(2200)
        page.screenshot(path=str(DOCS / "memo.png"))
        # 5. incomplete submission
        mouse.scroll_top()
        mouse.click("#queue button[data-id='SYN-2026-0003']")
        page.wait_for_timeout(1800)
        # 6. portfolio
        mouse.click(".tab[data-view='portfolio']")
        page.wait_for_selector("#tiles .tile")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(DOCS / "portfolio.png"))
        mouse.wheel(700)
        page.wait_for_timeout(1500)
        mouse.scroll_top()
        # 7. simulator
        mouse.click(".tab[data-view='simulator']")
        page.wait_for_selector("#compare .v")
        page.wait_for_timeout(1000)
        mouse.click("[data-preset='authority']")
        page.wait_for_timeout(1600)
        mouse.click("[data-preset='open']")
        page.wait_for_timeout(1900)
        page.screenshot(path=str(DOCS / "simulator.png"))
        mouse.click("[data-preset='tighten']")
        page.wait_for_timeout(1600)
        mouse.drag_range("#s-threshold", 25)
        page.wait_for_timeout(1800)

        video = page.video.path()
        ctx.close()
        browser.close()
    encode(Path(video))


class Mouse:
    """Moves a visible cursor between targets so the recording reads like a person."""

    def __init__(self, page):
        self.page, self.x, self.y = page, W / 2, H / 2

    def move(self, x, y, steps=18):
        self.page.mouse.move(x, y, steps=steps)
        self.x, self.y = x, y

    def click(self, selector):
        el = self.page.locator(selector).first
        el.scroll_into_view_if_needed()
        b = el.bounding_box()
        self.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        self.page.wait_for_timeout(120)
        self.page.mouse.click(self.x, self.y)

    def drag_range(self, selector, value):
        el = self.page.locator(selector)
        el.scroll_into_view_if_needed()
        b = el.bounding_box()
        lo, hi, cur = (float(el.get_attribute("min")), float(el.get_attribute("max")),
                       float(el.input_value()))
        to_x = lambda v: b["x"] + 8 + (b["width"] - 16) * (v - lo) / (hi - lo)  # noqa: E731
        y = b["y"] + b["height"] / 2
        self.move(to_x(cur), y)
        self.page.mouse.down()
        self.move(to_x(value), y, steps=30)
        self.page.mouse.up()

    def wheel(self, dy, steps=14):
        for _ in range(steps):
            self.page.mouse.wheel(0, dy / steps)
            self.page.wait_for_timeout(25)

    def scroll_to(self, selector):
        top = self.page.locator(selector).evaluate("e => e.getBoundingClientRect().top")
        self.wheel(top - 90)

    def scroll_top(self):
        self.wheel(-self.page.evaluate("scrollY"))


def encode(video: Path) -> None:
    ff = shutil.which("ffmpeg")
    if not ff:
        print(f"ffmpeg not found; raw video at {video}")
        return
    mp4, gif = DOCS / "demo.mp4", DOCS / "demo.gif"
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(video), "-vf", "fps=30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24", "-movflags", "+faststart",
                    str(mp4)], check=True)
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(video), "-vf",
                    "fps=8,scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=48:stats_mode=diff[p];"
                    "[b][p]paletteuse=dither=none:diff_mode=rectangle", str(gif)], check=True)
    print(f"wrote {mp4} ({mp4.stat().st_size // 1024} KB) and {gif} ({gif.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    raise SystemExit(main())
