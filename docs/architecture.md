# Architecture

## Weekly flow

1. Validate the ZIP folder, roster, date range, Poppler executable, database, and AI budget.
2. For each ZIP, select the PDF whose session ID matches the ZIP stem. This is the only weekly
   conversation eligible for scoring.
3. Use the other PDF filenames to build a historical customer-thread graph. They are context and
   repeat-contact evidence, not additional weekly rows.
4. Extract embedded PDF text with `pdftotext`. The application never uses OCR.
5. Parse headers, timestamped messages, agent assignments, transfers, bot messages, and system
   events. Remove the changing `Exported from Agent Assist` footer before hashing or caching.
6. Resolve the transcript's displayed agent name against the local roster and accepted aliases.
   Low-confidence suggestions go to the alias review UI.
7. Calculate exact response and workload metrics in code.
8. Ask TypeSafe Jev bounded questions about whether the chat is understandable, the customer's
   sentiment, resolution, empathy, clarity, and inferred satisfaction.
9. If the model cannot understand the interaction or confidence is below the configured threshold,
   leave it unscored and create a clarification item. A generative model writes the concise question;
   it does not invent a score.
10. Answered clarifications become local knowledge-base entries and requeue only affected chats.
11. Aggregate agent results with sample-size and coverage information, then publish local reports.

## Coverage terminology

- **Scoring coverage** = scored / (scored + needs clarification)
- **Clarification rate** = needs clarification / (scored + needs clarification)
- **Exclusion rate** = not scorable / all processed conversations

These measures keep unknown conversations visible instead of turning missing understanding into a
false neutral score.

## AI roles

- **Decision judge:** Jev returns typed scores and probability distributions.
- **Clarification writer:** a generative model writes one specific question only when knowledge is
  missing or the interaction cannot be interpreted confidently.
- **Code verifier:** deterministic checks enforce budgets, schemas, eligibility, arithmetic, and
  database state.

The application does not permit an AI response to execute external actions.

