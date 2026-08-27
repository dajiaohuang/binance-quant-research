# Security

This repository is designed for public-market research and dry-run infrastructure. It does
not require or authorize live trading credentials.

## API keys

- Prefer unauthenticated Binance public market-data and Data Vision endpoints.
- If an explicitly preregistered local experiment requires a key, use a dedicated read-only
  key with no trading or withdrawal permissions.
- Copy `.env.example` to `.env.binance.local` and fill it only on the local machine.
- `.env`, `.env.*`, private keys, runtime databases and local logs are ignored by Git.
- Never paste credentials into issues, reports, experiment logs, screenshots or terminal
  transcripts.

If a credential is ever committed or shown publicly, revoke it immediately before attempting
to remove it from Git history.

## Reporting a vulnerability

Do not open a public issue containing an exploit or credential. Use GitHub private
vulnerability reporting when it is available; otherwise contact the repository owner without
including secrets in a public channel.
