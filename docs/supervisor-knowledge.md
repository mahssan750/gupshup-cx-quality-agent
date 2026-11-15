# Supervisor knowledge workflow

## Purpose

New runs use `company-supervisor-v1`. The evaluator does not create penalties from generic
resolution, empathy, clarity, or tone dimensions. Its primary decision is `compliant` or
`non_compliant`, and a non-compliant decision requires all of the following:

1. An applicable active Knowledge rule or approved supervisor precedent.
2. A cited customer message establishing the request and context.
3. A cited agent message establishing the behavior being judged.

Customer reaction and wording style are retained as secondary signals. They cannot independently
create a company-rule violation.

Jev confidence is treated as answer-distribution concentration rather than proven accuracy. The
current calibrated gates are 0.50 for understanding and 0.30 for each mandatory reference/customer/
agent evidence choice; the three evidence choices remain required for every final verdict.

## Import a WhatsApp supervisor group

1. Export the WhatsApp chat with media as a ZIP file.
2. Start the local UI and open **Supervisor knowledge**.
3. Paste the absolute ZIP path. The supervisor sender may be entered explicitly or detected from
   repeated feedback captions.
4. Click **Import locally**. Importing makes no model call and spends no OpenRouter credit.

The import is idempotent by SHA-256 archive hash. Re-importing the same file never creates
duplicate cases. Referenced images are copied to `local/supervisor-knowledge/<import-id>/media`.
The source ZIP is not modified.

## Automatic queues

- `supervisor_case`: an available screenshot with a concrete supervisor caption.
- `rule_candidate_missing_image`: a concrete supervisor judgment whose media was omitted from the
  WhatsApp export. It can suggest a rule but is incomplete as a precedent.
- `review_required`: an available image whose purpose is uncertain.
- `link_only`: a screenshot caption containing only a URL or mention.
- `report_or_unrelated`: media posted without a supervisor judgment.

Automatic classification never activates Knowledge. A manager must review the item.

## Manager decisions

For each item choose a decision and final category:

- **Approved + Supervisor precedent** makes the reviewed case retrievable by future runs.
- **Approved + Rule candidate** records the case but does not make it a complete precedent.
- **Excluded + Report / Other platform / Unrelated** removes it from the review queue without
  deleting the source or audit record.
- **Needs context** keeps the item available until its missing screenshot or explanation is found.

The corrected caption is optional. Write a reusable rule only when the judgment is general enough
to apply to future conversations. Selecting **Promote the written rule to active Knowledge**
creates a versioned `company-supervisor-rule` Knowledge item linked back to the source case.

## Identifiers

The company configuration deliberately preserves customer names, phone numbers, country codes,
emails, and order identifiers. Phone country codes may be relevant to country-specific rules. The
company evaluator sends the original text to the configured model. This is an explicit operating
choice, not an automatic redaction workflow.

## Missing WhatsApp media

`<Media omitted>` records retain their supervisor caption. They remain rule candidates until the
original screenshot is recovered. They must not be approved as complete precedents without the
conversation evidence that the supervisor saw.

## Safe iteration loop

1. Approve a small batch of precedents and rules.
2. Prepare a 20–50 conversation sample with a low spending limit.
3. Review every AI/company disagreement.
4. Correct the decision and add or revise only the rule that explains the disagreement.
5. Repeat, while leaving the benchmark holdout untouched.

All UI and CLI runs use `company-supervisor-v1`. Historical evaluator selection has been removed.
Only active `company-policy`, `company-rule`, `company-supervisor-rule`, `quality-review`, and
approved supervisor precedents can govern a company verdict.

## Bulk approval requested by management

When management explicitly wants all written supervisor feedback promoted, run:

```powershell
.\.venv\Scripts\python.exe -m cxquality.cli approve-supervisor-rules --dry-run
.\.venv\Scripts\python.exe -m cxquality.cli approve-supervisor-rules
```

The command excludes messages whose text contains a group-wide penalty such as
`خصم 3 نقاط على الجميع`. It also excludes link-only, blank, and pure shift-penalty
captions because they do not contain a written rule. All other supervisor captions are
cleaned, approved, and promoted. Identical cleaned rules share one Knowledge item while
every source case keeps its audit record.

To inspect the Knowledge base as UTF-8 text:

```powershell
.\.venv\Scripts\python.exe -m cxquality.cli export-knowledge .\knowledge.txt
```
