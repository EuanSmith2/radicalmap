# RadicalMap

![RadicalMap Banner](banner.png)

![Python](https://img.shields.io/badge/Python-3.11--3.12-131510?style=flat-square&logo=python&logoColor=d8d4c6) ![License](https://img.shields.io/badge/License-MIT-131510?style=flat-square&logoColor=d8d4c6) ![Purpose](https://img.shields.io/badge/Purpose-Research_Only-c05038?style=flat-square)
[![DOI](https://zenodo.org/badge/doi/10.5281/zenodo.22083807.svg)](https://doi.org/10.5281/zenodo.22083807)

RadicalMap is an independent trust-and-safety research instrument for detecting and visualising potentially harmful content network signals in short-form video metadata.

## Project status
- **Status:** active research prototype (portfolio-quality reproducible baseline)
- **Dataset in this repository:** synthetic demo fixture only (`data/demo_fixture.json`)
- **Real-world collection:** requires researcher-controlled workflow and legal/ethical review

## Quick architecture

```mermaid
flowchart LR
  A[Input URLs or demo fixture] --> B[Ingestion / parsing]
  B --> C[(SQLite DB)]
  C --> D[Rule-based analysis\n(patterns + dictionary)]
  C --> E[Graph builder]
  E --> F[FastAPI + vis.js frontend]
  C --> G[Optional AI pass\nAnthropic API key]
```

## Safe offline demo (no credentials, no live platform access)

```bash
git clone https://github.com/EuanSmith2/radicalmap
cd radicalmap
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m scraper.load_demo_fixture
uvicorn frontend.app:app --reload --port 5001
```

Open `http://localhost:5001`.

This path uses a synthetic fixture and does not require private tokens, cookies, or live scraping.

## Reproducible test run

```bash
. .venv/bin/activate
pytest -q
```

CI runs the same tests on Python 3.11 and 3.12 via `.github/workflows/tests.yml`.

## Live ingestion workflow (researcher-operated)

If you are running your own approved collection workflow:

```bash
python -m scraper.manual_ingest
python -m scraper.analyse_text
uvicorn frontend.app:app --reload --port 5001
```

Optional AI analysis:

```bash
export ANTHROPIC_API_KEY=...
python -m analysis.run_fable
```

## What this repository includes

- `scraper/` — ingestion and analysis scripts
- `db/` — SQLite schema and DB helpers
- `analysis/` — dictionary loading and graph construction
- `frontend/` — FastAPI app and static graph UI
- `data/demo_fixture.json` — synthetic offline demo fixture
- `docs/METHODOLOGY_AND_LIMITATIONS.md` — evaluation limits and plan
- `docs/SECURITY_AND_THREAT_MODEL.md` — security and misuse controls

## Responsible use and safety

- Use for research, policy, and educational analysis only.
- Do **not** use for harassment, targeting, or automated punitive action.
- Treat outputs as investigatory leads, not proof of intent or affiliation.
- Minimise collection, store locally, and define retention/deletion procedures.

## Legal and compliance note

This project does **not** provide legal advice and does not guarantee that any specific collection method is lawful in your jurisdiction or compliant with platform terms. Users are responsible for obtaining appropriate approvals and ensuring compliance with applicable law, terms of service, and institutional ethics requirements.

For additional context, see [LEGAL.md](LEGAL.md).

## Limitations

- No public labelled benchmark dataset is included in this repository.
- No accuracy/performance claim is made in this release.
- Rule-based detection may produce false positives/false negatives.
- Co-occurrence links do not establish causation.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Attribution

Developed by **Euan Smith** — [euansmith.net](https://euansmith.net)
