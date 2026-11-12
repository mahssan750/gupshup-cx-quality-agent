# Gupshup Quality Officer — brief guide

## What this project does

The project downloads Gupshup chat transcripts, extracts embedded PDF text without OCR, measures
objective service metrics, and uses the approved Knowledge Base to score sentiment, inferred
customer satisfaction, resolution, empathy, clarity, and tone. Uncertain cases go to
**Clarifications** instead of receiving an invented score.

## Weekly cycle

1. Use `extractor/gupshup-transcript-downloader.mjs` to download the week's chat ZIP files.
2. Open the local Quality Officer UI with `C:\gubshup_quality_officer\Start-CXQuality.ps1`.
3. Open **New run**, paste only the path of the folder containing ZIP files, and click
   **Start full scoring run**.
4. Monitor progress under **Runs**, then review **Results**, **Aliases**, and **Clarifications**.
5. Answers and revisions in **Clarifications** update the Knowledge Base for later scoring runs.

The normal screen still needs only the ZIP folder. Open **Advanced controls** when you need a
manual date range, a deterministic sample, a different budget or model, more/fewer AI workers,
objective-only processing, a roster override, or a prepared run that does not start immediately.
The **Runs** page provides safe Stop, checkpoint Resume, worker logs, configuration editing, and
Clone Settings. Stop is cooperative for new runs: already-started requests are saved, then the
worker exits at a checkpoint.

## Manual quality control

- **Employee mistakes** is the operational mistake dashboard. It groups AI-flagged low components
  by employee, branch, staff ID, mistake category, and affected chat. It shows the evidence and why
  the component was flagged, supports manager confirmation/correction/dismissal, and exports CSV.
- **Quality review** lists every scored conversation and all five score segments. Opening a chat
  shows the weighting formula, AI evidence, retained decision payload, full locally extracted
  transcript, and manager correction form.

Every manager decision preserves the original AI score in an audit record. Corrections immediately
update dashboards and rankings. The manager must also write a reusable, identifier-free rule; that
rule is created or updated in the Knowledge Base for future scoring runs.

Managers work with one **Quality Score from 0 to 100**. Satisfaction, resolution, empathy, clarity,
and tone remain under-the-hood AI factors for explanation and audit, but they are not separate
manager inputs or report columns.

The reporting dates are detected from a folder name like
`gupshup-2026-11-03-to-2026-11-09\zips`. If the name contains no date range, today's Dubai date is
used. The roster, OpenRouter key, models, concurrency, and spending limits are private settings in
`.env`; they are not requested on every run.

## Simplest way to run

```powershell
cd C:\gubshup_quality_officer
.\Start-CXQuality.ps1
```

Then paste the ZIP folder into the single field on the page. For terminal-only use:

```powershell
.\Run-Weekly-QA.ps1 -ZipFolder "C:\path\to\weekly-export\zips"
```

Do not start the same folder twice while its first run is still active.

## Main folders

- `extractor/` — Gupshup cascade downloader and export monitor.
- `src/cxquality/ingest.py` — deterministic ZIP/PDF parser; no OCR.
- `src/cxquality/` — metrics, AI scoring, Knowledge Base, aliases, and web UI.
- `local/` — private SQLite database, logs, and local overrides; excluded from Git.
- `tests/` — synthetic automated checks that contain no customer chats.
