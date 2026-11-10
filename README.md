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

```powershell
.\Run-Weekly-QA.ps1 `
  -ZipFolder "C:\odoo_on_prem\gupshup-2026-11-03-to-2026-11-09\zips" `
  -Roster "C:\Users\moham\OneDrive\Desktop\الرقم الوظيفي لخدمة العملاء.xlsx" `
  -FromDate 2026-11-03 `
  -ToDate 2026-11-09
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
- **Clarifications:** answer a specific knowledge gap or ignore it. An answer becomes a local
  knowledge item and queues only the affected conversation for re-scoring.
- **Aliases:** accept or reject uncertain agent-name matches.
- **Knowledge:** add, edit, or deactivate policies and operational guidance.

## Cost control and model roles

TypeSafe Jev 1.14 is the bounded decision judge. GPT-6 Luna is used only for short clarification,
alias, or coaching text when required. `BudgetGuard` reserves estimated spend before every request,
records actual OpenRouter cost, and refuses any call that would cross the local stop threshold.

Run the pilot first and use its actual cost-per-chat to estimate the full week. Never extrapolate
from token prices alone when the transcripts vary substantially in length.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

The GitHub workflow runs only synthetic tests. It never needs transcript files or an API key.
See [docs/architecture.md](docs/architecture.md) for the complete processing flow.

