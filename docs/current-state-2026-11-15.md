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
  The Knowledge agent proposes an Arabic rule, retrieves nearby active rules, and labels each draft
  as new, duplicate, conflict, update, or insufficient. Drafts remain inactive until a supervisor
  approves their wording and resolution.
- Confirming a correct verdict records the manager review without copying its existing reference
  into Knowledge. Correcting a verdict requires a reusable rule and learns exactly one rule.
- Reviewed cases move out of the pending Evidence Lab sample and remain available under
  **تمت مراجعتها** with the manager reason.
- The React UI now has readable typography and a persistent light/dark theme.
- The entire UI and API now require a local `admin` session. Credentials remain in `.env`, session
  cookies are HTTP-only and SameSite Strict, logout is available in the header, and repeated failed
  attempts are temporarily rate-limited.
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

## Functional A-to-Z validation

Three additional 10-chat runs were completed against separate local ZIP folders. They processed
30 chats for USD 0.008981035 including Knowledge-agent analysis and manual retries, far below the
authorized USD 2 ceiling. The first run was safely stopped after five chats and resumed without
reprocessing its checkpoint. One real Knowledge draft was correctly classified as a duplicate of
rule #134 and closed without increasing the 248 active items.

The current automated suite is 80 tests including authentication lifecycle and lockout coverage, plus Ruff and the production
Vite build. See `docs/functional-a2z-report-2026-11-15.md` for the action matrix and defect log.
