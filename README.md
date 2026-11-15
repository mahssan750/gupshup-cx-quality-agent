# Gupshup CX Quality Agent

Local-first analysis of Gupshup Agent Assist transcript exports. The production evaluator is
`company-supervisor-v1`: it judges whether the employee followed an applicable approved company
rule or supervisor precedent. There is no generic tone, empathy, clarity, or resolution score.

## Current architecture

1. Read the ZIP whose name matches the chat/session ID and extract embedded PDF text.
2. Exclude media-only or unsupported chats before paid evaluation.
3. Calculate deterministic operational metrics such as response time and unanswered turns.
4. Retrieve active `company-rule`, `company-policy`, `company-supervisor-rule`, and manager-review
   Knowledge plus approved supervisor precedents.
5. Ask the configured decision model for one company verdict: `compliant`, `non_compliant`, or
   `needs_clarification`.
6. Require an applicable reference, a customer message, and an agent message before recording a
   non-compliant verdict.
7. Send genuine missing-rule cases to the Clarifications inbox.

Customer reaction and wording style are stored only as secondary observations. They cannot create
an employee violation without a matching company rule.

## Private local data

ZIPs, PDFs, rosters, API keys, SQLite databases, logs, screenshots, and local overrides remain
under `local/` or their original source folders and are ignored by Git. Phone numbers and country
codes are intentionally preserved because the company uses them to infer applicable country
context.

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Put the restricted OpenRouter key and model IDs in `.env`.

## Start the UI

```powershell
cd C:\gubshup_quality_officer
.\Start-CXQuality.ps1
```

The UI opens on `http://127.0.0.1:8765` and provides:

- Runs and deterministic samples with budget caps and resumable checkpoints.
- Company verdict results and employee mistake review.
- Clarifications for genuinely missing company rules.
- Supervisor knowledge import from a WhatsApp ZIP.
- Knowledge and agent-alias administration.

## Safe pilot

Use a 20–50 chat sample before a full week:

```powershell
.\.venv\Scripts\cxquality.exe sample `
  --zip-folder "C:\path\to\weekly\zips" `
  --sample-size 20 `
  --budget 0.15 `
  --stop-at 0.12
```

Use `--no-ai` for extraction and operational metrics without paid model calls. The evaluator is
fixed to `company-supervisor-v1`; historical evaluator selection has been removed.

## Supervisor rules

Open **Supervisor knowledge** to import the exported WhatsApp group ZIP. Importing is local and
costs nothing. The bulk command excludes group-wide penalties, links without written judgment,
blank captions, and pure shift penalties:

```powershell
.\.venv\Scripts\cxquality.exe approve-supervisor-rules --dry-run
.\.venv\Scripts\cxquality.exe approve-supervisor-rules
```

See [docs/supervisor-knowledge.md](docs/supervisor-knowledge.md) and the complete
[architecture text](docs/gupshup-quality-officer-architecture-a-to-z.txt).

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

Synthetic tests and GitHub Actions require no customer data or API key.
