# InventraIQ Inventory Aperture

Approved direction: Concept 1. Geometry version: `inventory-aperture-v1`.

Three horizontally offset shelf planes bound a negative-space capital I. The
middle plane extends two grid units and contains a single two-unit measurement
notch on its outer right edge. It suggests deliberate analysis without a chart,
robot, sparkle or literal warehouse box. The three-plane rhythm is a visual
composition, not a database limit or a promise of exactly three branches.

## Source and variants

Edit [inventory-aperture.json](inventory-aperture.json), then run:

```powershell
python scripts/render_brand_assets.py
python scripts/render_brand_assets.py --check
```

The renderer uses only Python's standard library and never accesses a database or
network. All seven current SVGs use the exact same path geometry. Do not edit a
generated SVG independently or paste the geometry into templates.

| Asset in `static/images/` | Use |
|---|---|
| `inventraiq-logo.svg` | Primary horizontal, emerald symbol / ink wordmark on light surfaces |
| `inventraiq.svg` | Compact emerald symbol, including the existing public asset path |
| `inventraiq-logo-dark.svg` | One-ink dark horizontal, print / light surfaces |
| `inventraiq-logo-light.svg` | One-ink white horizontal on dark ink surfaces |
| `inventraiq-symbol-dark.svg` | Compact one-ink dark on light surfaces |
| `inventraiq-symbol-light.svg` | Compact white on dark ink surfaces |
| `inventraiq-favicon.svg` | Compact emerald with white backing for either browser-tab theme |

The 32-unit symbol has a four-unit shelf thickness, eight-unit central aperture
stem and sixteen-unit aperture crossbars. Curves soften only the external shelf
corners. All major coordinates align to the half-scale 16px grid. The edge notch
is secondary and may become unobtrusive at 16px; recognition must rely on the I
and shelf silhouette, never the notch alone.

## Typography, size and space

The wordmark reads **InventraIQ** without an inserted space. It uses locally
available Segoe UI, then Arial, then sans-serif; 23-unit type, weight 650 and
restrained -0.6-unit tracking. No typeface file, remote font or converted third-party
glyph outline is included. System-font substitutions can vary between platforms.
The symbol-to-wordmark gap is 13 grid units; preserve the supplied proportions.

Use the horizontal logo at approximately 176px wide in the existing shell. At
320px the auth header retains a separate 12px gap around its brand link, protecting
the keyboard outline from the adjacent access link. Prefer the compact symbol to
shrinking a full wordmark below 140px. Compact minimum: 16px; use 24px or 32px where
space permits. Leave at least four symbol-grid units of clear space around the
artwork; larger external collateral may use eight. Do not crop, stretch, rotate,
animate or add shadows/gradients to the mark.

Emerald is `#087f67`, ink is `#192d28`, and reversed artwork is white. Primary
artwork supports white, T22 warm canvas and the existing pale auth-story surface.
Use the white monochrome variant on ink; do not put the primary emerald/ink lockup
on dark surfaces. Walnut remains an optional surrounding brand accent and is not
needed inside this compact identity. State colors and T22 tokens are unchanged.

## Accessibility and integration

Use the shared `brand()` Jinja macro. Its local SVG image has `alt="InventraIQ"`;
the enclosing navigation link receives that name without duplicate hidden text.
A local dark monochrome `<picture>` source is selected for print. Standalone
non-favicon SVGs have `role="img"` and a title association. When using an image
beside an already visible InventraIQ label elsewhere, use `alt=""` so the brand is
not announced twice. Favicon naming comes from the document/tab title.

The logo requires no motion or color distinction to convey the name. Keep the
shell's existing focus outlines, skip links and keyboard navigation behavior.
This artwork does not establish screen-reader or whole-application conformance.

## Review and provenance

Open [preview.html](preview.html) locally for the old/new comparison, variants,
dark/light applications and 16/24/32/64px examples. The earlier project mark is
retained in `previous-mark.svg` strictly for this labeled comparison; it is no
longer an application logo. Prior InventraIQ/SmartShelf project attribution in the
main README is preserved.

The replacement geometry was authored for this repository from the approved
concept, without stock icons, copied competitor geometry or image tracing. A
limited source/image-search review checked common inventory-brand motifs against
[Zoho Inventory](https://www.zoho.com/us/inventory/),
[Sortly](https://www.sortly.com/) and [inFlow](https://www.inflowinventory.com/),
on 2026-10-23. Zoho's official
[inventory artwork](https://www.zohowebstatic.com/sites/zweb/images/ogimage/inventory-logo.png)
shows a hand-truck/package approach; available Sortly/inFlow image-search results
show letter/geometry approaches. Third-party search thumbnails are only discovery
evidence, not authoritative current brand specifications. No competitor asset is
bundled, traced or licensed for use here.

Layered shelves, rounded rectangles and negative-space letters are common design
vocabulary; similar marks may exist. This limited review is not an exhaustive
similarity search, legal opinion, trademark clearance, registration or guarantee of
exclusive rights. Existing project attribution/license terms are retained; no
rights to third-party fonts or brands are asserted.
