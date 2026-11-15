# Current production state — 15 November 2026

## React control room

- `/` is now a production-built React interface themed with Dr Nutrition's official purple and
  green palette.
- A new run asks only for the folder containing ZIP files. Dates are inferred and private model,
  worker, and budget settings are applied automatically.
- Evidence Lab returns a deterministic representative sample across company-compliant,
  company-non-compliant, missing-rule, and textually non-scorable cases.
- Each case shows the selected company reference, direct conversation evidence, reason, and model
  confidence. Supervisor corrections update the verdict and add an approved reusable rule.
- Every completed run synchronizes recurring clarification themes into `knowledge_candidates`.
  These drafts are self-updating, but remain inactive until a supervisor approves their wording.
- Old generic quality criteria are not displayed or used by this interface.

## Removed

- The legacy generic scorer and `bilingual-v2` evaluator.
- The old comparison benchmark and scoring configuration.
- Generic tone, empathy, clarity, resolution, and customer-satisfaction review paths.
- Ten historical runs and their conversations, evaluations, clarification records, and spend rows.
- Manager-selected Knowledge IDs: 1, 5, 8, 16, 20, 21, 22, 23, 24, 26, 29, 31, 33, 35,
  37, 39, 47, and 48.

The recoverable pre-cleanup database is stored locally at
`local/backups/cxquality-before-legacy-cleanup-20261115.sqlite3` and is ignored by Git.

## Active Knowledge

- 32 manager-reviewed `company-rule` items retained from the previous Knowledge base.
- 216 deduplicated `company-supervisor-rule` items from WhatsApp supervisor feedback.
- Group-wide penalty messages, link-only captions, blank captions, and pure shift penalties are not
  active rules.

## Only evaluator

All new and resumed work uses `company-supervisor-v1`. A final verdict requires an applicable
approved reference, customer evidence, and agent evidence. Role-normalized evaluation text keeps
the original sender in the message so country codes and identifiers remain available as context.

## Validation sample

Run `fca4fc48-382f-402e-aca1-319dd8e6b676` processed a deterministic 20-chat sample using
`typesafe/jev-1.14` and `openai/gpt-6-luna`:

- 20 processed
- 1 scored as compliant
- 12 sent to clarification
- 7 not scorable
- USD 0.002910726 total model cost
- zero legacy scoring dimensions in stored results

The result confirms the cleaned engine is conservative. The next improvement cycle should review
the 12 clarification cases and add or refine only company rules that supervisors actually approve.
