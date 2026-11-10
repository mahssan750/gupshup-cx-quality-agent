# Security and data handling

- Keep transcript ZIPs, PDFs, staff rosters, SQLite files, reports, and `.env` files outside Git.
- Use an OpenRouter key dedicated to this application with a hard USD limit.
- The browser UI binds to `127.0.0.1`; do not expose it to a public network.
- Customer names are not required for scoring and should be replaced with stable local hashes.
- Do not paste production transcripts into issues, pull requests, CI logs, or public examples.
- Report security problems privately to the repository owner instead of opening a public issue.

