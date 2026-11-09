# Security Policy

## No real data

This repository is a public portfolio version of the project. It contains **no
real deal data**: every pitch, company, founder and fixture is synthetic and
marked "(fictional)". There are no credentials, API keys or internal endpoints in
the code or history. `.env.example` holds placeholders only (`change-me`).

If you deploy it:

- set a long random `API_KEY` and a strong `POSTGRES_PASSWORD`;
- keep `.env` out of version control (it is git-ignored);
- run `python scripts/validate_env.py --target docker` before deploying;
- keep uploaded pitch decks in a private volume (`STORAGE_DIR`).

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
[private vulnerability reporting](../../security/advisories/new) on this
repository instead. I aim to respond within 7 days.

If you believe you have found real confidential data in this repository, report
it the same way and it will be removed promptly.
