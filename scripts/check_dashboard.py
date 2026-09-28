"""Browser checks for the review dashboard, including mobile and dark mode."""

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--output", type=Path, default=Path(".demo/public-review/screenshots"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, color_scheme="light")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto(args.url, wait_until="networkidle")
        page.locator("#repo-filter").fill("no-such-repository")
        assert page.locator("[data-filter-count]").inner_text() == "No matching repositories"
        page.locator("#repo-filter").fill("")
        page.screenshot(path=str(args.output / "registry-light.png"), full_page=True)
        page.get_by_role("link", name="Provenance", exact=True).click()
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Refresh status").click()
        expect(page.locator("[data-refresh-state]")).to_contain_text("Status refreshed")
        assert page.locator("h1").inner_text() == "Provenance"
        page.screenshot(path=str(args.output / "provenance-light.png"), full_page=True)
        page.get_by_role("button", name="Switch colour theme").click()
        assert page.locator("html").get_attribute("data-theme") == "dark"
        page.screenshot(path=str(args.output / "provenance-dark.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(args.output / "provenance-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile page overflows horizontally"
        page.goto(args.url + "/ops", wait_until="networkidle")
        assert page.locator("h1").count() == 1
        data = page.request.get(args.url + "/api/v1/log").json()
        assert data["entries"], "The prepared review needs a commit for browser proof verification"
        digest = data["entries"][0]["commit_hash"]
        page.goto(args.url + "/c/" + digest, wait_until="networkidle")
        page.get_by_role("button", name="Recompute in this browser").click()
        expect(page.locator("[data-check-state]")).to_have_class(re.compile(r"\bok\b"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Commit page overflows horizontally"
        page.evaluate("() => window.scrollTo(0, 0)")
        expect(page.locator("h1")).to_be_in_viewport()
        page.screenshot(path=str(args.output / "commit-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.evaluate("() => window.scrollTo(0, 0)")
        expect(page.locator("h1")).to_be_in_viewport()
        page.screenshot(path=str(args.output / "commit-dark.png"), full_page=True)
        browser.close()
    assert not errors, errors
    print(json.dumps({"browser": "Chrome", "checks": "navigation, filtering, refresh, light/dark, mobile overflow, browser proof recomputation, console", "status": "passed", "screenshots": str(args.output)}))


if __name__ == "__main__":
    main()
