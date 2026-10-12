# Changelog

## Unreleased
- Added an offline synthetic demo fixture (`data/demo_fixture.json`) and loader (`scraper/load_demo_fixture.py`) for reproducible onboarding without live scraping or credentials.
- Added initial pytest coverage for dictionary loading, pattern flagging, demo DB loading, graph building, and API endpoints.
- Added GitHub Actions test workflow (`.github/workflows/tests.yml`) for Python 3.11 and 3.12.
- Added methodology/limitations and security/threat-model documents in `docs/`.
- Updated dependency constraints and README setup/demo/testing guidance.
