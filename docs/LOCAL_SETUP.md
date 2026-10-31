# Local portfolio setup

InventraIQ develops the owner's original SmartShelf implementation as a Flask/Jinja inventory
decision-support application. SQLAlchemy services enforce branch permissions and
transactional inventory workflows. Local evidence explains recorded stock, expiry,
sales and provisional planning inputs. Gemini is an optional bounded tool planner;
factual explanations are generated locally. Read [attribution](../ATTRIBUTION.md)
before considering publication. This source-only candidate excludes private evidence and development campaigns.
The owner approves MIT licensing for original work within their authority; third-party rights remain separate. No production deployment is certified.

## Requirements and clean installation

Verified: WSL Ubuntu, x86-64, Python **3.14.4**, SQLite **3.46.1**, Oracle MySQL
**8.4.11**. SQLite suffices for the disposable demos. MySQL requires its standard
client and `mysqldump` for migration/recovery work. Windows compiled-extension
compatibility and other Python/platform combinations are not certified. Node and
Playwright are optional browser/test tooling, not application requirements.

From a fresh approved local checkout, inside WSL:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c constraints/portfolio-python314.txt
.venv/bin/python -m pip check
export PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export ASSISTANT_PROVIDER=fake GEMINI_ENABLED=false ASSISTANT_PROPOSALS_ENABLED=false
unset GEMINI_API_KEY
```

The constraints capture the 27 resolved dependency versions from the Phase 6
fresh installation. They do not pin wheel hashes, package-index availability,
Python, the OS or MySQL. Requirements alone permit some version drift.
Dependencies are installed separately; no virtual environment or wheel is shipped.

## Disposable demos and account provisioning

```bash
.venv/bin/python -B scripts/run_demo.py --check
.venv/bin/python -B scripts/run_product_review.py --check
.venv/bin/python -B scripts/run_product_review.py
```

The populated review asks privately for a temporary password of at least 12
characters and lists five synthetic accounts: HQ administrator, independent HQ
approver, HQ analyst, branch manager and clerk. Visit
`http://127.0.0.1:5055/roles`. All stock, money and demand are synthetic; its pinned
simulation business clock is not today's operational date. Normal shutdown removes
the launcher's temporary fixture. Each launch creates its own database and accepts
no existing target. No data download or preserved Phase 2/3/5 target is needed.

The smaller `run_demo.py` creates the current schema, demo branches and an explicit
HQ grant for `demo-admin@example.invalid`; it prompts privately for its password
and serves on loopback port 5000. No default credential is shipped. Both `--check`
modes use undisclosed random credentials and exit without serving. For a persistent
local database, account creation must also assign explicit HQ/branch grants:
legacy `admin`/`staff` portal roles alone confer no business authority. Use the
supported demo for first setup rather than copying historical account snippets.

HQ administrators manage the catalog and assignments; HQ analysts have read-only
business access. Branch managers and clerks are limited to active assignments;
manager-only operational actions remain unavailable to clerks. Transfers retain
independent approval. See [permission inventory](../T05_PERMISSION_INVENTORY.md).

## Environment and MySQL

`.env.example` contains development settings only. If using `.env`, create it
privately, keep it ignored and remove `PYTHON_DOTENV_DISABLED` for that process.
Explicit process variables take precedence. Use a stable, privately generated
`SECRET_KEY` for persistent sessions; demos generate temporary keys themselves.

Choose either `DATABASE_URL` or all four `DB_HOST`, `DB_NAME`, `DB_USER`,
`DB_PASSWORD` (optional `DB_PORT`, default 3306). The URL takes precedence; unset
it when selecting `DB_*`. Do not put credentials in shell arguments, source,
screenshots, reports or Git. Use a private environment/secret injection mechanism.
MySQL URLs use `mysql+pymysql`; configuration errors must not trigger SQLite fallback.

Use a dedicated schema-scoped application account with SELECT/INSERT/UPDATE/DELETE,
no global access, GRANT OPTION or DDL. A separate operator applies schema changes.
`APP_ENV=production` additionally requires a stable secret of at least 32 characters,
secure cookies, HTTPS, debug disabled, MySQL and a trusted `DB_SSL_CA` file with
certificate/hostname verification. These guards do not constitute production
security acceptance. The app has no configured trusted-host allowlist; local demos
bind loopback. Any future exposed service requires a reviewed ingress/host policy.

### Six-step fresh MySQL procedure

Start only with a newly named, verified empty disposable schema. Current operational
metadata provides the prerequisite tables around the sequence; these six files
are **not** a standalone bootstrap over an arbitrary schema, nor a universal upgrade:

1. `t06_v002_expand_mysql.sql`
2. `t14_v002_expand_mysql.sql`
3. `t15_v002_expand_mysql.sql`
4. `t16_v001_indexes_mysql.sql`
5. `t20_v002_expand_mysql.sql`
6. `t27_v001_expand_mysql.sql`

The existing fresh-only rehearsal implements the exact metadata/SQL sequence,
checks all protected hashes, records the six identities/checksums in
`schema_migrations`, and verifies readiness. Read
[singleton compatibility](../migrations/PHASE1_SINGLETON_COMPATIBILITY.md) first.
The retained MySQL rehearsal helpers require an existing local root-socket operator and a non-root Unix account. They select `INVENTRAIQ_OPERATOR_USER` when explicitly set, otherwise the `SUDO_USER` account, and store private credentials beneath that account's home in `.local/state/inventraiq-phase1`. Missing, unknown or root operator selections stop before target provisioning. Existing `--mysql`, `--provision`, `--tests` and `--sqlite` interfaces are retained. These are opt-in fresh-target operator tools, not server installers; review the documented schema prerequisites first. SQLite demos need none of these operator privileges, and retained development targets must not be used to validate this copy.

After privately selecting only the intended new application's DML connection:

```bash
.venv/bin/python -m flask --app app db-readiness --allow-mysql
```

Readiness is read-only. No app startup applies migrations. Never replay historical
v001 singleton MySQL CREATE scripts over v002, relabel registry rows, rewrite
checksums or use `db.create_all()` to upgrade existing data. Existing-schema cutover
requires its own inspected starting state, backup, approvals and forward plan.

### Disposable recovery rehearsal

Create a distinct empty recovery schema using a separate operator; inspect both
source and destination identities. Stop writers/DDL for the small local rehearsal.
Use a schema-only positional name, not `--databases`, so the dump does not select
or recreate the source. With local socket authentication and private schema names:

```bash
umask 077
mysqldump --user=root --socket=/var/run/mysqld/mysqld.sock \
  --single-transaction --no-tablespaces --skip-add-drop-table --skip-add-locks \
  --set-gtid-purged=OFF "$SOURCE_SCHEMA" > "$PRIVATE_DUMP_PATH"
mysql --user=root --socket=/var/run/mysqld/mysqld.sock \
  "$EMPTY_RECOVERY_SCHEMA" < "$PRIVATE_DUMP_PATH"
```

The three variables must be explicitly set to reviewed targets/a private dump path.
Never restore onto the source or another existing target. Compare every table's
row counts and canonical row hashes, six migration identities/checksums, triggers
and view dependencies; use the restricted recovery account to assert the selected
schema and application readiness. Retain dumps privately with restrictive access;
never publish them. Phase 6 used this flag set with an in-memory dump and verified
67 tables, 79 triggers and one independent view. This is not production recovery,
point-in-time recovery, encryption, retention or disaster-recovery certification.

## Tests and optional Gemini

```bash
export APP_ENV=testing DATABASE_URL=sqlite:///:memory:
export PYTHON_DOTENV_DISABLED=1
PYTHONPATH=tests .venv/bin/python -B -m unittest \
  test_database_configuration test_authentication_hardening test_phase1_singletons -v
.venv/bin/python -B scripts/render_phase1_singletons.py --check
```

The commands above exercise public disposable tests. Historical development campaigns are separate evidence. Full discovery is optional when a future change
justifies it: `.venv/bin/python -B -m unittest discover -s tests -v`. Tests use their
own disposable SQLite fixtures; browser tests require separate tooling. Do not
run historical MySQL lifecycle verifiers against retained targets.

Default to `ASSISTANT_PROVIDER=fake`, `GEMINI_ENABLED=false`, and no backend key.
Read [Gemini configuration](../T26_STAGE_B_GEMINI.md) for the existing optional
adapter's flags, reviewed model and budget controls. Explicit enablement, reviewed
project/tier attestations, backend-only `GEMINI_API_KEY`, loopback, non-debug
development/testing and attested synthetic SQLite are all required. MySQL and
arbitrary prompts are rejected; outbound content is preset questions and static
schemas, without tool results or business history. Offline payload inspection:

```bash
.venv/bin/python -B scripts/smoke_t26_gemini.py --hq-inventory-urgency
```

Keep live authorization flags absent. Development records include an owner-reported successful smoke; release verification makes no live call. Quota/errors fail without automatic retry
or paid fallback. Tool planning is distinct from locally generated factual
explanations; cross-branch urgency ranking was not independently verified.

## Limits and policy gates

Forecasts remain provisional synthetic evidence, without proven production accuracy
or qualified replenishment policy. Missing forecasts are unknown, not zero demand.
No automatic ordering, autonomous AI operation or production readiness is claimed.
T27 defaults to disabled; schema readiness alone does not authorize execution.
Enablement requires explicit named-target migration, capability-policy,
backup/recovery and human-review approval. Do not use `--proposals` for ordinary
release checks. See [T27 migration/policy](../migrations/t27_v001_migration.md).

Legacy CSV and M5 dataset rights remain unresolved; use only shipped synthetic
generators. Generated credentials, databases, captures, benchmark evidence and
private reports remain ignored. Preserve the [SmartShelf acknowledgments and licensing scope](../ATTRIBUTION.md) and all applicable third-party terms; the software license does not grant dataset rights. Future POS integration
is out of scope and is not implemented by this release.
