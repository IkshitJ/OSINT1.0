"""
End-to-end check of the running crowd monitor in a real (headless) browser.

    .venv/bin/python tests/browser_check.py                    # http://localhost:3000
    .venv/bin/python tests/browser_check.py --url http://host:3000 --out shot.png

It opens the dashboard at several window sizes (desktop, laptop, tablet, phone
upright and on its side) and at each one checks that the map finished loading,
the ghats have live numbers, and all four CCTV cameras are completely on
screen with a picture. It saves a screenshot of the first size and exits
non-zero if anything is wrong. Needs the server to be running (./run.sh).

One-time setup:  uv pip install --python .venv/bin/python -r requirements-dev.txt
                 && .venv/bin/playwright install chromium
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

# Software rendering in headless mode is slow; real browsers are ready in ~6 s.
MAP_READY_TIMEOUT_MS = 45000
WINDOW_SIZES = [(1800, 1000), (1280, 800), (1024, 700), (700, 900), (390, 844), (844, 390)]

# Where each camera tile sits, and whether it has a picture yet.
CAMERA_TILES = """() => [...document.querySelectorAll('.camera')].map((tile) => {
  const box = tile.getBoundingClientRect();
  const canvas = tile.querySelector('canvas');
  return {
    name: tile.querySelector('.camera-name').textContent,
    onScreen: box.top >= 0 && box.left >= 0 && box.bottom <= innerHeight + 1 && box.right <= innerWidth + 1,
    size: [Math.round(box.width), Math.round(box.height)],
    hasPicture: canvas.width > 300 && canvas.height > 150,
  };
})"""


def check_size(browser, url: str, width: int, height: int, screenshot_path: str | None) -> list:
    problems = []
    page = browser.new_page(viewport={"width": width, "height": height})
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    page.goto(url, wait_until="domcontentloaded")
    # The map sets data-state="ready" once it has flown to Ujjain and drawn every tile.
    try:
        page.wait_for_selector('#map[data-state="ready"]', timeout=MAP_READY_TIMEOUT_MS)
    except PlaywrightTimeout:
        state = page.get_attribute("#map", "data-state")
        problems.append(f"map did not finish loading within {MAP_READY_TIMEOUT_MS // 1000} s (state: {state})")
    page.wait_for_timeout(2500)  # the first camera pictures and live numbers

    tiles = page.evaluate(CAMERA_TILES)
    if len(tiles) != 4:
        problems.append(f"expected 4 camera tiles, found {len(tiles)}")
    for tile in tiles:
        if not tile["onScreen"]:
            problems.append(f"the {tile['name']} camera is not completely on screen")
        if min(tile["size"]) < 60:
            problems.append(f"the {tile['name']} camera is too small to read: {tile['size']}")
        if not tile["hasPicture"]:
            problems.append(f"the {tile['name']} camera has no picture")

    markers = page.locator(".site-marker-label b").all_inner_texts()
    if len(markers) != 4:
        problems.append(f"expected 4 ghat markers, found {len(markers)}")
    problems.extend(f"marker without gate data: {text!r}" for text in markers if "no gate data" in text)

    if not page.get_attribute("#sensor-frame", "src"):
        problems.append("the sensor simulation is not embedded")
    if page.inner_text("#gate-inside").strip() == "–":
        problems.append("the sensor panel shows no count")

    # The status pills are hidden on narrow screens; their state is still set.
    for source in ("sensor", "headcounter", "dashboard"):
        classes = page.get_attribute(f'.source-pill[data-source="{source}"]', "class") or ""
        if "online" not in classes:
            problems.append(f"{source} status pill is not green")

    traffic_button = page.locator("#map-controls button", has_text="Traffic")
    if "unavailable" in (traffic_button.get_attribute("class") or ""):
        problems.append("live traffic unavailable (no TOMTOM_API_KEY)")

    problems.extend(f"page error: {e}" for e in page_errors)

    if screenshot_path:
        page.screenshot(path=screenshot_path)
        print(f"Screenshot: {screenshot_path}")
        print("Ghats: " + "; ".join(markers))
        print(f"Alert log: {page.locator('#active-count').inner_text()}, "
              f"{page.locator('#alert-list li').count()} entries")
    page.close()
    return [f"[{width}x{height}] {problem}" for problem in problems]


def run(url: str, screenshot_path: str) -> list:
    problems = []
    with sync_playwright() as p:
        # SwiftShader gives headless Chromium the WebGL that MapLibre needs.
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        for index, (width, height) in enumerate(WINDOW_SIZES):
            found = check_size(browser, url, width, height, screenshot_path if index == 0 else None)
            print(f"{width}x{height}: {'ok' if not found else f'{len(found)} problem(s)'}")
            problems.extend(found)
        browser.close()
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--out", default="dashboard.png", help="where to save the screenshot")
    args = parser.parse_args()

    problems = run(args.url, args.out)
    if problems:
        print("FAILED:\n  - " + "\n  - ".join(problems))
        sys.exit(1)
    print("OK: map, sensor gates, all four cameras, live feeds, traffic and alert log working at every window size")


if __name__ == "__main__":
    main()
