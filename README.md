# Gupshup CX Quality Agent

Local-first weekly analysis of Gupshup Agent Assist transcript exports. It combines deterministic
operational metrics with typed AI judgments about customer sentiment, inferred satisfaction,
resolution, empathy, clarity, and tone. Uncertain conversations stay unscored and enter a small
clarification inbox instead of receiving an invented score.

## Safety and privacy

- The parser reads embedded PDF text with Poppler `pdftotext`. **There is no OCR path.**
- Only the PDF whose filename matches the ZIP/session ID is scored. Extra PDFs from "Entire
  Conversation History" only help link repeat contacts; they are not double-counted.
- Phone numbers, email addresses, URLs, order-like identifiers, and other obvious personal data are
  redacted before model calls.
- ZIPs, PDFs, Excel rosters, API keys, SQLite databases, reports, and local overrides are ignored by
  Git.
- The review UI binds to `127.0.0.1` only.
- OpenRouter calls stop locally at `$3.80`, below the key's `$4.00` hard limit.

## What the weekly run produces

- Objective metrics: first response, response intervals, handling duration, unanswered customer
  turns, transfers, repeat contact, and workload.
- AI scores with evidence and confidence for understandable conversations.
- `needs_clarification` records for conversations the judge cannot interpret reliably.
- An alias-review queue for uncertain transcript-agent names.
- Agent and branch summaries that always show sample size and scoring coverage.
- A local knowledge-base editor so policies and answers can be updated without changing code.

Coverage is deliberately visible:

```text
scoring coverage   = scored / (scored + needs clarification)
clarification rate = needs clarification / (scored + needs clarification)
exclusion rate     = not scorable / all processed
```

Rankings use a reliability-adjusted score rather than the raw average alone:

```text
adjusted score = (agent score total + weekly average * 10) / (scored chats + 10)
reliability    = scored chats / (scored chats + 10)
```

An agent needs at least five scored conversations to receive a rank. Smaller samples remain
visible as `Insufficient sample`, and their displayed adjusted score is pulled toward the weekly
average. This prevents a single unusually good conversation from appearing as a reliable 90%
performance result above agents who handled substantial volume.

Eligible agents are ranked lexicographically by scored conversation count (descending), adjusted
score (descending), and sample reliability (descending). The ranking therefore treats demonstrated
workload as the primary business criterion and quality as the first tie-breaker.

## Requirements

- Windows with Python 3.11 or later
- Poppler `pdftotext` available on `PATH`
- Gupshup ZIP exports and an `.xlsx` agent roster
- An OpenRouter key with a hard spending limit

## First-time setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Put the OpenRouter key in the local `.env` file. Never commit it:

```dotenv
OPENROUTER_API_KEY=sk-or-v1-...
CXQUALITY_DB=local/cxquality.sqlite3
CXQUALITY_BUDGET_USD=4.00
CXQUALITY_STOP_AT_USD=3.80
CXQUALITY_DECISION_MODEL=typesafe/jev-1.14
CXQUALITY_WRITER_MODEL=openai/gpt-6-luna
CXQUALITY_AI_WORKERS=6
CXQUALITY_ROSTER=C:\path\to\agents.xlsx
CXQUALITY_ROSTER_OVERRIDES=local/agent-overrides.json
```

On Windows, open that file with `notepad .env`. The first line must contain the complete key after
the equals sign; a blank value cannot run paid scoring.

Initialize the database and import policy text:

```powershell
.\.venv\Scripts\cxquality.exe init-db
.\.venv\Scripts\cxquality.exe import-policy "C:\path\to\policies.txt"
```

## Run a safe pilot

Start with 200 deterministic conversations. Repeating the same dates, folder, and sample size
selects the same sample.

```powershell
.\.venv\Scripts\cxquality.exe sample `
  --zip-folder "C:\odoo_on_prem\gupshup-2026-11-03-to-2026-11-09\zips" `
  --roster "C:\Users\moham\OneDrive\Desktop\الرقم الوظيفي لخدمة العملاء.xlsx" `
  --from-date 2026-11-03 `
  --to-date 2026-11-09 `
  --sample-size 200 `
  --budget 4.00 `
  --stop-at 3.80
```

Use `--no-ai` to validate parsing and operational metrics at zero model cost.

## Run the full week

The normal workflow is folder-only. Start the UI:

```powershell
cd C:\gubshup_quality_officer
.\Start-CXQuality.ps1
```

On **New run**, paste the folder containing the ZIPs and click **Start full scoring run**. Dates are
inferred from names such as `gupshup-2026-11-03-to-2026-11-09\zips`; the configured roster,
Knowledge Base, aliases, models, and budget settings are loaded automatically.

The UI also provides optional advanced controls for samples, manual dates, per-run budgets,
concurrency, models, roster overrides, zero-cost objective-only runs, and prepare-without-starting.
The **Runs** page can stop safely at a checkpoint, resume, show worker logs, edit paused-run
controls, and clone settings. Duplicate active/resumable runs for the same folder are rejected.

Two manager QA workspaces complete the feedback loop: **Employee mistakes** provides agent/branch
mistake reporting with evidence and CSV export, while **Quality review** allows every scored chat
to be double-checked against the full transcript and transparent component weighting. Manager
overrides preserve the original AI judgment, update current reports, keep an audit history, and
write a reusable correction rule into the Knowledge Base.
Manager-facing review uses one Quality Score (0–100); the component factors remain internal.

The equivalent terminal command is:

```powershell
.\Run-Weekly-QA.ps1 -ZipFolder "C:\odoo_on_prem\gupshup-2026-11-03-to-2026-11-09\zips"
```

The workflow checkpoints every conversation. If the terminal or network stops, resume from the run
ID printed at startup:

```powershell
.\.venv\Scripts\cxquality.exe resume RUN_ID
```

## Review in the local UI

```powershell
.\.venv\Scripts\cxquality.exe serve --port 8765
```

Open <http://127.0.0.1:8765>. The UI contains:

- **Dashboard / Runs:** progress, coverage, exclusions, cost, and checkpoints.
- **Results:** agent rankings with sample size and coverage.
- **Clarifications:** pending records are organized into recurring business themes such as branch
  locations, greetings/no response, attachments, product information, health guidance, orders,
  and payments. Each theme shows representative customer/agent evidence inside the UI. One answer
  creates one manager-approved Knowledge rule for the group; ignoring dismisses the whole pattern.
  These actions never start a scoring run automatically.
- **Aliases:** accept or reject uncertain agent-name matches.
- **Knowledge:** add, edit, or deactivate policies and operational guidance.

## Bilingual evaluation update

New runs now use the v2 hybrid evaluator: multilingual interpretation and semantic Knowledge
selection, Jev decisions, and an independent GPT-6.1 Sol fallback. Customer satisfaction is
separate from employee quality; missing factors remain unscored with coverage and message evidence.
Historical and resumed legacy runs retain their original scoring definition. See
[the v2 operating and benchmark guide](docs/bilingual-evaluation.md) for configuration, limitations,
review labels, and the three-mode comparison command.

## Cost control and model roles

TypeSafe Jev 1.14 is the bounded decision judge. In v2, GPT-6 Luna also interprets requests
and selects relevant Knowledge; GPT-6.1 Sol reviews unsettled judgments. `BudgetGuard` reserves
estimated spend before every request,
records actual OpenRouter cost, and refuses any call that would cross the local stop threshold.
Future runs score up to six conversations concurrently by default. Set `CXQUALITY_AI_WORKERS`
between 1 and 16 to tune provider concurrency; the shared reservation-based budget guard remains
authoritative across every worker.

Run the pilot first and use its actual cost-per-chat to estimate the full week. Never extrapolate
from token prices alone when the transcripts vary substantially in length.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

The GitHub workflow runs only synthetic tests. It never needs transcript files or an API key.
See [docs/architecture.md](docs/architecture.md) for the complete processing flow.
See [docs/bilingual-evaluation.md](docs/bilingual-evaluation.md) for the v2 scoring and benchmark
workflow, and [the implementation record](docs/implementation-validation-2026-11-14.md) for the
validated rollout state and location of private review artifacts.
See [PROJECT-GUIDE.md](PROJECT-GUIDE.md) for the short weekly operating guide and
[extractor/README.md](extractor/README.md) for the bundled Gupshup downloader.

