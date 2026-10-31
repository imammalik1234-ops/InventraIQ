# M5 rights evidence and decision — 2026-10-22

Decision: **BLOCKED for every real-data use in InventraIQ.** This is an evidence
decision, not a claim that all these uses are forbidden. No terms were accepted,
account authenticated, owner contacted, dataset downloaded or permission inferred.
The readable official material establishes provenance, not a transferable license.

## Evidence register

All URLs below were checked on **2026-10-22** using the research tool. Only page
metadata and the organizer's public guide were reviewed; no dataset bytes were fetched.

| ID | Source / locator | Evidence and limitation |
|---|---|---|
| E1 | [Official Kaggle Accuracy overview](https://www.kaggle.com/competitions/m5-forecasting-accuracy/overview), Description / Submission File / Timeline | Identifies University of Nicosia competition and Walmart source; distinguishes validation and evaluation horizons. Not a dataset license. |
| E2 | [Competition rules](https://www.kaggle.com/competitions/m5-forecasting-accuracy/rules), also [legacy URL](https://www.kaggle.com/c/m5-forecasting-accuracy/rules) | Both returned a page title but **zero readable content lines**. Applicable Data Access and Use, sharing, output and post-competition clauses could not be verified. No direct license wording available to quote. |
| E3 | [Official Data tab](https://www.kaggle.com/competitions/m5-forecasting-accuracy/data) | Zero readable content lines. Current file revisions, byte sizes, hashes and any separate terms remain unverified. |
| E4 | [Kaggle platform terms](https://www.kaggle.com/terms) | Zero readable content lines. Platform access terms could not be independently reviewed. |
| E5 | [Organizer's Competitors' Guide, 10 March 2020](https://storage.googleapis.com/kaggle-forum-message-attachments/772349/15032/M5-Competitors-Guide-Final-10-March-2020.pdf), pp. 3–5 | Describes item/store/day data and weekly price/calendar semantics. Short exact wording: “The historical data range from 2011-01-29 to 2016-06-19.” This is descriptive wording, **not usage permission**. No general license grant was established. |
| E6 | [Organizer's M5-methods repository](https://github.com/Mcompetitions/M5-methods), About / root listing | Public methods/guide repository was inspected as another primary locator. Its public availability does not establish rights to redistribute Walmart observations or derived artifacts. No data files downloaded. |
| E7 | [MOFC M5 page](https://mofc.unic.ac.cy/m5-competition/) | Research tool could not access the URL. No permission attributed to it. |

Search results also alleged a non-commercial restriction, but readable authoritative
wording was not obtained. That allegation is **not an approved license conclusion**.
Third-party Kaggle mirrors, copied notebooks, repository code licenses and mirror
license badges do not prove the uploader can license Walmart data. They are not
substituted for E2–E4 or rights-holder authorization. Publication discussions in a
competition guide do not alone authorize this application's public outputs.

The guide's narrative end-date wording is not used as calendar truth: its stated
1941-day count and June end date are inconsistent with consecutive days from
2011-01-29. Our validation slice uses day-index arithmetic and requires actual
calendar-file reconciliation after approval, rather than silently propagating that
narrative inconsistency. This does not affect the unresolved rights decision.

## Decision by intended use

| Intended use | InventraIQ decision | Missing evidence |
|---|---|---|
| Private local analysis / educational research | BLOCKED, potentially reviewable | Full applicable competition/platform/data terms, lawful account access and acceptance evidence, explicit fit of intended use to permitted purpose. |
| Portfolio demonstration, including screenshots | BLOCKED | Whether self-promotion/non-commercial portfolio use is permitted; whether screenshots, samples and aggregates are publishable. |
| Public hosted application, free or paid | BLOCKED | Hosting, commercial/non-commercial scope, permitted audience, disclosure and serving of outputs; explicit authorization if terms do not cover it. |
| Raw files, subsets or normalized observation redistribution | BLOCKED | An express redistribution grant covering Walmart observations; anonymization and small subsets are not assumed to remove restrictions. |
| Derived CSVs, aggregates, charts, forecasts, metrics, trained weights | BLOCKED | Artifact-specific publication rights, disclosure/reconstruction limits, attribution and commercial restrictions. Model outputs are not assumed exempt. |
| Original synthetic fixtures and source code without M5 observations | Allowed within this task | Clearly label synthetic; no copied data, claimed Walmart results or implied Walmart endorsement. |

## Evidence required to unblock a future revision

An authorized owner must obtain and review the full applicable rules/data license
and platform terms, preserve exact wording and effective version, URL, checked date,
private evidence hash and lawful account acceptance reference (no account secrets).
Record a signed intended-use matrix and artifact restrictions. Obtain written
organizer/Walmart rights-holder clarification where a grant is ambiguous or absent.
No permission request has been sent on the owner's behalf.

Only **after** rights approval may a separately authorized download establish exact
file bytes, SHA-256, byte counts, source revision consistency and metadata. Do not
request a download merely to resolve this T13 task. A later reviewed manifest
version must bind those fingerprints, approvals and importer revision; JSON edits
cannot authorize ingestion. T14 real-data execution stays blocked until that gate
is satisfied. Unknown evidence remains null, never a fabricated checksum or
invented license identifier. Store full restricted terms/acceptance evidence under
`data/private/rights/`, excluded from Git, with approved redacted references only.

## Zenodo rights follow-up — checked 2026-10-22

**Decision unchanged: real ingestion remains BLOCKED.** Public record pages and
JSON metadata APIs were inspected. APIs were inaccessible through the research
tool; read-only PowerShell HTTPS requests succeeded after network approval. No
file-content, preview, archive or download URL was requested. The curated
[evidence register](source_evidence.v1.json) preserves reported fields, not a raw
response snapshot or verified file manifest. Public repository metadata reuse is
covered by [Zenodo's guidance](https://support.zenodo.org/help/en-gb/2-content/21-can-i-get-permission-to-use-a-specific-record).

### Recorded licenses and provenance

| Record / authoritative metadata | Recorded license | Provenance and limitation |
|---|---|---|
| [12636070](https://zenodo.org/records/12636070) / [API](https://zenodo.org/api/records/12636070), v1, 2024-07-03 | `metadata.license.id = cc-by-4.0`; access is open | Creator field says Kaggle; attribution points to Kaggle's Data tab. No underlying rights delegation was found. |
| [10203108](https://zenodo.org/records/10203108) / [API](https://zenodo.org/api/records/10203108), v1 | **No `metadata.license` field**; open access is not a license | Creator field says University of Nicosia; attribution points to Kaggle. Recorded publication year is 2020, repository creation is 2023-11-24; these are different facts. |

The first record's HTML extraction omitted its license value; the API confirmed
the recorded assertion. The second record's omission does not prove no permission
exists elsewhere. Creator fields do not authenticate the depositor's institutional
identity. Neither inspected record establishes uploader authority to relicense
Walmart data; that remains **unverified**, not disproven. No equivalence to original
Kaggle bytes or between the two deposits was established.

[Official Kaggle data](https://www.kaggle.com/competitions/m5-forecasting-accuracy/data)
and [rules](https://www.kaggle.com/competitions/m5-forecasting-accuracy/rules) were
rechecked and still returned zero readable content. No applicable grant was
verified. Zenodo publication cannot itself override competition obligations.

### License scope versus authority

[Zenodo's permission FAQ](https://support.zenodo.org/help/en-gb/2-content/21-can-i-get-permission-to-use-a-specific-record)
states it “does not own the content being uploaded”. Dataset licenses and CC0
repository metadata are separate. [Zenodo Terms of Use v1.3](https://about.zenodo.org/terms/),
clauses 3–7, place compliance on uploaders/users, disclaim endorsement and do not
transfer IP through access. These are not validations of either deposit's authority.

If validly granted, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode.en)
permits reproduction, sharing and adaptation, including commercial use. Sharing
requires supplied attribution/notices, a license reference and change indications;
trademark/endorsement rights are not granted. Section 1(g) limits the grant to
rights the licensor can license; section 5 disclaims title/non-infringement
warranties. A recorded badge cannot settle that authority question.

A limited private-research permission could permit analysis without permitting
portfolio screenshots, public hosting, raw redistribution or derived artifacts.
An authorized CC BY grant would be broader. Neither underlying authorization has
been verified here, so **all real-use decisions above remain BLOCKED**. This is an
evidence gate, not a conclusion that private use is illegal or that no exception
could apply. Attribution alone does not resolve the missing grant.

### Published fingerprints, not local verification

The API supplies the following **published MD5 and byte counts**:

| Record | Object | Published bytes | Published MD5 |
|---|---|---:|---|
| 12636070 | m5-forecasting-accuracy.zip | 48,009,163 | `86f57416a314197f40a17cc6fc60cbb4` |
| 10203108 | calendar.csv | 103,469 | `3ffeab2991b0c8e861d008b39ea4c95c` |
| 10203108 | sales_train_validation.csv | 120,007,726 | `26a366a25beb57b0a8f4c7b148758f94` |
| 10203108 | sales_train_evaluation.csv | 121,736,518 | `b806dfc9f30a745102b708c09951f6aa` |
| 10203108 | sample_submission.csv | 5,228,786 | `c281a69d7c011274899d92020a66e25b` |
| 10203108 | sell_prices.csv | 203,395,785 | `08c591caa99e55daf3e0ccac913f7c85` |

All locally verified MD5, SHA-256 and byte counts remain **null** in the evidence
register. Archive members are uninspected. A ZIP hash is not a member-CSV hash;
MD5 is not SHA-256 or permission evidence. Do not copy published mirror MD5 values
into manifest SHA-256 fields. Existing manifest fingerprints stay null, and the
evidence register cannot unlock the ingestion gate.

### Rights-holder investigation and remaining questions

The [organizer's README](https://raw.githubusercontent.com/Mcompetitions/M5-methods/master/README.md)
links to a Drive folder containing competition data for both phases. This is a
primary provenance locator, but the inspected README does not grant permission
for InventraIQ's intended uses. No Drive content was opened. The MOFC site remained
inaccessible; targeted searches did not locate an accessible Walmart/organizer
statement delegating licensing to these Zenodo depositors or clearing our uses.
That is a search limitation, not proof no such statement exists. Third-party
claims of non-commercial terms remain unverified against actual competition rules.

Needed clarification from an authorized organizer/Walmart rights-holder, with any
depositor delegation evidence: identify exact files/revisions; confirm whether
each deposit was authorized; address private analysis, portfolio promotion, free/
paid public hosting, raw/subset redistribution and charts/aggregates/forecasts/
weights separately; explain interaction with competition terms. Preserve signatory
authority, effective date, conditions and private signed evidence. No third party
was contacted. Until reviewed, neither mirror is an approved alternate source.
See [synthetic-only T14 preparation](T14_SYNTHETIC_PREPARATION.md).
