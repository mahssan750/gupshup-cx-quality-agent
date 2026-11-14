# Bilingual evaluation v2 implementation record

The bilingual evaluation update was implemented and validated on 14 November 2026.

## Validation completed

- 74 automated tests passed.
- Ruff and `git diff --check` passed.
- A final Arabic hybrid smoke test scored the observable response while leaving customer
  satisfaction and unsupported factual accuracy out of the employee score.
- No production employee evaluations were rewritten and no full-week paid rescore was launched.

The live smoke and comparison calls verified provider integration only. They do not establish
model accuracy. Current confidence thresholds and weights remain provisional until bilingual human
labels establish false employee penalties, missed mistakes, and clarification rates by language.

## Private review artifacts

The prepared files are stored under `local/evaluation-v2/`, which is excluded from Git:

- `qa-review-sample.jsonl`: 150 real chats, balanced across Arabic, English, and mixed language.
- `real-chat-pilot.jsonl`: three real-chat integration comparisons.
- `synthetic-comparison.json`: synthetic Arabic/English comparison output.
- `final-smoke-test.json`: final Arabic hybrid smoke-test output.

The sample labels are intentionally empty. A bilingual reviewer must fill `expected_status`,
`expected_mistakes`, and `reviewer_notes`. Development and holdout samples are marked. These files
can contain identifying details in free-form customer text even after standard redaction; keep them
inside the company and never add them to Git.

## Operational state

New runs use v2. Existing partially processed runs resume with the legacy scorer so one run cannot
mix scoring definitions. Restart the local UI and create a new run to load v2. Catalog and dated
inventory integration still depend on importing scoped records into Knowledge.

See [Bilingual evaluation v2](bilingual-evaluation.md) for the scoring design, configuration,
limitations, and model-comparison commands.
