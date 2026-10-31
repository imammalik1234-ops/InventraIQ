# Third-party dependency review

Reviewed 2026-10-31 from the 27 runtime distributions resolved in a fresh WSL
Python 3.14.4 environment. Exact versions are in
[`constraints/portfolio-python314.txt`](constraints/portfolio-python314.txt).
This source-only release installs dependencies separately: packages, wheels, databases
and dependency license files are not vendored. The [MIT License](LICENSE), copyright 2026 Abdulmalik Abdullahi Muhammad, applies
to original code the named holder has authority to license. This does not relicense third-party components
or assert copyright in them. Historical SmartShelf acknowledgments and the limits
of that authorization are recorded in [`ATTRIBUTION.md`](ATTRIBUTION.md).

| Installed distributions | Declared license family / review |
| --- | --- |
| Flask, Werkzeug, Click, Jinja2, MarkupSafe, itsdangerous, Flask-SQLAlchemy | BSD; current Flask/Werkzeug/Click/MarkupSafe metadata specifies BSD-3-Clause |
| blinker, Flask-Login, flask-cors, SQLAlchemy, PyMySQL, narwhals, six | MIT |
| colorama, joblib, threadpoolctl, python-dotenv, scikit-learn, pandas | BSD / BSD-3-Clause |
| cloudpickle | BSD-3-Clause |
| greenlet | MIT AND PSF-2.0 |
| typing_extensions | PSF-2.0 |
| python-dateutil | Dual license; installed license text contains BSD and Apache terms and additional notices |
| tzdata | Apache-2.0 metadata; packaged license notices also reviewed |
| NumPy | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 metadata; bundled notices extend beyond this summary |
| SciPy | BSD-family project license with bundled third-party notices |

The NumPy and SciPy wheels contain additional component notices, including GCC
runtime licensing and its runtime exception, numerical libraries and component
licenses. Their complete installed license files, including SciPy Qhull notices,
were retained with the distributions and reviewed by path/hash and relevant text.
A short metadata label cannot describe every binary component. Any future binary,
wheel, container or packaged-app redistribution needs its own complete notice and
license review. The installed flask-cors distribution declares MIT but did not
include a separately named license file; do not treat this summary as a substitute
for upstream license text if that package is later redistributed.

Pip 25.1.1 is environment tooling, not a declared runtime dependency. Python and
Oracle MySQL are separately installed prerequisites, not redistributed artifacts.
No dependency vulnerability certification or external advisory scan is claimed.
The version constraints improve repeatability but are not a hash-verified supply
chain lock or a commitment that later advisories are absent.

Active static assets use local source-authored SVG/CSS/JavaScript. No remote font,
font binary, CDN library or competitor image is bundled in the active templates.
System fonts are referenced without copying font files. Branding provenance is
recorded in [`docs/branding/README.md`](docs/branding/README.md); trademark clearance
is not established. Any separately owned code or assets retain their existing notices and terms; the owner's MIT authorization covers only original work they have authority to license.
