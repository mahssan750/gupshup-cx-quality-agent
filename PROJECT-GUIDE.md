# Gupshup Quality Officer — operating guide

## Weekly workflow

1. Download the weekly Gupshup ZIP files.
2. Run `C:\gubshup_quality_officer\Start-CXQuality.ps1`.
3. Sign in with the private administrator credentials stored in `.env`.
4. Import any new supervisor WhatsApp feedback under **Supervisor knowledge**.
5. Review and approve reusable company rules.
6. Create a 20–50 chat sample with a low budget.
7. Review every non-compliant verdict and clarification in **Evidence Lab**.
8. Confirm a correct verdict without writing a new rule. If the verdict is corrected, write the
   reusable company rule that justifies the correction.
9. Open **Knowledge Loop**. Review the AI proposal and its closest existing rule before choosing:
   new rule, update existing rule, confirm duplicate, or reject.
10. Run the full week only after the sample reflects supervisor judgment reliably.

## Access and sessions

All UI, API, Knowledge, and run actions require the `admin` role. Credentials stay in `.env`, the
session expires after the configured number of hours, and signing out immediately removes the
session. After five failed attempts, login is blocked for one minute from that local address.

## What creates an employee mistake

A mistake exists only when all three are present:

- an applicable approved company rule or supervisor precedent;
- customer text establishing the relevant request and context;
- employee text that directly violates the reference.

Customer reaction and style can help explain the case but cannot independently create a mistake.

## Knowledge sources used by the evaluator

- `company-rule` — manager-approved operating rules.
- `company-policy` — formal company policy.
- `company-supervisor-rule` — rules extracted from supervisor feedback.
- `quality-review` — reusable corrections written during manager review.
- approved supervisor precedents.

## Cost control

Start with `--sample-size 20 --budget 0.15 --stop-at 0.12`. The local budget guard reserves cost
before each request and refuses work that would cross the stop threshold. The default models are
configured privately in `.env`.

## Recovery

Runs checkpoint after each chat. The React control room provides **إيقاف آمن** and **استئناف** for
non-terminal runs. The run remains `running` while the Knowledge agent finishes, and changes to
`completed` only after its cost and draft analysis are stored. Local database backups are stored in
`local/backups/` and are not uploaded to GitHub.
