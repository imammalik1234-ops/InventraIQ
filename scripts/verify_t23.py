"""Capture T23 branding on a new disposable synthetic demo, never an existing DB."""

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before", "after"), default="after")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright
    from werkzeug.serving import WSGIRequestHandler, make_server
    from database import db
    from ingestion.target import ACK
    from simulation.runtime import create_app, initialize
    from simulation.runner import run
    from simulation.verify import verify

    project = Path(__file__).resolve().parents[1]
    output = project / "data/private/t23"
    output.mkdir(parents=True, exist_ok=True)
    installed = [(name, path) for name, path in (
        ("chrome", Path("C:/Program Files/Google/Chrome/Application/chrome.exe")),
        ("edge", Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")),
    ) if path.is_file()]
    if not installed:
        raise RuntimeError("No installed browser; this script does not download browsers")

    class Quiet(WSGIRequestHandler):
        def log_request(self, *args, **kwargs):
            pass

    with TemporaryDirectory(prefix="inventraiq-t23-browser-") as temp:
        root = initialize(temp, acknowledgment=ACK)
        app = create_app(root)
        run(app)
        initial = verify(app)
        private = json.loads((root / "simulation-private.json").read_text())
        server = make_server("127.0.0.1", 0, app, request_handler=Quiet)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        results = {"phase": args.phase, "synthetic_only": True, "browsers": {}}
        try:
            with sync_playwright() as pw:
                for name, executable in installed[:1] if args.phase == "before" else installed:
                    browser = pw.chromium.launch(executable_path=str(executable), headless=True)
                    context = browser.new_context(viewport={"width": 1440, "height": 1000})
                    context.route("**/*", lambda route: route.continue_()
                                  if route.request.url.startswith(origin + "/") else route.abort())
                    page = context.new_page()
                    errors, images, checks = [], [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def capture(label):
                        page.wait_for_load_state("networkidle")
                        page.screenshot(path=str(output / f"{args.phase}-{name}-{label}.png"), full_page=True)

                    page.goto(origin + "/login/admin")
                    capture("login-desktop")
                    if args.phase == "after":
                        images.extend(check_brand_images(page))
                        assert page.get_by_role("link", name="InventraIQ", exact=True).count() == 1
                        assert page.locator("a.brand").count() == 2
                        assert page.locator('link[rel="icon"]').get_attribute("href").endswith("inventraiq-favicon.svg")
                    mobile = context.new_page()
                    mobile.set_viewport_size({"width": 320, "height": 844})
                    mobile.goto(origin + "/login/admin")
                    mobile.wait_for_load_state("networkidle")
                    assert mobile.evaluate("document.documentElement.scrollWidth") == 320
                    mobile.screenshot(path=str(output / f"{args.phase}-{name}-login-320.png"), full_page=True)
                    if args.phase == "after":
                        check_brand_images(mobile)
                        link = mobile.get_by_role("link", name="InventraIQ", exact=True)
                        assert link.count() == 1
                        link.focus()
                        assert link.evaluate("el => el === document.activeElement")
                    mobile.close()
                    page.get_by_label("Email address").fill("synthetic-901@demo.invalid")
                    page.locator("#password").fill(private["credentials"]["901"])
                    page.get_by_role("button", name="Sign in to workspace", exact=True).click()
                    page.wait_for_load_state("networkidle")
                    capture("sidebar-desktop")
                    if args.phase == "after":
                        images.extend(check_brand_images(page))
                        brand = page.get_by_role("link", name="InventraIQ", exact=True)
                        page.keyboard.press("Tab")
                        brand.focus()
                        assert brand.evaluate("el => el === document.activeElement")
                        assert brand.evaluate("el => getComputedStyle(el).outlineStyle") != "none"
                    page.set_viewport_size({"width": 320, "height": 844})
                    page.goto(origin + "/admin-dashboard")
                    page.get_by_role("button", name="Open navigation", exact=True).click()
                    sidebar = page.locator("#primary-sidebar")
                    assert sidebar.evaluate("el => el.contains(document.activeElement)")
                    page.wait_for_timeout(250)
                    capture("navigation-320")
                    if args.phase == "after":
                        images.extend(check_brand_images(page))
                        assert page.evaluate("document.documentElement.scrollWidth") == 320
                        brand = sidebar.get_by_role("link", name="InventraIQ", exact=True)
                        brand.focus()
                        page.keyboard.press("Enter")
                        page.wait_for_load_state("networkidle")
                        assert page.url.endswith("/admin-dashboard")
                        page.get_by_role("button", name="Open navigation", exact=True).click()
                        page.keyboard.press("Escape")
                        assert page.get_by_role("button", name="Open navigation", exact=True).evaluate(
                            "el => el === document.activeElement")
                        page.emulate_media(reduced_motion="reduce")
                        assert page.locator(".brand-logo").evaluate(
                            "el => getComputedStyle(el).animationName") == "none"
                        checks.extend(["Login and sidebar accessible brand links/images loaded",
                                       "320px login and navigation remain bounded and named",
                                       "Keyboard brand link, mobile Escape and opener focus return",
                                       "Favicon reference is local and new; no logo animation"])
                    assert not errors, errors
                    results["browsers"][name] = {"version": browser.version, "checks": checks,
                                                  "images": images, "javascript_errors": errors}
                    browser.close()

                if args.phase == "after":
                    browser = pw.chromium.launch(executable_path=str(installed[0][1]), headless=True)
                    page = browser.new_page(viewport={"width": 1200, "height": 1400})
                    page.goto((project / "docs/branding/preview.html").as_uri())
                    page.wait_for_function("Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)")
                    page.screenshot(path=str(output / "brand-comparison.png"), full_page=True)
                    results["preview_images"] = page.locator("img").count()
                    results["raster_samples"] = measure_symbol(page, project, output)
                    page.emulate_media(media="print")
                    page.screenshot(path=str(output / "brand-comparison-print.png"), full_page=True)
                    browser.close()
            assert verify(app) == initial, "Brand verification changed synthetic business facts"
            results["business_facts_unchanged"] = True
            (output / f"{args.phase}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        print(json.dumps(results, indent=2))


def check_brand_images(page):
    return page.locator(".brand-logo:visible").evaluate_all("""images => images.map(img => {
        const rect = img.getBoundingClientRect();
        if (!img.complete || !img.naturalWidth || img.alt !== 'InventraIQ') throw new Error('Brand image missing/name');
        if (rect.width <= 0 || rect.height < 24 || rect.right > innerWidth + 1) throw new Error('Brand clipped');
        return {width: rect.width, height: rect.height, source: img.getAttribute('src'), alt: img.alt};
    })""")


def measure_symbol(page, project, output):
    """Inspect actual SVG raster pixels, not only the vector source dimensions."""
    import base64
    measurements = []
    for size in (16, 24, 32, 64):
        for variant in ("inventraiq", "inventraiq-symbol-dark", "inventraiq-symbol-light", "inventraiq-favicon"):
            path = project / f"static/images/{variant}.svg"
            image = page.locator("#raster-sample")
            image.evaluate("""async (el, settings) => {
                el.style.visibility = 'visible'; el.width = settings.size; el.height = settings.size;
                await new Promise((resolve, reject) => {el.onload = resolve; el.onerror = reject; el.src = settings.url;});
            }""", {"url": path.as_uri(), "size": size})
            background = "#192d28" if variant.endswith("light") else "#ffffff"
            image.evaluate("(el, color) => el.style.backgroundColor = color", background)
            target = output / f"{variant}-{size}.png"
            image.screenshot(path=str(target))
            # Aperture center must stay open, and solid shelf pixels must remain.
            samples = page.evaluate("""async settings => {
                const image = new Image();
                await new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject; image.src = settings.url;});
                const canvas = document.createElement('canvas');
                canvas.width = canvas.height = settings.size;
                const context = canvas.getContext('2d');
                context.fillStyle = settings.background;
                context.fillRect(0, 0, settings.size, settings.size);
                context.drawImage(image, 0, 0, settings.size, settings.size);
                const pixel = (x, y) => Array.from(context.getImageData(x, y, 1, 1).data).slice(0, 3);
                return {center: pixel(Math.floor(settings.size / 2), Math.floor(settings.size / 2)),
                    shelf: pixel(Math.floor(settings.size / 2), Math.floor(settings.size * 6 / 32))};
            }""", {"url": "data:image/svg+xml;base64," + base64.b64encode(path.read_bytes()).decode("ascii"),
                    "size": size, "background": background})
            center, shelf = samples["center"], samples["shelf"]
            assert center != shelf, (variant, size, center, shelf)
            measurements.append({"variant": variant, "size": size, "center_rgb": center, "shelf_rgb": shelf})
    return measurements


if __name__ == "__main__":
    main()
