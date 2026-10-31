# T14 preparation while real M5 rights remain blocked

Status update (2026-10-22): subsequent owner authorization implements the
synthetic-only JSONL/chunk/reconciliation/publication subset described in
[T14 importer instructions](T14_IMPORTER.md). The planning text below records the
earlier boundary; archive extraction and all real-data execution remain unavailable.

This is a development/test plan, **not an importer or execution approval**. Only
original generated `synthetic_fixture` inputs and fresh disposable test targets
may be used. Never read local M5 files, contact providers, open the operational DB
or weaken T13's unconditional real-ingestion block. Existing manifest contracts stay
unchanged. None of the future components below is implemented by this follow-up.

| Safe future component | Synthetic fixture / verification |
|---|---|
| Source integrity | Generate tiny bytes; compute MD5/SHA-256 and size; corrupt them. Keep provider assertions and local verification separate. Reject mismatch before staging. |
| Archive safety | Generate traversal, absolute/drive/device paths, symlinks, case-colliding names, duplicate/unexpected members and oversized expansion. Bound members/bytes/ratio before extraction. |
| Bounded parsing | Generate fictional item/store/day rows, invalid headers/dates, negative/fractional units and missing cells. Verify chunk bounds and no silent coercion. |
| Calendar/price joins | Generate unique and duplicate day/date/week keys, missing/future prices. Verify no multiplied facts or future backward filling. |
| Selection/mapping | Reuse T13 formula-generated training summaries, shared coverage and three bijective branch mappings. Prove no future leakage or cross-branch copying. |
| Observation contract | Design revision/store/item/date/measure uniqueness and synthetic provenance separate from POS. No stock effects, invented expiry or recorded MYR revenue. |
| Restart/publication | Use a fake repository or test-only temporary DB for chunk checkpoints, interrupted runs, rollback, duplicate retries and atomic revision visibility. No operational migration. |
| Reconciliation | Generated source/normalized counts, units, rejected rows, coverage, unknown prices and branch totals reconcile. Label all results synthetic. |
| Bounds/performance | Measure generated fixture memory/chunk/payload limits; never call these M5 performance results. |
| Target/rights gates | Reject real-data labels/paths, edited approval flags and non-disposable targets. Keep the current real gate closed. |

Use a separately versioned test-only parser contract for tiny day windows, or
generate the full declared synthetic range. Do not relax the real manifest to fit
a fixture. M5-shaped identifiers in independently generated fixtures must still
be clearly synthetic; do not copy mirror previews or source observations.

Still blocked: real dataset downloads/extraction/hash inspection/import jobs,
operational schema changes, publishing real observations/artifacts and model
training. No populated real manifest, source reconciliation or T14 acceptance is
claimed. Before real T14 execution, require verified authority/use approval,
authorized download, exact local fingerprints/metadata, reviewed mappings and
importer/restart/publication/rollback design, and an explicit approved demo target.
