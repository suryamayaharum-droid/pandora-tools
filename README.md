# MEAW Public Wake Beacon

Public, secret-free wake substrate for MEAW.

This repository intentionally contains **no private HIVE state, credentials, prompts, personal data, or authority roots**.

Its scheduled workflow:
1. wakes on a public GitHub-hosted runner;
2. performs bounded read-only research against public endpoints;
3. writes a hash-addressed evidence beacon into this same public repository;
4. exits.

Private MEAW components may read the public beacon, but the beacon cannot read or mutate private MEAW repositories.

## Safety / authority

- Public evidence only.
- No private-repository access.
- No external write except this repository's own beacon files.
- No arbitrary crawler.
- No LLM/API key requirement.
- No secrets committed.
- No claim of continuous cognition; this is an external periodic wake/evidence source.
