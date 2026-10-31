# T14 synthetic-only historical importer

**Real M5 remains BLOCKED.** The current owner authorized synthetic-only development;
this does not approve any use of Kaggle/Zenodo files. See [rights review](RIGHTS_REVIEW.md).
No dataset downloader, archive extractor, operational ingestion route, UI or ML code
is included. The prior [preparation plan](T14_SYNTHETIC_PREPARATION.md) is historical;
the supported subset is implemented below, while real-data execution stays blocked.

## Source/manifest contract

`inventraiq-synthetic-import-v1` is separate from the unchanged T13 M5 manifests.
Only `inventraiq-generated-history-v1` / `formula-v1` / `synthetic_fixture` is accepted.
See [small manifest example](t14_synthetic_small.example.json). The exact closed
manifest is reconstructed from bounded profile/seed/prices/fault configuration and
compared canonically, including expected file SHA-256/bytes/rows and fixed maps.
This prevents relabeling an arbitrary CSV/JSONL or real M5 manifest as synthetic.
There is no rights-approved flag or source/path override.

The generator writes only new fixtures under a verified target's
`fixtures/<manifest-sha256>/` directory: manifest.json, calendar.jsonl, prices.jsonl
and observations.jsonl. No external input path is accepted. Inputs are deterministic
ASCII JSONL generated from formulas, not sampled/copied observations. File hashes
are locally calculated generated-byte fingerprints, not Zenodo published MD5.
The reader requires exact file set/size/hash, 20 MB per file, <=40,000 rows per file,
<=1024 bytes per line and <=64 KB manifest. No archives, CSVs, symlinks, path traversal,
unexpected files or URI/database URLs are inputs. Rehash on restart and before publish.

| Profile | Shared synthetic SKUs | Stores | Original fixture dates | Cells |
|---|---:|---:|---|---:|
| small | 6 | 3 | 2001-01-01–2001-01-14 | 252 |
| medium | 120 | 3 | 2001-01-01–2001-03-31 | 32,400 |

These are test-sized profiles, **not the T13 M5 profile sizes**. All observations
keep their original generated dates, daily_units and synthetic_fixture provenance.
SYN-STORE-01/02/03 map once to DEMO-01/02/03, SYN-ITEM-NNNN maps to SYN-SKU-NNNN.
No Walmart IDs, manufacturer details or financial history are claimed.

For zero-based store/item/day indices and seed 13, observed units are
`(store + item + day + seed) % 7`; cells where `(store + item + day) % 17 == 0`
explicitly have missing coverage and null units. A nonmissing result 0 is a real
observed fixture zero. Calendar keys are consecutive original dates. Optional weekly
USD price metadata is absent when `(store + item + week) % 11 == 0`; absent price
stays null. Otherwise price_minor is `100 + item + week`, a generated weekly metadata
value. It is never a captured sale price, converted to MYR or counted as POS revenue.

## Explicit safe target and commands

Use the repository's working Python interpreter (`python` below). Create an empty
parent directory under ignored `data/private/` or use a temporary directory. No
command reads DATABASE_URL, .env or the configured Flask app. Initialization always
creates a new `t14-demo-<uuid>` child, never reuses a DB. Copy the printed absolute
target path into later commands; there is no default target.

```powershell
python scripts/import_history.py init --parent C:\path\to\private\empty-parent --acknowledgment "CREATE DISPOSABLE SYNTHETIC DATABASE"
python scripts/import_history.py generate --target C:\path\to\t14-demo-<uuid> --profile small --revision synthetic-r1
python scripts/import_history.py run --target C:\path\to\t14-demo-<uuid> --manifest C:\path\to\t14-demo-<uuid>\fixtures\<hash>\manifest.json --chunk-size 73 --max-chunks 1
python scripts/import_history.py run --target C:\path\to\t14-demo-<uuid> --manifest C:\path\to\t14-demo-<uuid>\fixtures\<hash>\manifest.json --chunk-size 317
python scripts/import_history.py reconcile --target C:\path\to\t14-demo-<uuid> --run-id 1
```

Replace placeholders; do not paste the angle-bracket paths literally. Generation
does not overwrite an existing fixture directory. To rerun, reuse its manifest.
For medium use a separate fresh target or an explicit new synthetic revision.
`--stage-only` allows reconciliation without publication; omit it on resume to publish.
`--max-chunks` interrupts only after committed chunks; it is a durable test control.
Library `ingest(..., chunk_size=...)` allows 1–1000 rows/chunk, default 250.

Target validation checks the new-directory shape, non-symlink paths, descriptor,
SQLite application ID, matching token-hash/purpose marker and FK enforcement. It
first probes read-only; only a matched target opens mode=rw (no file creation).
SQLite's authorizer then denies writes to **all core tables**, marker/schema/PRAGMA
changes and ATTACH. The local target descriptor is a private capability; keep its
directory isolated. This is an offline owner/admin tool, not an HTTP authorization
interface or protection against an OS owner rewriting the DB and capability together.
Do not promote a T14 disposable DB to an operational target.

Initialization seeds only the three demo branches and explicitly named synthetic
catalog dimensions in that brand-new DB. It creates no users, stock, sales, PO,
receipts or ledger movements. During ingestion, no operational table is writable.

## Stage, reconcile, publish and recovery

ImportRun statuses: staging → reconciled → published → superseded. Unique manifest
identity yields the same run; a source/revision cannot be rebound to different
parameters. Each BEGIN IMMEDIATE chunk atomically inserts facts/quarantine and
advances byte offset, line number and counters. Resume seeks to that exact byte
checkpoint after revalidating all inputs; no reread-induced duplicates. A crash
inside a chunk rolls it back. A crash after chunk commit resumes after it.

Validation rejects invalid date/calendar joins, unknown branch/SKU, bad units/coverage,
incorrect price-week joins, ambiguous duplicate price keys and malformed JSON.
Duplicate facts are rejected under DB uniqueness, including across chunks. One
primary reason per rejected line is stored with line number, raw-record hash and
nonnegative numeric-unit evidence; raw rejected data is not copied to the DB.
Known fault variants are generator options for tests, not arbitrary input admission.

Reconciliation independently aggregates persisted rows and rejects; checks durable
input count/offset, accepted+rejected, observed+missing, input numeric units =
accepted units + rejected numeric units, and branch sums. It reports explicit
missing, observed zeros, unknown prices, unrepresented cells and date range. Invalid
negative units have no nonnegative numeric-unit contribution. Quarantined cells do
not become fabricated zeros. Quarantine publication is supported and explicitly
reports incomplete coverage; clean profiles have zero rejects.

Only `published_sales_observations`, `observations()` (bounded 1–1000 rows/page) and
`published_summary()` expose active facts. Do not read the base table for public
analytics: it intentionally retains staged and superseded history. No existing app
dashboard/API has been wired to these tables. Future interfaces need T05 scope
enforcement before integration; this tool has no client branch authority.

The active-run pointer and statuses switch in one transaction after reconciliation
is rechecked. The old revision stays visible throughout replacement staging; WAL
readers holding a snapshot retain the complete old revision. New readers see the
complete new revision. A stale competing replacement cannot overwrite a newer
publication. Failed publication rolls back old status and pointer together. Replays
of superseded manifests never restore them. Facts/quarantine are immutable and
only staging runs accept inserts. Core branch/SKU and composite run identity FKs
prevent orphan/mismatched facts.

For interruption resume the same command/manifest. Corrupt files or changed manifests
must be restored to exact generated bytes; never edit checksums, counters, base IDs
or checkpoints to force progress. Keep failed evidence or generate a new demo/revision.
There is no destructive reset, automatic down migration or cleanup command.

## Reproducible verification

```powershell
python scripts/verify_t14.py --output C:\path\to\private\new-evidence.json
python -W error -m unittest discover -s tests -p test_historical_ingestion.py -v
python -W error -m unittest discover -s tests -p test_t14_migration.py -v
```

The verifier creates fresh temporary targets, imports small/medium, interrupts after
73 records, resumes, replays, reconciles independently calculated quantities, hashes
bounded published output and verifies all core records unchanged. Output is original
synthetic verification evidence; a new output file is required (no overwrite). Raw
fixtures, DBs and generated reports stay outside Git/under ignored private storage.
See T14 verification (historical development record; omitted from this package) for actual results and blockers.

Runtime is SQLite-only. Offline MySQL migration artifacts are **not MySQL validation**.
No real-data importer, real rights acceptance, fingerprint validation, production
cutover, large-M5 benchmark, archive support or T15 operational simulation is claimed.
