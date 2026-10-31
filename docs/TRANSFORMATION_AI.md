# Assistant completion and product integration

The final reported T26 live response selected two valid `inventory_risks`
functions, both passed validation and authorization and executed locally. Their
0-row and 3-row results established East low-stock evidence but did not establish
Central near-expiry evidence. No result was rejected. That evidence establishes
incomplete tool selection/coverage rather than a network or authorization defect;
the original arguments were deliberately not retained and are not reconstructed.

## Corrected local behavior

For the exact question `Which branch has the most urgent inventory issues, and
why?`, the server applies a disclosed inventory-risk coverage policy. The
provider's original plan must still pass tool, argument, call identifier,
duplicate, count and whole-plan branch checks. Unknown, mutating or out-of-scope
calls fail before any read. The server then reads **unfiltered**
`inventory_risks` over the authenticated scope through the existing T25 dispatcher.
Optional provider branch/search/status/page filters cannot accidentally exclude a
required condition from this recognized cross-branch question.

The first page contains at most 25 rows. If necessary, subsequent pages reuse the
T25 signed optimistic snapshot. All pages together remain within the existing
four-call, eight-second, 128 KiB charged-byte and 96 KiB output budgets. A changed
snapshot, denied read, unavailable continuation or exhausted budget never becomes
a claim of complete evidence. No repeated provider planning is needed for these
server-defined pages. Each local result is charged once; local pagination does
not construct or repeatedly charge an unused provider context.

The ordinary provider-independent read orchestration remains available for
other questions. Original call identifiers and duplicate signatures are now
validated for the entire plan before any read, and foreign row branch IDs fail
closed in addition to result-level scope checks.

## Grounded explanation and interface

The new local formatter describes low-stock, near-expiry, expired and review
conditions with the recorded quantities, days remaining and E-number citations.
It reports full or incomplete coverage explicitly. It neither ranks one branch
above another nor invents a business priority rule. The disclosure identifies
the explanation as **deterministic and local**, never model-authored advice.
It contains no hardcoded scenario facts. Branch identifiers are those supplied
by the T25 contract; the tool contract does not currently expose branch display
names.

The assistant drawer provides example questions, readable findings, source
tables, timestamps, scope, provenance and useful failure messages. Raw JSON is
secondary under a technical disclosure. Text nodes render all untrusted values;
there is no provider/user HTML insertion. Download links accept only the existing
same-origin artifact path shape. Closing clears the displayed evidence;
reopening performs a fresh authorized local history read before showing it.
The dialog opens immediately without a fabricated unavailable/loading state.
The action-proposal link requires feature enablement, an authorized action role
and a selected nonconsolidated source branch. T27 core services are unchanged.

## External privacy and remaining limitations

No live external call was made for this work. The unchanged HQ outbound body is
5,752 bytes with SHA-256
`7b191b9833cc169b30cfded581938dcbfa9c40c976f61d7719a4f9a546aa6ece`
at the 256-output-token smoke setting. It consists only of the existing synthetic
instruction, exact question and eight static declarations/configuration. No
tool results, inventory records, history, source names or grants are added.
There is still at most one external planning request, with no retry/fallback.

Fresh explicit authorization remains necessary before another external Gemini
test. Model-authored factual synthesis would require separate approval to send
synthetic evidence and a separately reviewed data/round/budget contract. No real
business-data transmission is enabled. Production ranking policy, T27 existing
database activation/MySQL verification and T28 release requirements remain open.

## Verification

- 9 new disposable-fixture tests passed (17.188 s): HQ completeness, branch
  isolation, invalid/duplicate/foreign/mutating-plan preflight, cancellation,
  denied evidence, signed-snapshot paging, four-tool ceiling, partial coverage,
  full multi-page coverage, source requirements and unchanged external body.
- Existing focused suites: 39 Gemini tests (29.793 s), 29 conversation tests
  (41.369 s), 38 T25 tests (50.343 s), all passed. HTTPS remained mocked.
- JavaScript syntax and focused native-dialog keyboard/focus interaction test
  passed. The retained T26 browser script selectors were updated for the new
  interface; its historical WSL runner was not executed here. Final integrated
  native-Windows browser and full regression evidence belong to the final
  transformation verification report.
- Disposable fixture verification checked business fingerprints unchanged.
  No existing databases, schemas, migrations, credentials, permissions or
  inventory rules were changed; no dependencies, commits, pushes or deployments.

Implementation: `assistant/evidence.py`, `assistant/orchestrator.py`,
`static/js/assistant.js`, the assistant opener in `static/js/workspace.js`,
`templates/components/assistant.html`, focused Python/interaction tests and
the retained T26 browser verification selectors. Shared shell styles are
maintained in `static/css/product.css` by the shell transformation.
