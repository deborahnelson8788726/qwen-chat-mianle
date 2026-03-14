# Security Notes

## Secrets policy
- Never store real API keys/tokens in `public/`, `*.html`, `*.js`, or committed source code.
- Keep runtime secrets only in platform env vars (`Vercel`, `Railway`) or local `.env`.
- Use `.env.example` as placeholders only.

## Pre-commit secret scan
This repository includes a local pre-commit hook:
- Hook: `.githooks/pre-commit`
- Scanner: `scripts/scan_secrets.py`

Enable once per clone:
```bash
git config core.hooksPath .githooks
```

Manual scan before commit:
```bash
python3 scripts/scan_secrets.py
```
