"""T23 asset and rendered-component contracts; no application/database imports."""

from html.parser import HTMLParser
import json
import unittest
import xml.etree.ElementTree as ET

from scripts.render_brand_assets import PROJECT, render_assets
from scripts.preview_t23 import render_shells
from scripts.verify_t22 import contrast_ratio


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        self.elements.append((tag, dict(attributes)))


class BrandIdentityTests(unittest.TestCase):
    def test_generated_variants_match_source_without_drift(self):
        for name, expected in render_assets().items():
            with self.subTest(name=name):
                self.assertEqual((PROJECT / "static/images" / name).read_bytes(), expected.encode("utf-8"))

    def test_svg_assets_are_passive_local_and_bounded(self):
        allowed_tags = {"svg", "title", "path", "text", "rect"}
        allowed_attributes = {"viewBox", "role", "aria-labelledby", "id", "fill", "d", "x", "y",
                              "font-family", "font-size", "font-weight", "letter-spacing", "width", "height", "rx"}
        for name, content in render_assets().items():
            with self.subTest(name=name):
                self.assertLess(len(content.encode("utf-8")), 1800)
                self.assertNotIn("<!", content)
                root = ET.fromstring(content)
                self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
                for element in root.iter():
                    self.assertIn(element.tag.rsplit("}", 1)[-1], allowed_tags)
                    self.assertTrue(set(element.attrib) <= allowed_attributes)
                    for value in element.attrib.values():
                        self.assertNotRegex(value, r"(?i)(url\(|javascript:|data:|https?://)")
                self.assertNotRegex(content, r"(?i)(<script|<foreignObject|onload=|<animate|<image|<use)")
        self.assertLess((PROJECT / "static/images/inventraiq-favicon.svg").stat().st_size, 550)

    def test_all_variants_share_exact_symbol_geometry(self):
        canonical = json.loads((PROJECT / "docs/branding/inventory-aperture.json").read_text())
        self.assertEqual(len(canonical["planes"]), 3)
        expected = " ".join(canonical["planes"])
        for name, content in render_assets().items():
            with self.subTest(name=name):
                self.assertEqual(ET.fromstring(content).find("{*}path").attrib["d"], expected)

    def test_standalone_symbols_and_lockups_are_named(self):
        for name, content in render_assets().items():
            if "favicon" in name:
                continue  # Browser-tab image; document title supplies the tab name.
            with self.subTest(name=name):
                root = ET.fromstring(content)
                title = root.find("{*}title")
                self.assertEqual(root.attrib["role"], "img")
                self.assertEqual(root.attrib["aria-labelledby"], title.attrib["id"])
                self.assertIn("InventraIQ", title.text)
                text = root.find("{*}text")
                if "logo" in name:
                    self.assertEqual(text.text, "InventraIQ")
                    self.assertNotIn("http", text.attrib["font-family"])

    def test_supported_surface_contrast(self):
        # Ink wordmark text >=4.5:1; the emerald graphic >=3:1 on supported surfaces.
        colors = {
            "emerald": (8, 127, 103), "ink": (25, 45, 40),
            "white": (255, 255, 255), "canvas": (247, 247, 243), "auth": (240, 245, 241),
        }
        for background in ("white", "canvas", "auth"):
            self.assertGreaterEqual(contrast_ratio(colors["emerald"], colors[background]), 3)
            self.assertGreaterEqual(contrast_ratio(colors["ink"], colors[background]), 4.5)
        self.assertGreaterEqual(contrast_ratio(colors["white"], colors["ink"]), 4.5)

    def test_rendered_shells_share_accessible_local_identity_and_print_variant(self):
        for name, content in render_shells().items():
            with self.subTest(shell=name):
                elements = Elements(content).elements
                logos = [attributes for tag, attributes in elements
                         if tag == "img" and attributes.get("class") == "brand-logo"]
                self.assertEqual(len(logos), 2 if name == "login" else 1)
                for logo in logos:
                    self.assertEqual(logo["alt"], "InventraIQ")
                    self.assertEqual(logo["width"], "168")
                    self.assertEqual(logo["height"], "32")
                    self.assertTrue(logo["src"].endswith("/static/images/inventraiq-logo.svg"))
                    self.assertNotIn("aria-hidden", logo)
                print_sources = [a for tag, a in elements if tag == "source" and a.get("media") == "print"]
                self.assertEqual(len(print_sources), len(logos))
                self.assertTrue(all(a["srcset"].endswith("inventraiq-logo-dark.svg") for a in print_sources))
                favicons = [a for tag, a in elements if tag == "link" and a.get("rel") == "icon"]
                self.assertEqual(len(favicons), 1)
                self.assertTrue(favicons[0]["href"].endswith("inventraiq-favicon.svg"))

    def test_templates_contain_no_duplicate_mark_geometry_or_old_brand_classes(self):
        canonical = json.loads((PROJECT / "docs/branding/inventory-aperture.json").read_text())
        for path in (PROJECT / "templates").rglob("*.html"):
            content = path.read_text(encoding="utf-8")
            self.assertNotIn('class="brand-mark"', content, path)
            self.assertNotIn('class="brand-name"', content, path)
            for plane in canonical["planes"]:
                self.assertNotIn(plane, content, path)

    def test_preview_links_resolve_only_to_local_review_assets(self):
        preview = PROJECT / "docs/branding/preview.html"
        for tag, attributes in Elements(preview.read_text(encoding="utf-8")).elements:
            if tag != "img":
                continue
            source = attributes["src"]
            self.assertNotRegex(source, r"^(/|https?:|data:|javascript:)")
            target = (preview.parent / source).resolve()
            self.assertTrue(target.is_relative_to(PROJECT))
            self.assertTrue(target.is_file())
            self.assertIn("alt", attributes)


if __name__ == "__main__":
    unittest.main()
