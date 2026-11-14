# Bilingual evaluation v2

New runs use a versioned hybrid evaluator. Existing partially completed runs retain the legacy
evaluator on resume, so a single run does not mix scoring definitions. Historical reports are
unchanged. Restart the local UI process to load updated Python code; start a new run to use v2.

The interpreter (configured writer model, default GPT-6 Luna) reads the original full text.
It produces an intent hypothesis, product candidates and missing details. Semantic reference
selection operates in batches of 40 existing Knowledge entries, supplemented by normalized
Arabic/English keywords and fuzzy spelling matching. This is model-based semantic selection,
not an embedding database. Catalog records and dated inventory are not connected automatically:
import scoped reference material through Knowledge. Unavailable historical facts stay unverified.

Jev answers independent questions about handling the request, clarity, empathy, tone and factual
accuracy. Each included factor requires observable evidence, a valid original message id and
sufficient confidence. Accuracy additionally requires a selected applicable reference. Missing
dimensions are omitted, with weights renormalized and coverage displayed. Resolution/handling is
required, and observed weight must total at least 55%. Default weights are handling 30%, accuracy
25%, clarity 25%, empathy 10%, tone 10%. Customer satisfaction is separate and never affects v2
employee quality. These weights and thresholds are initial engineering defaults, not calibrated
accuracy guarantees. Model confidence values are not empirical accuracy.

Unsettled judgments receive an independent GPT-6.1 Sol review before remaining unscored. Both
models see the original conversation and scoped references. Invalid numeric values, unsupported
message ids and malformed answers cannot create a quality score. Photo/document and other media
markers exclude the chat before paid calls. This depends on the text export retaining a marker;
the application cannot detect attachments omitted entirely by the exporter.

Configuration:

```
CXQUALITY_EVALUATION_MODE=hybrid
CXQUALITY_REVIEWER_MODEL=openai/gpt-6.1-sol
CXQUALITY_REVIEWER_INPUT_PER_MILLION=10
CXQUALITY_REVIEWER_OUTPUT_PER_MILLION=40
```

Reviewer price settings are conservative reservation estimates, not advertised model prices.
All interpreter, retrieval, Jev and reviewer calls share the existing budget guard. Actual cost is
recorded by the provider. Do not increase the run budget until a pilot establishes actual cost.

## Model comparison

```
.\.venv\Scripts\python.exe -m cxquality.benchmark prepare --count 150 --output local/review.jsonl
.\.venv\Scripts\python.exe -m cxquality.benchmark compare --dataset local/review.jsonl --output local/comparison.jsonl --limit 150 --budget 1
```

Preparation reads the database and PDFs without modifying them. It balances Arabic, English and
mixed customer messages, annotates short requests, and assigns a deterministic development/holdout
split. Expert reviewers fill expected_status (scored, needs_clarification, not_scorable),
expected_mistakes (an array of component names), and reviewer_notes. Null means unlabelled;
an empty mistakes array means a reviewer found no mistakes. Inspect de-identification before
sharing outside the company. Customer free text can still contain identifying details.

Comparison evaluates jev, llm and hybrid against identical chats and Knowledge. Results are written
incrementally to a separate JSONL file; employee reports and production evaluations are untouched.
Use a new output path for each run. Inspect cost, clarifications, false flags and missed flags by
language. No accuracy claims are available for unlabelled cases. Use --split holdout only after
prompt/threshold development; do not tune on that set. Model failures propagate and a budget stop
retains completed results. There is no automatic benchmark resume yet.

Manager corrections continue to use the existing Knowledge workflow. They change retrieved
context, not model weights. Cache keys include knowledge, models, mode and thresholds. Keep
corrections scoped to the specific product, country, date and service situation; existing broad
theme grouping is not proof that a correction applies to every conversation in that theme.
