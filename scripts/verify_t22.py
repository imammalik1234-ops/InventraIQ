"""T22 visual/accessibility evidence on a newly generated disposable demo.

No existing database or saved demo can be supplied. Credentials remain private.
"""

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def contrast_ratio(foreground, background):
    """WCAG 2 relative luminance for opaque RGB channels (0..255)."""
    def luminance(channels):
        values = [channel / 255 for channel in channels]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                  for value in values]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    values = sorted((luminance(foreground), luminance(background)))
    return (values[1] + .05) / (values[0] + .05)


COLORS = """(selector) => {
    const element = document.querySelector(selector);
    if (!element || !element.getClientRects().length) return null;
    const rgb = color => color.match(/[\\d.]+/g).map(Number);
    const over = (front, back) => {
        const alpha = front.length > 3 ? front[3] : 1;
        return front.slice(0, 3).map((channel, index) => channel * alpha + back[index] * (1 - alpha));
    };
    const background = node => {
        if (!node) return [255, 255, 255];
        return over(rgb(getComputedStyle(node).backgroundColor), background(node.parentElement));
    };
    const style = getComputedStyle(element), bg = background(element);
    return {color: over(rgb(style.color), bg), background: bg,
        border: over(rgb(style.borderTopColor), bg), outline: over(rgb(style.outlineColor), bg),
        font_size: parseFloat(style.fontSize), font_weight: parseInt(style.fontWeight),
        outline_style: style.outlineStyle, outline_width: parseFloat(style.outlineWidth)};
}"""


def measure_contrast(page):
    results = []
    for selector in (".button-primary", ".button-secondary", ".button-quiet", ".breadcrumb",
                     ".page-description", ".eyebrow", ".section-meta", ".text-muted",
                     ".product-meta", ".field-error", ".notice", ".notice.danger", ".notice.success",
                     ".badge.emerald", ".badge.amber", ".badge.danger", "input:not([type=hidden])", "select"):
        value = page.evaluate(COLORS, selector)
        if value is None:
            continue
        ratio = contrast_ratio(value["color"], value["background"])
        large = value["font_size"] >= 24 or value["font_size"] >= 18.66 and value["font_weight"] >= 700
        minimum = 3 if large else 4.5
        results.append({"selector": selector, "ratio": round(ratio, 3), "minimum": minimum,
                        "foreground_rgb": value["color"], "background_rgb": value["background"]})
        assert ratio >= minimum, f"Text contrast {selector}: {ratio:.3f} < {minimum}"
        if selector in ("input:not([type=hidden])", "select"):
            boundary = contrast_ratio(value["border"], value["background"])
            assert boundary >= 3, f"Control boundary {selector}: {boundary:.3f} < 3"
            results.append({"selector": selector + " boundary", "ratio": round(boundary, 3), "minimum": 3,
                            "foreground_rgb": value["border"], "background_rgb": value["background"]})
    return results


def form_semantics(page):
    return page.evaluate("""() => {
        const missing = [];
        document.querySelectorAll('input:not([type=hidden]), select, textarea').forEach(control => {
            if (!control.labels?.length && !control.getAttribute('aria-label') && !control.getAttribute('aria-labelledby'))
                missing.push(control.outerHTML.substring(0, 120));
        });
        return missing;
    }""")


def wait_for_mobile_capture(page):
    """Let the existing off-canvas transition settle before taking evidence."""
    page.wait_for_function("""() => !document.body.classList.contains('nav-open') &&
        document.querySelector('[data-nav-toggle]')?.getAttribute('aria-expanded') === 'false' &&
        (!document.getElementById('primary-sidebar') ||
         document.getElementById('primary-sidebar').getBoundingClientRect().right <= 1)""", timeout=5000)


def check_keyboard(page, origin, checks):
    page.goto(origin + "/admin-dashboard")
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("class") == "skip-link"
    page.keyboard.press("Enter")
    assert page.locator("#main-content").evaluate("el => el === document.activeElement")
    checks.append("Skip link is first keyboard stop and focuses the main landmark")

    opener = page.locator("[data-assistant-open]").first
    opener.focus()
    page.keyboard.press("Enter")
    dialog = page.locator("#assistant-panel")
    assert dialog.evaluate("el => el.open && el.contains(document.activeElement)")
    for index in range(8):
        page.keyboard.press("Tab")
        assert dialog.evaluate("el => el.contains(document.activeElement)"), {
            "tab": index + 1, "active": page.evaluate("document.activeElement.outerHTML.substring(0, 240)")}
    page.keyboard.press("Escape")
    assert not dialog.evaluate("el => el.open")
    assert opener.evaluate("el => el === document.activeElement")
    opener.click()
    page.locator("[data-assistant-close]").click()
    assert opener.evaluate("el => el === document.activeElement")
    checks.append("Native assistant preview dialog contains keyboard focus; Escape/close return focus to opener")

    page.set_viewport_size({"width": 320, "height": 844})
    toggle = page.locator("[data-nav-toggle]")
    assert toggle.get_attribute("aria-label")
    assert opener.get_attribute("aria-label") or opener.inner_text().strip()
    toggle.focus()
    page.keyboard.press("Enter")
    assert toggle.get_attribute("aria-expanded") == "true"
    sidebar = page.locator("#primary-sidebar")
    assert sidebar.evaluate("el => el.contains(document.activeElement)")
    assert page.locator(".workspace-body").evaluate("el => el.inert")
    for _ in range(25):
        page.keyboard.press("Tab")
        assert sidebar.evaluate("el => el.contains(document.activeElement)")
    page.keyboard.press("Escape")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert toggle.evaluate("el => el === document.activeElement")
    assert sidebar.evaluate("el => el.inert")
    toggle.click()
    page.locator("#primary-sidebar [data-nav-close]").click()
    assert toggle.evaluate("el => el === document.activeElement")
    toggle.click()
    page.set_viewport_size({"width": 1440, "height": 1000})
    assert not sidebar.evaluate("el => el.inert")
    assert not page.locator(".workspace-body").evaluate("el => el.inert")
    checks.append("320px mobile navigation contains focus, hides background from interaction, closes/restores focus, and resets on desktop resize")

    page.goto(origin + "/purchasing?order_id=2&branch_id=2")
    details = page.locator("details").first
    details.locator("summary").focus()
    page.keyboard.press("Enter")
    assert details.evaluate("el => el.open")
    checks.append("Supplier edit disclosure opens with keyboard Enter")


def check_layout(page, origin, name, output):
    results = []
    for width, label in ((1440, "desktop"), (900, "tablet"), (640, "200-percent-equivalent"), (320, "400-percent-equivalent")):
        page.set_viewport_size({"width": width, "height": 1000})
        for section, path in (("inventory", "/admin-inventory"), ("sales", "/branch-sales?sale_id=1&branch_id=1"),
                              ("transfer", "/transfers?transfer_id=1&branch_id=2"),
                              ("purchasing", "/purchasing?order_id=2&branch_id=2")):
            assert page.goto(origin + path).status == 200
            assert not form_semantics(page), form_semantics(page)
            actual = page.evaluate("document.documentElement.scrollWidth")
            assert actual <= width + 2, f"{section} document width {actual} at {width}"
            targets = page.locator("button, .button").evaluate_all("""elements => elements
                .filter(el => el.getClientRects().length && !el.disabled)
                .map(el => ({name: el.getAttribute('aria-label') || el.textContent.trim(),
                    width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height}))""")
            assert all(target["width"] >= 24 and target["height"] >= 24 for target in targets), targets
            for region in page.locator(".table-scroll").all():
                table = region.locator("table")
                assert table.locator("thead th").first.evaluate("el => getComputedStyle(el).position") == "sticky"
                identities = table.locator('tbody th[scope="row"]')
                if width >= 761 and identities.count():
                    assert identities.first.evaluate("el => getComputedStyle(el).position") == "sticky"
            if width == 320:
                for region in page.locator(".table-scroll").all():
                    assert region.get_attribute("tabindex") == "0"
                    assert region.get_attribute("role") == "region"
                    assert region.get_attribute("aria-label") or region.get_attribute("aria-labelledby")
                    table = region.locator("table")
                    assert table.locator("caption").count() == 1
                    assert table.locator("thead th:not([scope])").count() == 0
                    if region.evaluate("el => el.scrollWidth > el.clientWidth + 2"):
                        region.focus()
                        page.keyboard.press("ArrowRight")
                        page.wait_for_timeout(150)
                        assert region.evaluate("el => el.scrollLeft > 0")
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(output / f"after-{name}-{section}-320.png"), full_page=True)
            results.append({"screen": section, "css_viewport_width": width, "document_width": actual, "condition": label,
                            "smallest_button_width": round(min(target["width"] for target in targets), 2),
                            "smallest_button_height": round(min(target["height"] for target in targets), 2),
                            "tables": page.locator(".table-scroll table").evaluate_all("""tables => tables.map(table => {
                                const cell = table.querySelector('tbody td, tbody th');
                                const row = table.querySelector('tbody tr');
                                return {caption: table.caption?.textContent.trim(),
                                    cell_font_size: cell ? getComputedStyle(cell).fontSize : null,
                                    header_font_size: getComputedStyle(table.querySelector('thead th')).fontSize,
                                    first_row_height: row ? row.getBoundingClientRect().height : null};
                            })""")})
    return results


def check_security(browser, page, origin, private, checks):
    form = page.get_by_role("form", name="Switch branch", exact=True)
    form.locator("select").select_option("1")
    form.get_by_role("button", name="Switch", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert page.request.get(origin + "/api/operations").json()["total"]["on_hand"] == 215
    assert page.request.post(origin + "/scope", form={"branch_id": "all"}).status == 400
    checks.append("Persistent branch selector retains server validation and rejects missing CSRF")
    for uid, portal in ((903, "admin"), (912, "staff"), (922, "staff")):
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.route("**/*", lambda route: route.continue_()
                      if route.request.url.startswith(origin + "/") else route.abort())
        role_page = context.new_page()
        role_errors = []
        role_page.on("pageerror", lambda error: role_errors.append(str(error)))
        role_page.goto(origin + "/login/" + portal)
        role_page.get_by_label("Email address").fill(f"synthetic-{uid}@demo.invalid")
        role_page.locator("#password").fill(private["credentials"][str(uid)])
        role_page.get_by_role("button", name="Sign in to workspace", exact=True).click()
        role_page.wait_for_load_state("networkidle")
        csrf = role_page.locator('meta[name="csrf-token"]').get_attribute("content")
        if uid == 903:
            assert role_page.request.get(origin + "/api/operations").json()["total"]["on_hand"] == 304
            assert role_page.request.post(origin + "/api/purchase-orders", data={}, headers={"X-CSRF-Token": csrf}).status == 403
        else:
            assert role_page.request.get(origin + "/api/operations").json()["total"]["on_hand"] == 17
            assert role_page.request.get(origin + "/api/operations?branch_id=1").status == 403
            assert role_page.request.get(origin + "/branch-sales?sale_id=1&branch_id=2").status in (403, 404)
            assert role_page.request.post(origin + "/scope", form={"branch_id": "1", "csrf_token": csrf}).status == 403
            if uid == 912:
                assert role_page.goto(origin + "/purchasing?order_id=2&branch_id=2").status == 200
                assert "PartiallyReceived" in role_page.locator("main").inner_text()
            else:
                assert role_page.request.get(origin + "/purchasing").status == 403
                assert role_page.request.get(origin + "/transfers").status == 403
        assert not role_errors, role_errors
        context.close()
    checks.append("Real HQ analyst, manager and clerk logins preserve scoped records, non-disclosing resource denial and purchasing/transfer capabilities")
    page.goto(origin + "/admin-dashboard")
    form = page.get_by_role("form", name="Switch branch", exact=True)
    form.locator("select").select_option("all")
    form.get_by_role("button", name="Switch", exact=True).click()
    page.wait_for_load_state("networkidle")


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

    output = Path(__file__).resolve().parents[1] / "data/private/t22"
    output.mkdir(parents=True, exist_ok=True)
    executables = [
        ("chrome", Path("C:/Program Files/Google/Chrome/Application/chrome.exe")),
        ("edge", Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")),
    ]
    available = [(name, executable) for name, executable in executables if executable.is_file()]
    if not available:
        raise RuntimeError("No installed browser; no browser is downloaded by this script")

    class Quiet(WSGIRequestHandler):
        def log_request(self, *args, **kwargs):
            pass

    with TemporaryDirectory(prefix="inventraiq-t22-browser-") as temp:
        root = initialize(temp, acknowledgment=ACK)
        app = create_app(root)
        run(app)
        initial = verify(app)
        private = json.loads((root / "simulation-private.json").read_text())
        server = make_server("127.0.0.1", 0, app, threaded=False, request_handler=Quiet)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        measurements = {"phase": args.phase, "browsers": {}, "synthetic_only": True}
        try:
            with sync_playwright() as pw:
                for name, executable in available[:1] if args.phase == "before" else available:
                    browser = pw.chromium.launch(executable_path=str(executable), headless=True)
                    context = browser.new_context(viewport={"width": 1440, "height": 1000})
                    context.route("**/*", lambda route: route.continue_()
                                  if route.request.url.startswith(origin + "/") else route.abort())
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(origin + "/login/admin")
                    checks = []
                    contrast = []
                    zoom_probe = None
                    focus_evidence = None
                    if args.phase == "after":
                        metric = "({width: innerWidth, pixel_ratio: devicePixelRatio, visual_scale: visualViewport.scale})"
                        original_zoom = page.evaluate(metric)
                        for _ in range(6):
                            page.keyboard.press("Control+Equal")
                        observed_zoom = page.evaluate(metric)
                        page.keyboard.press("Control+0")
                        zoom_probe = {"before": original_zoom, "after_six_ctrl_plus": observed_zoom,
                                      "native_zoom_verified": False,
                                      "limitation": "Headless shortcut probe does not establish native 200%/400% browser zoom; layout reflow is tested separately at equivalent CSS widths"}
                        page.get_by_role("button", name="Sign in to workspace", exact=True).click()
                        email = page.locator("#email")
                        assert email.get_attribute("aria-invalid") == "true"
                        descriptions = (email.get_attribute("aria-describedby") or "").split()
                        assert descriptions and all(page.locator("#" + ident).inner_text().strip() for ident in descriptions)
                        checks.append("Native required validation is associated with visible field error text without submitting credentials")
                        contrast.extend({"screen": "login validation", **result} for result in measure_contrast(page))
                        page.screenshot(path=str(output / f"after-{name}-login-validation.png"), full_page=True)
                    page.get_by_label("Email address").fill("synthetic-901@demo.invalid")
                    page.locator("#password").fill(private["credentials"]["901"])
                    page.get_by_role("button", name="Sign in to workspace", exact=True).click()
                    page.wait_for_load_state("networkidle")
                    if args.phase == "after":
                        check_keyboard(page, origin, checks)
                        layout = check_layout(page, origin, name, output)
                        page.set_viewport_size({"width": 1440, "height": 1000})
                        for screen, path in (("overview", "/admin-dashboard"), ("purchasing", "/purchasing?order_id=2&branch_id=2"),
                                             ("receiving", "/receiving?branch_id=2"), ("sales", "/branch-sales?sale_id=1&branch_id=1")):
                            assert page.goto(origin + path).status == 200
                            contrast.extend({"screen": screen, **result} for result in measure_contrast(page))
                        page.locator("[data-assistant-open]").focus()
                        focus = page.evaluate(COLORS, "[data-assistant-open]")
                        assert focus["outline_style"] != "none" and focus["outline_width"] >= 2
                        focus_ratio = contrast_ratio(focus["outline"], focus["background"])
                        assert focus_ratio >= 3
                        focus_evidence = {"ratio": round(focus_ratio, 3), "outline_rgb": focus["outline"],
                                          "background_rgb": focus["background"], "width": focus["outline_width"]}
                        page.emulate_media(reduced_motion="reduce")
                        motion = page.locator(".button").first.evaluate("el => ({animation: getComputedStyle(el).animationDuration, transition: getComputedStyle(el).transitionDuration})")
                        assert all(float(part.strip().rstrip("s")) <= .01 for value in motion.values() for part in value.split(","))
                        page.emulate_media(reduced_motion="no-preference")
                        checks.append("Keyboard focus indicator exceeds 3:1 contrast; reduced-motion durations are at most 0.01 seconds")
                        check_security(browser, page, origin, private, checks)
                    else:
                        layout, contrast = [], []
                    screens = {}
                    viewport_screens = {}
                    viewport_bounds = {}
                    for label, path in {
                        "overview": "/admin-dashboard",
                        "inventory": "/admin-inventory",
                        "sales": "/branch-sales?sale_id=1&branch_id=1",
                        "transfer": "/transfers?transfer_id=1&branch_id=2",
                        "purchasing": "/purchasing?order_id=2&branch_id=2",
                    }.items():
                        response = page.goto(origin + path)
                        if response.status == 404 and label == "inventory":
                            page.goto(origin + "/admin-inventory")
                        page.wait_for_load_state("networkidle")
                        filename = f"{args.phase}-{name}-{label}-desktop.png"
                        page.evaluate("window.scrollTo(0, 0)")
                        page.screenshot(path=str(output / filename), full_page=True)
                        screens[label] = filename
                        if args.phase == "after" and label in ("overview", "purchasing"):
                            mobile_page = context.new_page()
                            mobile_page.on("pageerror", lambda error: errors.append(str(error)))
                            mobile_page.set_viewport_size({"width": 320, "height": 844})
                            assert mobile_page.goto(origin + path).status == 200
                            mobile_page.wait_for_load_state("networkidle")
                            wait_for_mobile_capture(mobile_page)
                            mobile_filename = f"after-{name}-{label}-320-viewport.png"
                            viewport_bounds[label] = mobile_page.locator(".page-content, #synthetic-demo-notice, .panel").evaluate_all("""elements => elements.map(el => ({
                                cls: el.className, x: el.getBoundingClientRect().x, right: el.getBoundingClientRect().right,
                                width: el.getBoundingClientRect().width, client: el.clientWidth, scroll: el.scrollWidth
                            }))""")
                            assert all(value["right"] <= 320 and value["scroll"] <= value["client"] + 1 for value in viewport_bounds[label])
                            mobile_page.screenshot(path=str(output / mobile_filename), full_page=False)
                            viewport_screens[label] = mobile_filename
                            mobile_page.close()
                    page.set_viewport_size({"width": 320, "height": 844})
                    page.goto(origin + "/admin-dashboard")
                    page.screenshot(path=str(output / f"{args.phase}-{name}-overview-320.png"), full_page=True)
                    measurements["browsers"][name] = {
                        "version": browser.version,
                        "screenshots": screens,
                        "viewport_screenshots": viewport_screens,
                        "viewport_capture_waited_for_sidebar": args.phase == "after",
                        "viewport_capture_method": "Fresh page sized 320x844 before navigation; avoids stale compositor frames after full-page captures",
                        "viewport_bounds": viewport_bounds,
                        "javascript_errors": errors,
                        "mobile_document_width": page.evaluate("document.documentElement.scrollWidth"),
                        "checks": checks,
                        "layout": layout,
                        "contrast": contrast,
                        "native_zoom_probe": zoom_probe,
                        "focus_indicator": focus_evidence,
                    }
                    assert not errors, errors
                    browser.close()
            final = verify(app)
            assert initial == final, "Browser reads changed disposable demo business facts"
            measurements["business_facts_unchanged"] = True
            (output / f"{args.phase}.json").write_text(json.dumps(measurements, indent=2), encoding="utf-8")
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        summary = {"phase": args.phase, "evidence": str(output / f"{args.phase}.json"),
                   "business_facts_unchanged": measurements["business_facts_unchanged"],
                   "browsers": {name: {"version": value["version"], "checks": value["checks"],
                       "javascript_errors": value["javascript_errors"], "layout_cases": len(value["layout"]),
                       "contrast_cases": len(value["contrast"]), "focus_indicator": value["focus_indicator"]}
                       for name, value in measurements["browsers"].items()}}
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
