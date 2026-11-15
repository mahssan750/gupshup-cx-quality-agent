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
7. Group genuine missing-rule cases into Knowledge drafts after the run.
8. Let the Knowledge agent draft an Arabic rule, retrieve the closest existing rules, and classify
   the proposal as `new`, `duplicate`, `conflict`, `update`, or `insufficient`.
9. Require supervisor approval before a new rule is activated or an existing rule is changed.

Customer reaction and wording style are stored only as secondary observations. They cannot create
an employee violation without a matching company rule.

Complete architecture maps:

- `docs/gupshup-quality-officer-architecture-a-to-z.txt` — current concise architecture.
- `docs/gupshup-quality-officer-architecture-a-to-z-en.txt` — full English ASCII A–Z structure,
  scenarios, interactions, failure paths, recovery paths, and sequence diagrams.

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

## Start the React UI

```powershell
cd C:\gubshup_quality_officer
.\Start-CXQuality.ps1
```

The UI opens on `http://127.0.0.1:8765`. The new Dr Nutrition control room uses the official
purple (`#723881`) and green (`#8EA82E`) palette and provides:

- One-field run creation: enter only the folder containing ZIP files; dates and safe cost/model
  controls come from the project configuration.
- A representative Evidence Lab sample balanced across compliant, non-compliant, missing-rule,
  and excluded outcomes, with the exact rule and message evidence behind each decision.
- A Knowledge Loop that converts recurring gaps after every run into review drafts. The AI can
  draft the rule and check for duplication or conflict, but only a supervisor can activate or
  update a rule.
- Company-verdict confirmation that records the review without duplicating the existing rule;
  corrections alone require and save a new reusable rule.
- Run status, coverage, and actual OpenRouter cost without the removed generic quality criteria.
- A readable light/dark theme. The selection is stored in the browser and the first visit respects
  the operating-system color preference.

The previous server-rendered administration pages remain temporarily reachable at `/classic`
for data administration; they are not part of the current scoring workflow.

## Local access control

The React interface, every `/api/v1` endpoint, and the older administration pages require an
administrator session. Set the private credentials in `.env`; they are ignored by Git:

```dotenv
CXQUALITY_ADMIN_USERNAME=admin
CXQUALITY_ADMIN_PASSWORD=choose-a-strong-private-password
CXQUALITY_SESSION_HOURS=8
```

The session cookie is HTTP-only and SameSite Strict. Five failed logins from the same local address
cause a one-minute lock. The signing secret is generated automatically under `local/` and is never
committed. Use the header account button to sign out. The intentionally simple local password
should be changed before the UI is exposed through any proxy or shared machine.

### Rebuild the frontend after editing React

```powershell
npm install
npm run build
```

The compiled frontend is committed under `src/cxquality/static/app`, so normal users only need
`Start-CXQuality.ps1`; Node.js is not needed to run the built UI.

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
[architecture text](docs/gupshup-quality-officer-architecture-a-to-z.txt). The latest end-to-end
validation and defect log is in
[docs/functional-a2z-report-2026-11-15.md](docs/functional-a2z-report-2026-11-15.md).

## Development

```powershell
npm run build
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

Synthetic tests and GitHub Actions require no customer data or API key.
