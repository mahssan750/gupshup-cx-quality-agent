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
