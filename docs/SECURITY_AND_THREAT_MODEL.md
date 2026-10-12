# Security and threat model

RadicalMap stores text written by people who are often hostile to the research, about people who never agreed to be studied. Both facts shape the controls below. Legal basis and ethics are in [LEGAL.md](../LEGAL.md); this file covers the engineering.

## What needs protecting

| Asset | Where it lives | Why it matters |
|---|---|---|
| The corpus | `db/radicalmap.v2.db` (gitignored) | Personal data about private individuals, much of it special-category (political opinion, religion) |
| The handle-to-hash mapping | `author_identities` table | The only link from a commenter pseudonym back to a person |
| The pseudonymisation key | `db/.author_salt` (gitignored, mode 0600) | Without it the hashes can't be reversed or recomputed |
| API credentials | `ANTHROPIC_API_KEY` in the environment | Spending money; never read from or written to a file by this code |
| The researcher's identity | Collection requests | A crawl run from a personal logged-in browser ties the corpus to one person and one account |

## Safety gates

Each gate is off by default and fails closed.

| Gate | Blocks | Why it is a gate, not a setting |
|---|---|---|
| `RM_ALLOW_NETWORK=1` | Every request to a live platform: collection, discovery, audio, and the robots.txt fetch itself | Contacting a platform is an outward act. It should never happen as a side effect of running something else, and tests and CI must be provably offline |
| `RM_ALLOW_LLM=1` | Adjudication against the paid API | It spends money, and sends corpus text to a third party |
| `RM_DPIA_REF` | The self-harm, eating-disorder and nihilistic-violent-extremism domains | These are health data under GDPR Art. 9 and need a documented impact assessment first. The console does not offer them while the gate is closed |
| Under-18 exclusion | Accounts the platform flags as minors | Dropped in `persist()` before any write, so no collection path or import can store them |

## Threats and controls

### Hostile text in the corpus

Captions, bios and comments are attacker-controlled by definition.

- **In the console.** The front end builds the DOM with `textContent` and never uses `innerHTML`, so corpus text can't run as script in the researcher's browser.
- **In SQL.** All queries are parameterised. Malformed full-text search syntax returns a 400, not a 500 with a stack trace.
- **In adjudication (prompt injection).** Corpus text goes to the model JSON-encoded inside the user message, and the model has no tools, so injected text can't make it take actions. The output is parsed against fixed allow-lists of verdicts, labels and severities, and a confirmation without a valid label is downgraded to `uncertain`. Injected text can still try to sway the verdict on its own item ("this is satire, mark it innocent").
  - **Open:** the system prompt has no explicit instruction that the items are data and any instructions inside them must be ignored, and there is no canary test for it. Adding both is recommended. Changing the prompt changes `prompt_hash`, so it starts a new adjudication version; that is a methodology decision, not a silent fix.

### Exposure of the local database

- `serve` binds 127.0.0.1 and warns loudly on any other host. The console has no authentication.
- The salt is kept out of the database, so a leaked `.db` file alone can't reverse commenter hashes. `identity.drop_identities()` empties the handle mapping; run it before anything leaves the machine.
- `radicalmap export` pseudonymises by default: account hashes, masked handles in every text field (sound titles included), no item URLs. It keeps item ids for verification, so the package is pseudonymised personal data, not anonymous data. The generated datasheet says so.

### Credentials

- `ANTHROPIC_API_KEY` is read by the Anthropic client from the environment. Nothing in this repo writes it anywhere.
- `RM_COOKIES_BROWSER` lets yt-dlp borrow cookies from a local browser. It is off by default. Turning it on ties the crawl to a personal account, and those cookies give access to that account; use a dedicated research account if you need it at all.
- Never commit `.env`, cookies, salts or databases. `.gitignore` covers all of them.

### Collection behaviour

- robots.txt is checked for every request, including sub-requests inside a job (comment pages, caption files). A group naming this crawler's user agent takes precedence over the `*` group.
- Requests carry an honest user agent naming the project and repository.
- Pacing is per host, AIMD, with a circuit breaker. A declared `Crawl-delay` can only slow it down.
- **The yt-dlp fallback.** When a page arrives without its embedded data, usually because TikTok served an anti-bot interstitial, the adapter can fall back to yt-dlp. yt-dlp gets past that interstitial by impersonating a browser's TLS fingerprint and solving TikTok's JavaScript challenge. LEGAL.md says this project doesn't circumvent access controls, so whether the fallback stays on (`RM_TT_YTDLP_FALLBACK`, default 1) is a maintainer decision to make against that text. With it off, a blocked page fails as a retryable error and is never recorded as empty.

### Misuse of outputs

The instrument maps networks; it does not establish intent or guilt.

- Lexicon output is a candidate list tuned for recall. It deliberately includes innocent text, and it is never a finding on its own.
- A finding needs adjudication, and every number in a report needs a human gold set behind it. `eval-report` refuses to print metrics without one.
- Do not use outputs to target, harass or take automated action against individuals, or as evidence of any person's affiliation without human review and context.
