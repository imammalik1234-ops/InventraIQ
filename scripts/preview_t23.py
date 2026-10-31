"""Database-free visual review of actual Jinja branding and shell interactions.

This is a component fixture, NOT authentication or operational workflow evidence.
It imports no application, forecasting, database or business service modules.
"""

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


PROJECT = Path(__file__).resolve().parents[1]


def render_shells():
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    environment = Environment(loader=FileSystemLoader(PROJECT / "templates"),
                              autoescape=select_autoescape(("html",)))
    from services.planning_presentation import branch_name, missing_inputs
    environment.filters.update(branch_name=branch_name,planning_inputs=missing_inputs)

    def local_url(endpoint, **values):
        if endpoint == "static":
            return (PROJECT / "static" / values["filename"]).as_uri()
        return "#preview-" + endpoint.replace(".", "-")

    environment.globals.update(url_for=local_url, csrf_token=lambda: "component-fixture-not-a-session",
                               get_flashed_messages=lambda **kwargs: [])
    context = {
        "current_user": SimpleNamespace(is_authenticated=True),
        "authz_role": "hq_admin", "can_adjust_stock": True, "can_manage_catalog": True,
        "request": SimpleNamespace(endpoint="admin_dashboard", path="/preview", form={}),
        "profile_name": "Component preview", "profile_initials": "IQ", "login_role": "admin",
    }
    login = environment.from_string(
        '{% extends "auth-base.html" %}{% block content %}'
        '{% include "pages/login.html" %}{% endblock %}').render(context)
    workspace = environment.from_string(
        '{% extends "base.html" %}{% block content %}'
        '<h1>Brand integration preview</h1><p>Component fixture only. '
        'No authentication, database or business services are connected.</p>'
        '{% endblock %}').render(context)
    return {"login": login, "sidebar": workspace}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before", "after"), default="after")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    from scripts.verify_t23 import check_brand_images, measure_symbol

    output = PROJECT / "data/private/t23"
    output.mkdir(parents=True, exist_ok=True)
    shells = render_shells()
    results = {"phase": args.phase, "component_fixture_only": True, "database_access": False,
               "browsers": {}}
    for name, content in shells.items():
        (output / f"{args.phase}-{name}-fixture.html").write_text(content, encoding="utf-8")
    with sync_playwright() as pw:
        for name, executable in (
            ("chrome", Path("C:/Program Files/Google/Chrome/Application/chrome.exe")),
            ("edge", Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")),
        ):
            if not executable.is_file() or args.phase == "before" and name != "chrome":
                continue
            browser = pw.chromium.launch(executable_path=str(executable), headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.route("**/*", lambda route: route.continue_()
                       if route.request.url.startswith("file:") else route.abort())
            errors, checks, images = [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for shell in shells:
                for width in (1440, 320):
                    page.set_viewport_size({"width": width, "height": 1000})
                    page.goto((output / f"{args.phase}-{shell}-fixture.html").as_uri())
                    page.wait_for_load_state("load")
                    if shell == "sidebar" and width == 320:
                        page.get_by_role("button", name="Open navigation", exact=True).click()
                        page.wait_for_timeout(250)
                    assert page.evaluate("document.documentElement.scrollWidth") == width
                    if args.phase == "after":
                        images.extend(check_brand_images(page))
                        link = page.get_by_role("link", name="InventraIQ", exact=True)
                        assert link.count() == 1
                        page.keyboard.press("Tab")
                        link.focus()
                        assert link.evaluate("el => el === document.activeElement")
                        focus = link.evaluate("el => ({style: getComputedStyle(el).outlineStyle, width: getComputedStyle(el).outlineWidth})")
                        assert focus["style"] != "none" and float(focus["width"][:-2]) >= 2
                        if shell == "login" and width == 320:
                            gap = page.locator(".auth-top").evaluate("""el =>
                                el.querySelector('.text-link').getBoundingClientRect().left -
                                el.querySelector('.brand').getBoundingClientRect().right""")
                            assert gap >= 6, f"Brand focus outline overlaps adjacent link: {gap}"
                            results.setdefault("mobile_link_gap", {})[name] = gap
                        assert page.locator('link[rel="icon"]').get_attribute("href").endswith("inventraiq-favicon.svg")
                        if shell == "sidebar" and width == 320:
                            sidebar = page.locator("#primary-sidebar")
                            for _ in range(24):
                                page.keyboard.press("Tab")
                                assert sidebar.evaluate("el => el.contains(document.activeElement)")
                            page.keyboard.press("Escape")
                            assert page.locator("[data-nav-toggle]").evaluate("el => el === document.activeElement")
                            page.locator("[data-nav-toggle]").click()
                            page.wait_for_timeout(250)
                        page.emulate_media(reduced_motion="reduce")
                        assert page.locator(".brand-logo:visible").evaluate(
                            "el => getComputedStyle(el).animationName") == "none"
                        page.emulate_media(reduced_motion="no-preference")
                        page.emulate_media(media="print")
                        page.wait_for_function("""() => Array.from(document.querySelectorAll('.brand-logo')).every(
                            img => img.currentSrc.endsWith('inventraiq-logo-dark.svg') && img.complete && img.naturalWidth > 0)""")
                        page.emulate_media(media="screen")
                        page.wait_for_function("""() => Array.from(document.querySelectorAll('.brand-logo')).every(
                            img => img.currentSrc.endsWith('inventraiq-logo.svg') && img.complete && img.naturalWidth > 0)""")
                        checks.append(f"{shell}/{width}: loaded/named brand, focus, favicon, bounded layout, no animation")
                    page.screenshot(path=str(output / f"{args.phase}-{name}-{shell}-{width}-fixture.png"), full_page=True)
            if args.phase == "after":
                page.set_viewport_size({"width": 1200, "height": 1400})
                page.goto((PROJECT / "docs/branding/preview.html").as_uri())
                page.wait_for_function("Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)")
                page.screenshot(path=str(output / f"{name}-brand-comparison.png"), full_page=True)
                results["raster_samples"] = measure_symbol(page, PROJECT, output)
                page.emulate_media(media="print")
                page.screenshot(path=str(output / f"{name}-brand-comparison-print.png"), full_page=True)
                page.goto((PROJECT / "static/images/inventraiq-logo.svg").as_uri())
                extent = page.locator("text").evaluate("""el => {
                    const box = el.getBBox();
                    return {left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height};
                }""")
                assert extent["right"] <= 168 and extent["bottom"] <= 32 and extent["top"] >= 0, extent
                results.setdefault("wordmark_bounds", {})[name] = extent
            assert not errors, errors
            results["browsers"][name] = {"version": browser.version, "checks": checks, "images": images,
                                          "javascript_errors": errors}
            browser.close()
    if not results["browsers"]:
        raise RuntimeError("No installed browser; no download attempted")
    (output / f"{args.phase}-components.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
