# Optional Gemini planner

The current adapter in `assistant/gemini.py` implements bounded read-tool planning for an attested synthetic SQLite demonstration. Factual explanations are generated locally from authorized tool evidence. Default settings use `ASSISTANT_PROVIDER=fake`, `GEMINI_ENABLED=false` and disabled proposals. No API key or live request is needed for the demo or mocked tests.

## Verified implementation boundary

The standard-library HTTPS adapter uses a fixed Google endpoint and the code allowlist currently contains `gemini-3.5-flash-lite`. This describes implementation, not current provider availability, pricing or access. Before any separately authorized live use, review the provider's current capabilities, terms, project billing and quotas. `GEMINI_FREE_TIER_VERIFIED` is an operator attestation, not an account/billing check. Local caps cannot guarantee zero billing.

Enablement requires explicit provider selection, enable/tier flags, non-debug development/testing, loopback caller and an attested disposable synthetic SQLite target. Production, MySQL and unverified targets fail closed. Keys are read from the backend environment at call time, never from source or browser inputs. Do not put a key in chat, command arguments or logs.

One external planning round contains a fixed allowed question, static instruction and eight static tool schemas: inventory_summary, sales_summary, compare_branches, inventory_risks, product_performance, forecast_evidence, recommendation_preview and report_artifact. Current preset questions include the inventory-urgency question. Arbitrary input, nonempty context, history and tool-result egress are rejected. No actual inventory, financial, account, session, permission or business-history rows enter that payload. Transport/account metadata and the authentication header still reach the provider.

Returned calls undergo strict schema validation, branch preflight and authenticated local dispatch. Provider narrative is neither rendered nor saved. The follow-up evidence round terminates locally. No write tools are registered. Cross-branch urgency ranking has not been independently established as reliable.

The orchestration limit is four calls and an eight-second outer deadline with bounded workers. Adapter defaults are ten attempts per process, two RPM, 16,384 input bytes, 512 output tokens and 65,536 session units; environment values are bounded by code. Quota/excessive usage latches the counter closed. There are no retries, redirects or paid/model fallback. Cancellation cannot retract a transmitted request; DNS/TLS work can outlive the outer wait. Diagnostic logging retains fixed categories, stage and numeric HTTP status without provider bodies, headers, credentials or exception payloads.

## Offline inspection and tests

From the repository root in the installed environment:

```bash
python -B scripts/smoke_t26_gemini.py --hq-inventory-urgency
PYTHONPATH=tests python -B -m unittest test_gemini_provider -v
```

Keep live authorization flags absent. The smoke command above inspects a payload offline. The tests mock HTTPS and use disposable fixtures. Live flags require a separately reviewed synthetic payload and explicit transmission authorization; this release preparation grants none.

## Historical evidence and remaining limits

Development records describe an initial unsuccessful live attempt with insufficient diagnostics, subsequent offline diagnostic hardening and an owner-reported later successful smoke. These are historical records, not a fresh live verification of this copy. Mocked verification establishes local guard behavior, not provider availability, billing safety, production privacy or answer reliability. No external Gemini request is performed during release verification. Free-tier retention and data-use terms require independent review before any future use.
