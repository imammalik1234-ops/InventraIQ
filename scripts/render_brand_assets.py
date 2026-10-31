"""Generate local SVG brand variants from the reviewed Inventory Aperture geometry.

No network, fonts, third-party icon library or database access. Run --check in CI.
"""

import argparse
from html import escape
import json
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "docs/branding/inventory-aperture.json"
DESTINATION = PROJECT / "static/images"


def render_assets():
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    colors = source["colors"]
    geometry = " ".join(source["planes"])
    wordmark = source["wordmark"]

    def svg(color, *, horizontal=False, word_color=None, favicon=False):
        view_box = "0 0 168 32" if horizontal else source["view_box"]
        title = source["name"] if horizontal else "InventraIQ Inventory Aperture symbol"
        if favicon:
            # A neutral backing keeps the mark visible in either browser-chrome theme.
            return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}">'
                    f'<rect width="32" height="32" rx="6" fill="{colors["light"]}"/>'
                    f'<path fill="{color}" d="{geometry}"/></svg>\n')
        content = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" '
                   'role="img" aria-labelledby="brand-title">',
                   f'  <title id="brand-title">{title}</title>',
                   f'  <path fill="{color}" d="{geometry}"/>']
        if horizontal:
            attributes = " ".join(f'{name.replace("_", "-")}="{escape(str(value), quote=True)}"'
                                  for name, value in wordmark.items())
            content.append(f'  <text {attributes} fill="{word_color or color}">{source["name"]}</text>')
        content.append('</svg>')
        return "\n".join(content) + "\n"

    return {
        "inventraiq.svg": svg(colors["emerald"]),
        "inventraiq-logo.svg": svg(colors["emerald"], horizontal=True, word_color=colors["ink"]),
        "inventraiq-symbol-dark.svg": svg(colors["ink"]),
        "inventraiq-symbol-light.svg": svg(colors["light"]),
        "inventraiq-logo-dark.svg": svg(colors["ink"], horizontal=True),
        "inventraiq-logo-light.svg": svg(colors["light"], horizontal=True),
        "inventraiq-favicon.svg": svg(colors["emerald"], favicon=True),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check bytes without writing files")
    args = parser.parse_args()
    mismatches = []
    for name, content in render_assets().items():
        path = DESTINATION / name
        if args.check:
            if not path.is_file() or path.read_bytes() != content.encode("utf-8"):
                mismatches.append(name)
        else:
            path.write_bytes(content.encode("utf-8"))
    if mismatches:
        parser.exit(1, "Brand assets differ from canonical source: " + ", ".join(mismatches) + "\n")
    print("7 canonical SVG assets " + ("verified" if args.check else "generated"))


if __name__ == "__main__":
    main()
