# Functional A-to-Z report — 15 November 2026

## Outcome

The React frontend, Flask API, worker lifecycle, SQLite persistence, budget ledger, Evidence Lab,
and Knowledge Loop were exercised as one system. Three new paid runs processed 30 real ZIP chats.
The complete validation spent USD 0.008981035, measured by comparing the spend ledger before and
after testing, against the authorized USD 2 ceiling.

The database was backed up before testing at:

`local/backups/cxquality-before-functional-a2z-20261115.sqlite3`

The active Knowledge count was 248 before and after testing. No AI proposal was activated without
supervisor action.

## Live runs

| Run | Processed | Scored | Needs rule | Excluded | Final status |
|---|---:|---:|---:|---:|---|
| `07eccbf1-a7bd-4da3-9f9a-2a9d4706d5a0` | 10 | 2 | 5 | 3 | completed |
| `3ea5bd61-d318-4ea0-971f-d8f8da78527b` | 10 | 2 | 2 | 6 | completed |
| `dddddfeb-23ef-4890-ad72-54aec209e32c` | 10 | 3 | 3 | 4 | completed |

The first run was stopped after five completed sessions. Its checkpoint contained exactly those
five session IDs. Resume processed only the remaining five and finished at 10/10.

## Action matrix

| Action | Expected result | Verified result |
|---|---|---|
| Open `/` | React shell and compiled assets load | HTTP 200; JS and CSS bundle tests pass |
| Enter valid ZIP folder | Create queued full-folder run with inferred dates | 201; worker launched; 10 ZIPs detected |
| Missing/empty ZIP folder | Reject without creating a run | 409 |
| Duplicate active folder | Prevent competing resumable run | 409 in integration test |
| Stop queued/running run | Cooperative checkpoint stop | 200; status blocked; checkpoint safe |
| Resume blocked run | Continue remaining session IDs | 200; no completed session reprocessed |
| Stop/resume completed run | Reject invalid transition | 409 |
| Unknown run | Clear not-found response | 404 |
| Progress polling | Show processed, coverage, cost, and status | Values matched SQLite after each stage |
| Run completion | Wait for Knowledge agent | Remained running until analysis saved |
| Evidence filters | Pending, verdict groups, excluded, reviewed | API stratification and counts verified |
| Confirm correct verdict | Store review only | Case moved to reviewed; KB stayed 248 |
| Correct verdict | Update score and learn exactly one rule | Integration and database tests pass |
| Client lies about `decision` | Backend derives confirm/correct from actual score | Backend ignores client label |
| Invalid verdict | Reject before database write | 400 |
| Candidate AI analysis | Draft rule and compare nearby Knowledge | Stored model, reason, comparisons, kind |
| Duplicate candidate | Close without new Knowledge | Real duplicate of rule #134; KB stayed 248 |
| Empty rule approval | Reject unsafe activation | 400 |
| New rule | Require supervisor wording and approval | Tested in isolated database |
| Update existing rule | Modify linked item without increasing count | Tested in isolated database |
| Conflict | Display existing rule and conflict notes before action | Tested in isolated database |
| Double review candidate | Prevent a second mutation | 400 |
| Knowledge search | Return only active matching items | API test and manual query verified |
| Light/dark theme | Persist choice and respect OS first use | Built bundle contains stored theme logic |

## Defects found and fixed

1. **Reviewed case remained visible as pending.** The case list was not reloaded after save and the
   API did not distinguish pending from reviewed. Added refresh revision, a reviewed filter, review
   badges, manager reason, and pending exclusion.
2. **Confirming a correct verdict duplicated Knowledge.** Confirmation copied the already selected
   reference into a new `quality-review` item. Confirmation now writes audit history only. Existing
   duplicate item 267 was deactivated while its review history was preserved.
3. **The review API trusted a client-supplied decision label.** A caller could label a changed
   verdict as confirmed. The backend now derives confirmed versus corrected from the stored score
   and the manager verdict.
4. **Run status became completed too early.** The worker marked completion before Knowledge analysis
   finished. Completion now occurs only after candidates and spend are saved.
5. **Paid writer responses could fail strict JSON parsing.** Added support for fenced JSON, content
   arrays, and a bounded second attempt with a compact schema. Invalid unstructured output is still
   rejected.
6. **Knowledge drafts had no semantic governance.** Added local nearest-rule retrieval plus an AI
   editor that classifies new, duplicate, conflict, update, or insufficient and stores audit data.
7. **No safe React controls for stop/resume.** Added API endpoints and contextual buttons with
   invalid-state protection.
8. **Frontend typography was too thin/small and had no dark mode.** Increased readable sizes,
   improved local font stacks, added focus/reduced-motion behavior, and implemented persistent dark
   mode.
9. **Generated React asset routing previously produced a white page.** Static asset routing and a
   regression test now verify the emitted JS and CSS URLs return successfully.

## Knowledge-agent result

The real runs produced one confirmed duplicate, nine insufficient-evidence drafts, and two new-rule
proposals still awaiting supervisor review. The duplicate proposed directing a branch-location
customer to the nearest branch; the agent matched it to approved rule #134 with the same required
behavior. Confirming the duplicate added zero Knowledge items.

Insufficient drafts are intentionally not activated. New proposals also remain pending until the
supervisor reviews their wording, evidence, closest rules, and any conflict notes.

## Verification commands

```powershell
npm run build
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest
```

Final result: production Vite build succeeded, Ruff succeeded, and 78 automated tests passed. The
native Chrome automation helper could not safely identify the current URL during this turn, so it
stopped before browser input. User-visible flows were therefore verified through the built React
assets, Flask integration client, live HTTP actions, workers, and direct SQLite outcome checks; no
unsafe coordinate automation was used.
