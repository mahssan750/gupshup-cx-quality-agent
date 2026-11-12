# Gupshup transcript cascade downloader

This downloader uses the two requests captured from Chrome:

- the paginated closed-chat list;
- the per-chat PDF transcript generator.

It selects chats by `updatedAt`, then downloads the ZIP/PDF transcript for every
matching chat. The default transcript mode matches **Entire Conversation History
(Last 30 days)** while the chat selection defaults to the latest seven Dubai
calendar days.

## Run a two-chat test

Open PowerShell and run:

```powershell
node "C:\gubshup_quality_officer\extractor\gupshup-transcript-downloader.mjs" `
  --auth-curl "C:\Users\moham\.codex\attachments\e0b926e0-b8fe-4ccb-ab62-26c8de799e32\Pasted text.txt" `
  --from 2026-11-03 `
  --to 2026-11-09 `
  --max-chats 2 `
  --out "C:\odoo_on_prem\gupshup-test"
```

## Download all chats from the last seven days

```powershell
node "C:\gubshup_quality_officer\extractor\gupshup-transcript-downloader.mjs" `
  --auth-curl "C:\Users\moham\.codex\attachments\e0b926e0-b8fe-4ccb-ab62-26c8de799e32\Pasted text.txt" `
  --from 2026-11-03 `
  --to 2026-11-09 `
  --out "C:\odoo_on_prem\gupshup-2026-11-03-to-2026-11-09"
```

The default `--transcript-mode entire30` reproduces the checkbox behavior: select
chats updated during the seven-day window, but request each selected chat's full
available history from the last 30 days. To restrict transcript messages to the
same seven-day window, add:

```text
--transcript-mode selected
```

## Resume and authentication

- The bearer token is read from the captured cURL and is never copied into the
  output files.
- Tokens expire. If the script reports HTTP 401/403, capture a fresh cURL, save it
  to a text file, and rerun with that file. Existing ZIPs are skipped.
- Successful and failed chats are recorded in `manifest.jsonl`.
- `selection.json` records only internal chat identifiers and timestamps, not
  customer names or message content.
- ZIPs are written atomically under `zips`; interrupted `.part` files are never
  treated as complete.

## Monitor the active background download

One-time status:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\gubshup_quality_officer\extractor\Monitor-GupshupExport.ps1" -ExportPath "C:\path\to\export"
```

Live status, refreshed every 10 seconds:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\gubshup_quality_officer\extractor\Monitor-GupshupExport.ps1" -ExportPath "C:\path\to\export" -Watch
```

You can also inspect these files directly:

```text
C:\odoo_on_prem\gupshup-2026-11-03-to-2026-11-09\background.log
C:\odoo_on_prem\gupshup-2026-11-03-to-2026-11-09\background-errors.log
C:\odoo_on_prem\gupshup-2026-11-03-to-2026-11-09\manifest.jsonl
```

## Resume if the process stops

Run the same full-download command again. It scans the output folder and skips
every completed ZIP. If the token expired, first capture a fresh Gupshup API
request as cURL, save it to a text file, and replace the `--auth-curl` path.

## Useful options

```text
--plan-only          Enumerate matching chats without downloading
--concurrency 4      Number of simultaneous transcript requests (1-12)
--page-size 250      Records per chat-list request (1-499)
--max-chats N        Download only the first N matching chats
--delay-ms 150       Per-worker delay between chats
```

