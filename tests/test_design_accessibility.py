"""Rendered component contracts on a new disposable synthetic database only.

These are semantic/security checks; real keyboard, reflow and colors are checked
by scripts/verify_t22.py. Neither layer claims assistive-technology certification.
"""

from html.parser import HTMLParser
from tempfile import TemporaryDirectory
import unittest


class Element:
    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.text = ""


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__()
        self.nodes, self.stack = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs, self.stack[-1] if self.stack else None)
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for element in self.stack:
            element.text += data

    def elements(self, tag):
        return [node for node in self.nodes if node.tag == tag]

    def descendants(self, parent):
        values = []
        for node in self.nodes:
            current = node.parent
            while current:
                if current is parent:
                    values.append(node)
                    break
                current = current.parent
        return values


class ContrastCalculationTests(unittest.TestCase):
    def test_reference_black_white_and_identical_colors(self):
        from scripts.verify_t22 import contrast_ratio
        self.assertAlmostEqual(contrast_ratio((0, 0, 0), (255, 255, 255)), 21)
        self.assertAlmostEqual(contrast_ratio((80, 120, 65), (80, 120, 65)), 1)

    def test_reference_srgb_threshold_and_symmetry(self):
        from scripts.verify_t22 import contrast_ratio
        ratio = contrast_ratio((118, 118, 118), (255, 255, 255))
        self.assertAlmostEqual(ratio, 4.5422249596)
        self.assertEqual(ratio, contrast_ratio((255, 255, 255), (118, 118, 118)))


class RenderedAccessibilityTests(unittest.TestCase):
    PATHS = (
        "/admin-dashboard", "/admin-inventory", "/sku-inventory?branch_id=2",
        "/branch-sales?sale_id=1&branch_id=1", "/transfers?transfer_id=1&branch_id=2",
        "/purchasing?order_id=2&branch_id=2", "/catalog", "/receiving?branch_id=2",
    )

    @classmethod
    def setUpClass(cls):
        from ingestion.target import ACK
        from simulation.runtime import initialize, create_app
        from simulation.runner import run
        from simulation.verify import verify
        cls.temp = TemporaryDirectory(prefix="inventraiq-t22-components-")
        cls.root = initialize(cls.temp.name, acknowledgment=ACK)
        cls.app = create_app(cls.root)
        run(cls.app)
        cls.initial = verify(cls.app)

    @classmethod
    def tearDownClass(cls):
        from database import db
        from simulation.verify import verify
        try:
            if verify(cls.app) != cls.initial:
                raise AssertionError("Accessibility reads modified synthetic business facts")
        finally:
            with cls.app.app_context():
                db.session.remove()
                db.engine.dispose()
            cls.temp.cleanup()

    def client(self, uid=901, branch=None):
        from scripts.measure_t16 import client
        return client(self.app, uid, branch)

    def pages(self):
        client = self.client()
        for path in self.PATHS:
            with self.subTest(path=path):
                result = client.get(path)
                self.assertEqual(result.status_code, 200)
                yield path, Document(result.get_data(as_text=True))

    def test_one_main_landmark_unique_ids_and_document_language(self):
        for path, document in self.pages():
            self.assertEqual(len(document.elements("main")), 1, path)
            self.assertEqual(document.elements("html")[0].attrs["lang"], "en")
            self.assertEqual(len(document.elements("h1")), 1, path)
            ids = [node.attrs["id"] for node in document.nodes if "id" in node.attrs]
            self.assertEqual(len(ids), len(set(ids)), path)
            main = document.elements("main")[0]
            self.assertEqual(main.attrs.get("tabindex"), "-1")
            self.assertTrue(any(node.tag == "a" and node.attrs.get("href") == "#" + main.attrs["id"]
                                for node in document.nodes))

    def test_visible_form_controls_have_programmatic_names(self):
        for path, document in self.pages():
            labels = document.elements("label")
            for node in document.nodes:
                if node.tag not in ("input", "select", "textarea") or node.attrs.get("type") == "hidden":
                    continue
                ancestor, nested = node.parent, False
                while ancestor:
                    nested = nested or ancestor.tag == "label"
                    ancestor = ancestor.parent
                explicit = any(label.attrs.get("for") == node.attrs.get("id") and "for" in label.attrs for label in labels)
                self.assertTrue(nested or explicit or node.attrs.get("aria-label") or node.attrs.get("aria-labelledby"),
                                f"{path}: {node.tag} {node.attrs.get('name')}")

    def test_dense_tables_have_captions_headers_and_named_scroll_regions(self):
        count = 0
        for path, document in self.pages():
            for table in document.elements("table"):
                count += 1
                descendants = document.descendants(table)
                captions = [node for node in descendants if node.tag == "caption"]
                self.assertEqual(len(captions), 1, path)
                self.assertTrue(captions[0].text.strip(), path)
                self.assertTrue(all(node.attrs.get("scope") in ("row", "col")
                                    for node in descendants if node.tag == "th"), path)
                region = table.parent
                self.assertEqual(region.attrs.get("role"), "region", path)
                self.assertEqual(region.attrs.get("tabindex"), "0", path)
                self.assertTrue(region.attrs.get("aria-label") or region.attrs.get("aria-labelledby"), path)
        self.assertGreaterEqual(count, 5)

    def test_post_forms_keep_csrf_and_workflow_concurrency_fields(self):
        for path, document in self.pages():
            for form in document.elements("form"):
                if form.attrs.get("method", "GET").upper() != "POST":
                    continue
                inputs = [node for node in document.descendants(form) if node.tag == "input"]
                self.assertTrue(any(node.attrs.get("name") == "csrf_token" and node.attrs.get("value") for node in inputs), path)
        client = self.client(branch=2)
        document = Document(client.get("/purchasing?order_id=2&branch_id=2").get_data(as_text=True))
        receiving = [form for form in document.elements("form") if "/receive" in form.attrs.get("action", "")]
        self.assertTrue(receiving)
        for form in receiving:
            names = {node.attrs.get("name") for node in document.descendants(form)}
            self.assertTrue({"expected_version", "request_key", "external_reference", "reason", "evidence"} <= names)

    def test_field_macro_associates_hint_and_error_and_escapes_content(self):
        from flask import render_template_string
        with self.app.test_request_context():
            html = render_template_string("{% from 'components/forms.html' import field %}{{ field('units','Quantity',hint='Whole units',error=error) }}",
                                          error="Invalid <script>value</script>")
        document = Document(html)
        field = document.elements("input")[0]
        self.assertEqual(field.attrs.get("aria-invalid"), "true")
        self.assertEqual(field.attrs.get("aria-describedby", "").split(), ["units-hint", "units-error"])
        self.assertFalse(document.elements("script"))
        self.assertTrue(any(node.attrs.get("id") == "units-error" and node.attrs.get("role") == "alert" for node in document.nodes))

    def test_flash_announcements_escape_html_without_losing_error_text(self):
        client = self.client()
        with client.session_transaction() as state:
            state["_flashes"] = [("error", "Review <script>untrusted</script> input")]
        document = Document(client.get("/admin-dashboard").get_data(as_text=True))
        self.assertFalse(any("untrusted" in node.text for node in document.elements("script")))
        notices = [node for node in document.nodes if node.attrs.get("role") in ("alert", "status")]
        self.assertTrue(any("Review <script>untrusted</script> input" in node.text for node in notices))

    def test_security_guards_survive_component_refinement(self):
        manager, clerk, analyst = self.client(912, 2), self.client(922, 2), self.client(903)
        self.assertEqual(manager.get("/api/operations?branch_id=1").status_code, 403)
        self.assertEqual(manager.get("/branch-sales?sale_id=1&branch_id=2").status_code, 404)
        self.assertEqual(clerk.get("/purchasing").status_code, 403)
        self.assertEqual(clerk.get("/transfers").status_code, 403)
        self.assertEqual(analyst.post("/api/purchase-orders", json={}).status_code, 400)
        self.assertEqual(manager.post("/scope", data={"branch_id": "1"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
