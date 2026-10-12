# Methodology and Limitations

## Method summary
RadicalMap maps relationships between video metadata, hashtags, comments, and extracted text signals into a local SQLite-backed graph for trust-and-safety research.

## Current scope
- Pattern-based signal detection from curated dictionary and regex patterns.
- Local graph construction from ingested metadata.
- Optional AI-assisted analysis (`analysis/run_fable.py`) for interpretive review.

## Reproducible baseline
Use the synthetic fixture (`data/demo_fixture.json`) and loader (`python -m scraper.load_demo_fixture`) to run the project offline without platform access or private credentials.

## Limitations
- This is a methodological instrument, not a ground-truth adjudication system.
- Regex and dictionary rules can produce both false positives and false negatives.
- Network edges represent co-occurrence and linkage in sampled data, not proof of intent, affiliation, or causation.
- The public repository does not include the original development URLs/dataset.

## Evaluation status and plan
There is currently no public labelled benchmark dataset in this repository; therefore no accuracy/performance claim is made.

Recommended follow-up evaluation plan:
1. Build or obtain a labelled, governance-reviewed dataset.
2. Version the dataset and annotation guidelines.
3. Track precision/recall/F1 per category over fixed test splits.
4. Add regression fixtures for newly observed evasion patterns.
