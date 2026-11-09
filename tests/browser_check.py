"""
End-to-end check of the running dashboard in a real (headless) browser.

    .venv/bin/python tests/browser_check.py                    # http://localhost:3000
    .venv/bin/python tests/browser_check.py --url http://host:3000 --out shot.png

It opens the page, waits for the zoom to Ujjain to finish, checks every panel,
saves a screenshot, and exits non-zero if anything is wrong. Needs the
dashboard (and ideally Sensor and Headcounter) to be running.

One-time setup:  python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
                 && .venv/bin/playwright install chromium
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

# Software rendering in headless mode is slow; real browsers are ready in ~6 s.
MAP_READY_TIMEOUT_MS = 45000


def run(url: str, screenshot_path: str) -> list:
    problems = []

    with sync_playwright() as p:
        # SwiftShader gives headless Chromium the WebGL that MapLibre needs.
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1800, "height": 1000})

        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        page.goto(url, wait_until="domcontentloaded")
        # The map sets data-state="ready" once it has flown to Ujjain and drawn every tile.
        try:
            page.wait_for_selector('#map[data-state="ready"]', timeout=MAP_READY_TIMEOUT_MS)
        except PlaywrightTimeout:
            state = page.get_attribute("#map", "data-state")
            problems.append(f"map did not finish loading within {MAP_READY_TIMEOUT_MS // 1000} s (state: {state})")

        for name in ("sensor", "headcounter"):
            src = page.get_attribute(f"#{name}-frame", "src")
            if not src:
                problems.append(f"{name} app is not embedded")

        for source in ("sensor", "headcounter", "dashboard"):
            classes = page.get_attribute(f'.source-pill[data-source="{source}"]', "class") or ""
            if "online" not in classes:
                problems.append(f"{source} status pill is not green")

        traffic_button = page.locator("#map-controls button", has_text="Traffic")
        if "unavailable" in (traffic_button.get_attribute("class") or ""):
            problems.append("live traffic unavailable (no TOMTOM_API_KEY)")

        marker = page.locator(".site-marker-label").first.inner_text()
        if "no data" in marker:
            problems.append(f"Ram Ghat marker has no data: {marker!r}")

        problems.extend(f"page error: {e}" for e in page_errors)

        page.screenshot(path=screenshot_path)
        print(f"Screenshot: {screenshot_path}")
        print(f"Ram Ghat marker: {marker}")
        print(f"Alert log: {page.locator('#active-count').inner_text()}, "
              f"{page.locator('#alert-list li').count()} entries")
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
    print("OK: map, both apps, live feeds, traffic and alert log all working")


if __name__ == "__main__":
    main()
